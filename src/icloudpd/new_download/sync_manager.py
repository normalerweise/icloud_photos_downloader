"""Main orchestration for the new download architecture."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Iterator

from .database import PhotoDatabase
from .asset_processor import AssetProcessor
from .file_manager import FileManager
from .download_manager import DownloadManager

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
        self.asset_processor = AssetProcessor(self.database)
        self.download_manager = DownloadManager(self.file_manager)
    
    def sync_photos(self, icloud_photos: Any, recent: Optional[int] = None,
                   since: Optional[datetime] = None, until_found: Optional[int] = None) -> Dict[str, Any]:
        """Main sync method to download photos from iCloud.
        
        Args:
            icloud_photos: iCloud photos collection
            recent: Only download N most recent photos
            since: Only download photos created since this date
            until_found: Stop after finding N photos
            
        Returns:
            Dictionary with sync statistics
        """
        logger.info("Starting photo sync...")
        
        # Clean up any incomplete downloads
        cleaned_count = self.file_manager.cleanup_incomplete_downloads()
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} incomplete downloads")
        
        # Process and filter assets
        logger.info("Processing iCloud assets...")
        processed_assets = list(self.asset_processor.filter_assets(
            icloud_photos, recent=recent, since=since, until_found=until_found
        ))
        
        logger.info(f"Processed {len(processed_assets)} assets")
        
        # Get assets that need downloading
        assets_to_download = self.asset_processor.get_assets_for_download(
            recent=recent, since=since, until_found=until_found
        )
        
        logger.info(f"Found {len(assets_to_download)} assets needing download")
        
        if not assets_to_download:
            logger.info("No assets need downloading")
            return self._get_sync_stats()
        
        # Download assets
        logger.info("Starting downloads...")
        download_results = self._download_assets(assets_to_download, icloud_photos)
        
        # Update database with download results
        self._update_download_status(download_results)
        
        # Get final statistics
        stats = self._get_sync_stats()
        logger.info("Sync completed")
        logger.info(f"Total assets: {stats['total_assets']}")
        logger.info(f"Downloaded assets: {stats['downloaded_assets']}")
        logger.info(f"Failed assets: {stats['failed_assets']}")
        
        return stats
    
    def _download_assets(self, assets_to_download: List[Dict[str, Any]], 
                        icloud_photos: Any) -> Dict[str, tuple]:
        """Download assets in batches.
        
        Args:
            assets_to_download: List of assets to download
            icloud_photos: iCloud photos collection
            
        Returns:
            Dictionary mapping asset_id to (downloaded_versions, failed_versions)
        """
        download_results = {}
        
        # Process assets in batches to avoid memory issues
        batch_size = 10  # Process 10 assets at a time
        
        for i in range(0, len(assets_to_download), batch_size):
            batch = assets_to_download[i:i + batch_size]
            logger.info(f"Processing batch {i//batch_size + 1}/{(len(assets_to_download) + batch_size - 1)//batch_size}")
            
            # Get corresponding iCloud assets for this batch
            batch_with_icloud_assets = []
            for asset_data in batch:
                try:
                    # Find the asset in iCloud collection
                    icloud_asset = self._find_icloud_asset(asset_data['asset_id'], icloud_photos)
                    if icloud_asset:
                        batch_with_icloud_assets.append((asset_data, icloud_asset))
                    else:
                        logger.warning(f"Could not find iCloud asset for {asset_data['asset_id']}")
                        download_results[asset_data['asset_id']] = ([], ['not_found'])
                except Exception as e:
                    logger.error(f"Error finding iCloud asset for {asset_data['asset_id']}: {e}")
                    download_results[asset_data['asset_id']] = ([], ['error'])
            
            if batch_with_icloud_assets:
                # Download this batch
                batch_results = self.download_manager.download_assets_batch(batch_with_icloud_assets)
                download_results.update(batch_results)
        
        return download_results
    
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