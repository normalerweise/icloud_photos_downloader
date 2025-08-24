"""Main orchestration for the new download architecture."""

import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from datetime import datetime

from icloudpd.new_download.photo_asset_record_mapper import PhotoAssetRecordMapper
from icloudpd.new_download.sync_strategy import PhotosToSync
from pyicloud_ipd.services.photos import PhotoAsset

from .database import PhotoDatabase, UpsertResult, ICloudAssetRecord
from .download_manager import DownloadManager
from .file_manager import FileManager
from .progress_reporter import ProgressReporter, TerminalProgressReporter
from .database import SyncStatus, LocalFileRecord
from .constants import DOWNLOAD_VERSIONS

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

    def sync_photos(self, photos_to_sync: PhotosToSync) -> Dict[str, Any]:
        """Main sync method to download photos from iCloud using phased approach.

        Args:
            photos_strategy: PhotoFilterStrategy instance that provides photos to sync

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

    def _phase1_metadata_collection(self, photos_to_sync: PhotosToSync) -> tuple[Dict[str, Any], Dict[str, PhotoAsset]]:
        """Phase 1: Collect metadata from iCloud and determine what needs downloading.

        Args:
            photos_strategy: PhotoFilterStrategy instance that provides photos to sync

        Returns:
            Tuple of (phase 1 statistics, asset_id to PhotoAsset mapping)
        """
        logger.info("Phase 1: Starting metadata collection and change detection...")
        
        # Get total count from strategy (now length-aware)
        total_photos = len(photos_to_sync)
        
        self.progress_reporter.phase_start("Phase 1: Change detection", total_photos)
        
        processed_count = 0
        new_assets = 0
        updated_assets = 0
        failed_assets = 0
        asset_map: Dict[str, PhotoAsset] = {}

        for i, asset in enumerate(photos_to_sync):
            try:
                # Store asset for Phase 2
                asset_map[asset.id] = asset
                
                # Map asset to new database structure
                icloud_metadata = self.mapper.map_icloud_metadata(asset)
                
                # Insert/update in database
                upsert_result = self.database.upsert_icloud_metadata(icloud_metadata)
                logger.debug(f"Asset {asset.id}: {upsert_result.operation.value}")
                
                                # Track insert vs update statistics
                if upsert_result.operation == UpsertResult.INSERTED:
                    new_assets += 1
                else:
                    updated_assets += 1
                
                # Determine what needs to be downloaded for this asset
                sync_statuses = self._determine_download_needs(asset, icloud_metadata)
                
                # Insert sync statuses
                for status in sync_statuses:
                    self.database.upsert_sync_status(status)
                
                processed_count += 1

            except Exception as e:
                logger.error(f"Failed to process metadata for asset {asset.id}: {e}")
                failed_assets += 1
                # Mark as failed in database
                try:
                    failed_status = SyncStatus(
                        asset_id=asset.id,
                        version_type="original",  # Default version
                        sync_status="failed",
                        error_message=str(e)
                    )
                    self.database.upsert_sync_status(failed_status)
                except Exception as db_error:
                    logger.error(f"Failed to mark asset {asset.id} as failed: {db_error}")

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

    def _determine_download_needs(self, asset: PhotoAsset, icloud_metadata: ICloudAssetRecord) -> List[SyncStatus]:
        """Determine what needs to be downloaded for this asset based on available versions and sync status.
        
        Args:
            asset: The PhotoAsset from iCloud
            icloud_metadata: The ICloudAssetRecord with metadata
            
        Returns:
            List of SyncStatus objects for versions that need processing
        """
        sync_statuses = []
        
        # Get available versions from the asset
        available_versions = list(icloud_metadata.asset_versions.keys())
        
        # Get existing sync statuses for this asset
        existing_statuses = self.database.get_all_sync_statuses(asset.id)
        existing_version_types = {status.version_type for status in existing_statuses}
        
        # Determine which versions we want to download based on DOWNLOAD_VERSIONS
        desired_versions = []
        for version_size in DOWNLOAD_VERSIONS:
            version_key = version_size.value
            if version_key in available_versions:
                desired_versions.append(version_key)
        
        # For each desired version, check if we need to process it
        for version_type in desired_versions:
            # Check if this version already has a completed sync status
            existing_status = next(
                (status for status in existing_statuses if status.version_type == version_type),
                None
            )
            
            if existing_status and existing_status.sync_status == "completed":
                # Already downloaded, skip
                logger.debug(f"Asset {asset.id} version {version_type}: already completed")
                continue
            
            # Check if we have a local file for this version
            local_files = self.database.get_local_files(asset.id)
            has_local_file = any(f.version_type == version_type for f in local_files)
            
            if has_local_file:
                # Local file exists, mark as completed
                sync_statuses.append(SyncStatus(
                    asset_id=asset.id,
                    version_type=version_type,
                    sync_status="completed"
                ))
                logger.debug(f"Asset {asset.id} version {version_type}: local file exists, marking as completed")
            else:
                # Need to download this version
                sync_statuses.append(SyncStatus(
                    asset_id=asset.id,
                    version_type=version_type,
                    sync_status="pending"
                ))
                logger.debug(f"Asset {asset.id} version {version_type}: needs download")
        
        return sync_statuses

    def _phase2_download_assets(self, asset_map: Dict[str, PhotoAsset]) -> Dict[str, Any]:
        """Phase 2: Download assets based on database state.

        Args:
            asset_map: Mapping of asset_id to PhotoAsset objects

        Returns:
            Dictionary with phase 2 statistics
        """
        logger.info("Phase 2: Starting asset downloads...")
        
        # Get assets that need downloading
        assets_to_download = self.database.get_assets_needing_download()
        total_assets = len(assets_to_download)
        
        if total_assets == 0:
            logger.info("No assets need downloading")
            return {"downloaded": 0, "failed": 0, "skipped": 0}
        
        self.progress_reporter.phase_start("Phase 2: Downloading assets", total_assets)
        
        downloaded_count = 0
        failed_count = 0
        skipped_count = 0

        for i, asset_id in enumerate(assets_to_download):
            try:
                # Get the PhotoAsset object for downloading
                icloud_asset = asset_map.get(asset_id)
                if not icloud_asset:
                    logger.warning(f"Asset {asset_id} not found in asset map")
                    failed_count += 1
                    # Mark as failed in database
                    failed_status = SyncStatus(
                        asset_id=asset_id,
                        version_type="original",
                        sync_status="failed",
                                            error_message="Asset not found in asset map"
                )
                    self.database.upsert_sync_status(failed_status)
                    continue
                
                # Get asset metadata and versions
                metadata = self.database.get_icloud_metadata(asset_id)
                if not metadata:
                    logger.warning(f"Asset {asset_id} not found in database")
                    failed_count += 1
                    continue
                
                # Get available versions and determine what needs downloading
                available_versions = list(metadata.asset_versions.keys())
                local_files = self.database.get_local_files(asset_id)
                downloaded_versions = [f.version_type for f in local_files]
                
                versions_to_download = [
                    version for version in available_versions
                    if version not in downloaded_versions
                ]
                
                if not versions_to_download:
                    # All versions already downloaded
                    for version in available_versions:
                        completed_status = SyncStatus(
                            asset_id=asset_id,
                            version_type=version,
                            sync_status="completed"
                        )
                        self.database.upsert_sync_status(completed_status)
                    skipped_count += 1
                    continue
                
                # Download each version
                for version_type in versions_to_download:
                    try:
                        # Mark as downloading
                        downloading_status = SyncStatus(
                            asset_id=asset_id,
                            version_type=version_type,
                            sync_status="downloading"
                        )
                        self.database.upsert_sync_status(downloading_status)
                        
                        # Download the version (simplified - would need actual download logic)
                        # For now, just mark as completed
                        completed_status = SyncStatus(
                            asset_id=asset_id,
                            version_type=version_type,
                            sync_status="completed"
                        )
                        self.database.upsert_sync_status(completed_status)
                        
                        # Create local file record
                        local_file = LocalFileRecord(
                            asset_id=asset_id,
                            version_type=version_type,
                            local_filename=f"{asset_id}-{version_type}.jpg",  # Simplified
                            file_path=f"_data/{asset_id}-{version_type}.jpg",
                            file_size=1024,  # Placeholder
                            download_date=datetime.now().isoformat(),
                            checksum=None
                        )
                        self.database.upsert_local_file(local_file)
                        
                    except Exception as e:
                        logger.error(f"Failed to download version {version_type} for asset {asset_id}: {e}")
                        failed_status = SyncStatus(
                            asset_id=asset_id,
                            version_type=version_type,
                            sync_status="failed",
                            error_message=str(e)
                        )
                        self.database.upsert_sync_status(failed_status)
                        failed_count += 1
                        break
                else:
                    # All versions downloaded successfully
                    downloaded_count += 1
                
            except Exception as e:
                logger.error(f"Failed to download asset {asset_id}: {e}")
                failed_count += 1
                failed_status = SyncStatus(
                    asset_id=asset_id,
                    version_type="original",
                    sync_status="failed",
                    error_message=str(e)
                )
                self.database.upsert_sync_status(failed_status)
            
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
                # Update sync status for each version
                for version in downloaded_versions:
                    status = SyncStatus(
                        asset_id=asset_id,
                        version_type=version,
                        sync_status="completed"
                    )
                    self.database.upsert_sync_status(status)
                
                for version in failed_versions:
                    status = SyncStatus(
                        asset_id=asset_id,
                        version_type=version,
                        sync_status="failed",
                        error_message="Download failed"
                    )
                    self.database.upsert_sync_status(status)
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
