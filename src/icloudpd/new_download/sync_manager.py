"""Main orchestration for the new download architecture."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Iterator

from icloudpd.new_download.photo_asset_record_mapper import PhotoAssetRecordMapper

from .database import PhotoDatabase, PhotoAssetRecord
from .file_manager import FileManager
from .download_manager import DownloadManager

from pyicloud_ipd.services.photos import  PhotoAsset

logger = logging.getLogger(__name__)

class SyncManager:
    """Main orchestration class for downloading iCloud photos."""
    
    def __init__(self, base_directory: Path):
        """Initialize sync manager.
        
        Args:
            base_directory: Base directory for downloads
        """
        self.base_directory = Path(base_directory)
        
        # Initialize components
        self.database = PhotoDatabase(self.base_directory)
        self.file_manager = FileManager(self.base_directory)
        self.mapper = PhotoAssetRecordMapper()
        self.download_manager = DownloadManager(self.file_manager)
    
    def sync_photos(self, photos_to_sync: Iterator[PhotoAsset]) -> Dict[str, Any]:
        """Main sync method to download photos from iCloud.
        
        Args:
            photos_to_sync: Iterator of PhotoAsset objects to check/download (already filtered)
            
        Returns:
            Dictionary with sync statistics
        """
        logger.info("Starting photo sync...")
        
        # Clean up any incomplete downloads
        cleaned_count = self.file_manager.cleanup_incomplete_downloads()
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} incomplete downloads")
        
        # Process and download assets in the same pass
        logger.info("Processing and downloading iCloud assets...")
        download_results = {}
        processed_count = 0
        found_count = 0
        
        for asset in photos_to_sync:
            processed_count += 1
            
            # Check if we already have this asset in database
            asset_record = self.database.get_asset(asset.id)
            if asset_record:
                asset_record = PhotoAssetRecordMapper.merge(asset_record, asset)
            else:
                asset_record = PhotoAssetRecordMapper.map(asset)

            # Insert/update in database
            self.database.insert_asset(asset_record)
            
            # TODO: logic is broken -> list_downloaded_files should not be aware of DOWNLOAD_VERSIONS this is part of versions to download determination
            # Check if asset needs downloading
            existing_versions = self.file_manager.list_downloaded_files(asset_record.asset_id)
            available_versions = asset_record.available_versions
            from icloudpd.new_download.constants import DOWNLOAD_VERSIONS
            print(f"[DEBUG] asset_id={asset_record.asset_id} available_versions={available_versions} existing_versions={existing_versions}")
            versions_to_download = [
                v for v in DOWNLOAD_VERSIONS
                if v.value in available_versions and v not in existing_versions
            ]
            print(f"[DEBUG] asset_id={asset_record.asset_id} versions_to_download={versions_to_download}")
            
            if versions_to_download:
                # Download the asset
                downloaded_versions, failed_versions = self.download_manager.download_asset_versions(asset_record, asset, versions_to_download)
                download_results[asset_record.asset_id] = (downloaded_versions, failed_versions)
                
                # Update database with download results
                self.database.update_download_status(asset_record.asset_id, downloaded_versions, failed_versions)
            
            found_count += 1
        
        logger.info(f"Processed {processed_count} assets, found {found_count} assets")
        
        # Get final statistics
        stats = self._get_sync_stats()
        logger.info("Sync completed")
        logger.info(f"Total assets: {stats['total_assets']}")
        logger.info(f"Downloaded assets: {stats['downloaded_assets']}")
        logger.info(f"Failed assets: {stats['failed_assets']}")
        
        return stats
    
 
    
    def _find_icloud_asset(self, asset_id: str, icloud_photos: Any) -> Optional[Any]:
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
                if hasattr(asset, 'id') and asset.id == asset_id:
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
            'total_assets': total_assets,
            'downloaded_assets': downloaded_assets,
            'failed_assets': failed_assets,
            'disk_usage_bytes': disk_usage,
            'disk_usage_mb': disk_usage / (1024 * 1024),
            'disk_usage_gb': disk_usage / (1024 * 1024 * 1024)
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
            if hasattr(icloud_photos, 'albums'):
                for album_name, album in icloud_photos.albums.items():
                    album_info = {
                        'name': album_name,
                        'count': len(album) if hasattr(album, '__len__') else 0
                    }
                    albums.append(album_info)
        except Exception as e:
            logger.error(f"Error listing albums: {e}")
        
        return albums
    
    def cleanup(self) -> None:
        """Clean up resources."""
        self.download_manager.cleanup() 