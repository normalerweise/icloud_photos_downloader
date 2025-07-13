"""Main orchestration for the new download architecture."""

import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from icloudpd.new_download.photo_asset_record_mapper import PhotoAssetRecordMapper
from pyicloud_ipd.services.photos import PhotoAsset

from .database import PhotoDatabase
from .download_manager import DownloadManager
from .file_manager import FileManager
from .progress_reporter import ProgressReporter, TerminalProgressReporter

logger = logging.getLogger(__name__)


class SyncManager:
    """Main orchestration class for downloading iCloud photos."""

    def __init__(self, base_directory: Path, progress_reporter: Optional[ProgressReporter] = None):
        """Initialize sync manager.

        Args:
            base_directory: Base directory for downloads
            progress_reporter: Optional progress reporter for tracking sync progress
        """
        self.base_directory = Path(base_directory)
        self.progress_reporter = progress_reporter or TerminalProgressReporter()

        # Initialize components
        self.database = PhotoDatabase(self.base_directory)
        self.file_manager = FileManager(self.base_directory)
        self.mapper = PhotoAssetRecordMapper()
        self.download_manager = DownloadManager(self.file_manager)

    def sync_photos(self, photos_to_sync: Iterator[PhotoAsset]) -> Dict[str, Any]:
        """Main sync method to download photos from iCloud using phased approach.

        Args:
            photos_to_sync: Iterator of PhotoAsset objects to check/download (already filtered)

        Returns:
            Dictionary with sync statistics
        """
        logger.info("Starting photo sync with phased approach...")

        # Clean up any incomplete downloads
        cleaned_count = self.file_manager.cleanup_incomplete_downloads()
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} incomplete downloads")

        # Phase 1: Metadata collection and change detection
        phase1_stats, asset_map = self._phase1_metadata_collection(photos_to_sync)
        
        # Phase 2: Download files based on database state
        phase2_stats = self._phase2_download_assets(asset_map)

        # Get final statistics
        final_stats = self._get_sync_stats()
        
        # Report completion
        self.progress_reporter.sync_complete(final_stats)
        
        return final_stats

    def _phase1_metadata_collection(self, photos_to_sync: Iterator[PhotoAsset]) -> tuple[Dict[str, Any], Dict[str, PhotoAsset]]:
        """Phase 1: Collect metadata from iCloud and determine what needs downloading.

        Args:
            photos_to_sync: Iterator of PhotoAsset objects from iCloud

        Returns:
            Tuple of (phase 1 statistics, asset_id to PhotoAsset mapping)
        """
        logger.info("Phase 1: Starting metadata collection and change detection...")
        
        # Convert iterator to list for progress tracking
        photos_list = list(photos_to_sync)
        total_photos = len(photos_list)
        
        self.progress_reporter.phase_start("Phase 1: Change detection", total_photos)
        
        processed_count = 0
        new_assets = 0
        updated_assets = 0
        failed_assets = 0
        asset_map: Dict[str, PhotoAsset] = {}

        for i, asset in enumerate(photos_list):
            try:
                # Store asset for Phase 2
                asset_map[asset.id] = asset
                
                # Check if we already have this asset in database
                asset_record = self.database.get_asset(asset.id)
                
                if asset_record:
                    # Update existing asset
                    asset_record = PhotoAssetRecordMapper.merge(asset_record, asset)
                    asset_record.sync_status = "metadata_processed"
                    updated_assets += 1
                else:
                    # New asset
                    asset_record = PhotoAssetRecordMapper.map(asset)
                    asset_record.sync_status = "metadata_processed"
                    new_assets += 1

                # Insert/update in database
                self.database.insert_asset(asset_record)
                processed_count += 1

            except Exception as e:
                logger.error(f"Failed to process metadata for asset {asset.id}: {e}")
                failed_assets += 1
                # Mark as failed in database
                if asset_record:
                    asset_record.sync_status = "failed"
                    self.database.insert_asset(asset_record)

            # Report progress
            self.progress_reporter.phase_progress(i + 1, total_photos)

        # Report phase completion
        phase1_stats = {
            "processed": processed_count,
            "new_assets": new_assets,
            "updated_assets": updated_assets,
            "failed_assets": failed_assets,
        }
        
        self.progress_reporter.phase_complete("Phase 1: Change detection", phase1_stats)
        logger.info(f"Phase 1 completed: {processed_count} assets processed")
        
        return phase1_stats, asset_map

    def _phase2_download_assets(self, asset_map: Dict[str, PhotoAsset]) -> Dict[str, Any]:
        """Phase 2: Download assets based on database state.

        Args:
            asset_map: Mapping of asset_id to PhotoAsset objects

        Returns:
            Dictionary with phase 2 statistics
        """
        logger.info("Phase 2: Starting asset downloads...")
        
        # Get assets that need downloading
        assets_to_download = self.database.get_assets_needing_download_phase2()
        total_assets = len(assets_to_download)
        
        if total_assets == 0:
            logger.info("No assets need downloading")
            return {"downloaded": 0, "failed": 0, "skipped": 0}
        
        self.progress_reporter.phase_start("Phase 2: Downloading assets", total_assets)
        
        downloaded_count = 0
        failed_count = 0
        skipped_count = 0

        for i, asset_record in enumerate(assets_to_download):
            try:
                # Mark as downloading
                self.database.update_sync_status(asset_record.asset_id, "downloading")
                
                # Get the PhotoAsset object for downloading
                icloud_asset = asset_map.get(asset_record.asset_id)
                if not icloud_asset:
                    logger.warning(f"Asset {asset_record.asset_id} not found in asset map")
                    failed_count += 1
                    self.database.update_sync_status(asset_record.asset_id, "failed")
                    continue
                
                # Determine which versions need downloading
                from icloudpd.new_download.constants import DOWNLOAD_VERSIONS
                
                versions_to_download = [
                    v for v in DOWNLOAD_VERSIONS
                    if v.value in asset_record.available_versions 
                    and v.value not in asset_record.downloaded_versions
                ]
                
                if not versions_to_download:
                    # All versions already downloaded
                    self.database.update_sync_status(asset_record.asset_id, "completed")
                    skipped_count += 1
                    continue
                
                # Download the asset
                downloaded_versions, failed_versions = (
                    self.download_manager.download_asset_versions(
                        asset_record, icloud_asset, versions_to_download
                    )
                )
                
                # Update database with download results
                self.database.update_download_status(
                    asset_record.asset_id, downloaded_versions, failed_versions
                )
                
                if failed_versions:
                    self.database.update_sync_status(asset_record.asset_id, "failed")
                    failed_count += 1
                else:
                    self.database.update_sync_status(asset_record.asset_id, "completed")
                    downloaded_count += 1
                
            except Exception as e:
                logger.error(f"Failed to download asset {asset_record.asset_id}: {e}")
                failed_count += 1
                self.database.update_sync_status(asset_record.asset_id, "failed")
            
            # Report progress
            self.progress_reporter.phase_progress(i + 1, total_assets)

        # Report phase completion
        phase2_stats = {
            "downloaded": downloaded_count,
            "failed": failed_count,
            "skipped": skipped_count,
        }
        
        self.progress_reporter.phase_complete("Phase 2: Downloading assets", phase2_stats)
        logger.info(f"Phase 2 completed: {downloaded_count} downloaded, {failed_count} failed, {skipped_count} skipped")
        
        return phase2_stats

    def _find_icloud_asset(self, asset_id: str, icloud_photos: Any) -> Any | None:
        """Find an asset in the iCloud photos collection.

        Args:
            asset_id: iCloud asset ID
            icloud_photos: iCloud photos collection

        Returns:
            iCloud asset object or None if not found
        """
        try:
            # Try to find by ID
            for asset in icloud_photos:
                if hasattr(asset, "id") and asset.id == asset_id:
                    return asset
        except Exception as e:
            logger.error(f"Error searching for asset {asset_id}: {e}")

        return None

    def _update_download_status(self, download_results: Dict[str, tuple]) -> None:
        """Update database with download results.

        Args:
            download_results: Dictionary mapping asset_id to (downloaded_versions, failed_versions)
        """
        for asset_id, (downloaded_versions, failed_versions) in download_results.items():
            try:
                self.database.update_download_status(asset_id, downloaded_versions, failed_versions)
            except Exception as e:
                logger.error(f"Failed to update download status for asset {asset_id}: {e}")

    def _get_sync_stats(self) -> Dict[str, Any]:
        """Get synchronization statistics.

        Returns:
            Dictionary with sync statistics
        """
        total_assets = self.database.get_asset_count()
        downloaded_assets = self.database.get_downloaded_count()
        failed_assets = total_assets - downloaded_assets

        disk_usage = self.file_manager.get_disk_usage()

        return {
            "total_assets": total_assets,
            "downloaded_assets": downloaded_assets,
            "failed_assets": failed_assets,
            "disk_usage_bytes": disk_usage,
            "disk_usage_mb": disk_usage / (1024 * 1024),
            "disk_usage_gb": disk_usage / (1024 * 1024 * 1024),
        }

    def list_albums(self, icloud_photos: Any) -> List[Dict[str, Any]]:
        """List available albums.

        Args:
            icloud_photos: iCloud photos collection

        Returns:
            List of album information
        """
        albums = []

        try:
            # Get albums from iCloud photos
            if hasattr(icloud_photos, "albums"):
                for album_name, album in icloud_photos.albums.items():
                    album_info = {
                        "name": album_name,
                        "count": len(album) if hasattr(album, "__len__") else 0,
                    }
                    albums.append(album_info)
        except Exception as e:
            logger.error(f"Error listing albums: {e}")

        return albums

    def cleanup(self) -> None:
        """Clean up resources."""
        self.download_manager.cleanup()
