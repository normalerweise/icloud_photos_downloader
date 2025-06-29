"""Asset processing and filtering logic."""

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Iterator
from pathlib import Path

from .constants import DOWNLOAD_VERSIONS
from .database import PhotoDatabase

logger = logging.getLogger(__name__)


class AssetProcessor:
    """Process and filter iCloud photo assets."""
    
    def __init__(self, database: PhotoDatabase):
        """Initialize asset processor.
        
        Args:
            database: Database instance for tracking assets
        """
        self.database = database
    
    def process_asset(self, asset: Any) -> Dict[str, Any]:
        """Process a single iCloud asset and extract relevant information.
        
        Args:
            asset: iCloud asset object from pyicloud_ipd
            
        Returns:
            Dictionary containing processed asset data
        """
        # Extract basic asset information
        asset_data = {
            'asset_id': asset.id,
            'filename': asset.filename,
            'asset_type': self._determine_asset_type(asset),
            'created_date': asset.created,
            'added_date': asset.added,
            'width': asset.dimensions[0] if asset.dimensions else None,
            'height': asset.dimensions[1] if asset.dimensions else None,
            'location_latitude': asset.location.lat if asset.location else None,
            'location_longitude': asset.location.lng if asset.location else None,
            'location_altitude': asset.location.altitude if asset.location else None,
            'available_versions': self._get_available_versions(asset),
            'downloaded_versions': [],
            'failed_versions': [],
            'master_record': self._extract_master_record(asset),
            'asset_record': self._extract_asset_record(asset)
        }
        
        return asset_data
    
    def _determine_asset_type(self, asset: Any) -> str:
        """Determine if asset is photo or video.
        
        Args:
            asset: iCloud asset object
            
        Returns:
            'photo' or 'video'
        """
        # Check if it's a video based on asset properties
        if hasattr(asset, 'type') and asset.type:
            if isinstance(asset.type, str) and 'video' in asset.type.lower():
                return 'video'
        
        # Check filename extension
        if asset.filename:
            video_extensions = ['.mov', '.mp4', '.avi', '.m4v']
            if any(asset.filename.lower().endswith(ext) for ext in video_extensions):
                return 'video'
        
        return 'photo'
    
    def _get_available_versions(self, asset: Any) -> List[str]:
        """Get list of available versions for download.
        
        Args:
            asset: iCloud asset object
            
        Returns:
            List of available version types
        """
        available_versions = []
        
        # Check for original version
        if hasattr(asset, 'versions') and asset.versions:
            for version in asset.versions:
                if version.type in DOWNLOAD_VERSIONS:
                    available_versions.append(version.type)
        
        # If no versions found, assume original is available
        if not available_versions:
            available_versions.append('original')
        
        return available_versions
    
    def _extract_master_record(self, asset: Any) -> Dict[str, Any]:
        """Extract master record data from asset.
        
        Args:
            asset: iCloud asset object
            
        Returns:
            Master record dictionary
        """
        master_record = {}
        
        if hasattr(asset, 'master_record'):
            # Convert to dict if it's an object
            if hasattr(asset.master_record, '__dict__'):
                master_record = asset.master_record.__dict__.copy()
            elif isinstance(asset.master_record, dict):
                master_record = asset.master_record.copy()
        
        return master_record
    
    def _extract_asset_record(self, asset: Any) -> Dict[str, Any]:
        """Extract asset record data from asset.
        
        Args:
            asset: iCloud asset object
            
        Returns:
            Asset record dictionary
        """
        asset_record = {}
        
        if hasattr(asset, 'asset_record'):
            # Convert to dict if it's an object
            if hasattr(asset.asset_record, '__dict__'):
                asset_record = asset.asset_record.__dict__.copy()
            elif isinstance(asset.asset_record, dict):
                asset_record = asset.asset_record.copy()
        
        return asset_record
    
    def filter_assets(self, assets: Iterator[Any], recent: Optional[int] = None, 
                     since: Optional[datetime] = None, until_found: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Filter assets based on criteria.
        
        Args:
            assets: Iterator of iCloud assets
            recent: Only process N most recent assets
            since: Only process assets created since this date
            until_found: Stop after finding N assets
            
        Yields:
            Processed asset dictionaries
        """
        processed_count = 0
        found_count = 0
        
        for asset in assets:
            processed_count += 1
            
            # Apply date filter
            if since and asset.created and asset.created < since:
                continue
            
            # Process the asset
            asset_data = self.process_asset(asset)
            
            # Check if we already have this asset in database
            existing_asset = self.database.get_asset(asset_data['asset_id'])
            if existing_asset:
                # Update with latest information
                asset_data['downloaded_versions'] = existing_asset.get('downloaded_versions', [])
                asset_data['failed_versions'] = existing_asset.get('failed_versions', [])
            
            # Insert/update in database
            self.database.insert_asset(asset_data)
            
            found_count += 1
            yield asset_data
            
            # Check until_found limit
            if until_found and found_count >= until_found:
                logger.info(f"Stopping after finding {until_found} assets")
                break
            
            # Check recent limit
            if recent and processed_count >= recent:
                logger.info(f"Stopping after processing {recent} most recent assets")
                break
    
    def get_assets_for_download(self, recent: Optional[int] = None, 
                               since: Optional[datetime] = None, 
                               until_found: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get assets that need downloading, applying filters.
        
        Args:
            recent: Only consider N most recent assets
            since: Only consider assets created since this date
            until_found: Stop after finding N assets
            
        Returns:
            List of assets that need downloading
        """
        # Get all assets needing download from database
        assets_needing_download = self.database.get_assets_needing_download()
        
        # Apply filters
        filtered_assets = []
        
        for asset in assets_needing_download:
            # Apply date filter
            if since and asset.get('created_date'):
                try:
                    created_date = datetime.fromisoformat(asset['created_date'])
                    if created_date < since:
                        continue
                except (ValueError, TypeError):
                    # Skip if date parsing fails
                    continue
            
            filtered_assets.append(asset)
            
            # Check until_found limit
            if until_found and len(filtered_assets) >= until_found:
                break
        
        # Apply recent limit (sort by created_date descending)
        if recent:
            filtered_assets.sort(
                key=lambda x: x.get('created_date', ''), 
                reverse=True
            )
            filtered_assets = filtered_assets[:recent]
        
        return filtered_assets 