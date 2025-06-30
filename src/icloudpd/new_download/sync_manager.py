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
        
        # Process and download assets in the same pass
        logger.info("Processing and downloading iCloud assets...")
        download_results = {}
        processed_count = 0
        found_count = 0
        
        for asset in icloud_photos:
            processed_count += 1
            
            # Apply date filter
            if since and asset.created and asset.created < since:
                continue
            
            # Process the asset
            asset_data = self.asset_processor.process_asset(asset)
            
            # Check if we already have this asset in database
            existing_asset = self.database.get_asset(asset_data['asset_id'])
            if existing_asset:
                # Update with latest information
                asset_data['downloaded_versions'] = existing_asset.get('downloaded_versions', [])
                asset_data['failed_versions'] = existing_asset.get('failed_versions', [])
            
            # Insert/update in database
            self.database.insert_asset(asset_data)
            
            # Check if asset needs downloading
            existing_versions = self.file_manager.list_downloaded_files(asset_data['asset_id'])
            available_versions = asset_data.get('available_versions', [])
            versions_to_download = [v for v in available_versions if v not in existing_versions]
            
            if versions_to_download:
                # Download the asset
                downloaded_versions, failed_versions = self.download_manager.download_asset_versions(asset_data, asset)
                download_results[asset_data['asset_id']] = (downloaded_versions, failed_versions)
                
                # Update database with download results
                self.database.update_download_status(asset_data['asset_id'], downloaded_versions, failed_versions)
            
            found_count += 1
            
            # Check until_found limit
            if until_found and found_count >= until_found:
                logger.info(f"Stopping after finding {until_found} assets")
                break
            
            # Check recent limit
            if recent and processed_count >= recent:
                logger.info(f"Stopping after processing {recent} most recent assets")
                break
        
        logger.info(f"Processed {processed_count} assets, found {found_count} assets")
        
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