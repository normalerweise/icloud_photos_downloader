"""Parallel download management with retry logic."""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

import requests

from .constants import MAX_CONCURRENT_DOWNLOADS, DOWNLOAD_TIMEOUT, RETRY_ATTEMPTS, RETRY_DELAY
from .file_manager import FileManager

logger = logging.getLogger(__name__)


class DownloadManager:
    """Manage parallel downloads with retry logic."""
    
    def __init__(self, file_manager: FileManager):
        """Initialize download manager.
        
        Args:
            file_manager: File manager instance
        """
        self.file_manager = file_manager
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'icloudpd/1.0'
        })
    
    def download_asset_versions(self, asset: Dict[str, Any], icloud_asset: Any) -> Tuple[List[str], List[str]]:
        """Download all available versions for an asset.
        
        Args:
            asset: Asset data from database
            icloud_asset: iCloud asset object
            
        Returns:
            Tuple of (downloaded_versions, failed_versions)
        """
        asset_id = asset['asset_id']
        available_versions = asset.get('available_versions', [])
        original_filename = asset['filename']
        
        downloaded_versions = []
        failed_versions = []
        
        # Check what's already downloaded
        existing_versions = self.file_manager.list_downloaded_files(asset_id)
        
        # Determine what needs to be downloaded
        versions_to_download = [v for v in available_versions if v not in existing_versions]
        
        if not versions_to_download:
            logger.debug(f"All versions already downloaded for asset {asset_id}")
            return existing_versions, failed_versions
        
        logger.info(f"Downloading {len(versions_to_download)} versions for asset {asset_id}")
        
        # Download versions in parallel
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS) as executor:
            # Submit download tasks
            future_to_version = {}
            for version in versions_to_download:
                future = executor.submit(
                    self._download_single_version,
                    asset_id, version, original_filename, icloud_asset
                )
                future_to_version[future] = version
            
            # Collect results
            for future in as_completed(future_to_version):
                version = future_to_version[future]
                try:
                    success = future.result()
                    if success:
                        downloaded_versions.append(version)
                    else:
                        failed_versions.append(version)
                except Exception as e:
                    logger.error(f"Download failed for asset {asset_id} version {version}: {e}")
                    failed_versions.append(version)
        
        # Combine with existing versions
        all_downloaded = list(set(existing_versions + downloaded_versions))
        
        return all_downloaded, failed_versions
    
    def _download_single_version(self, asset_id: str, version: str, original_filename: str, 
                                icloud_asset: Any) -> bool:
        """Download a single version of an asset.
        
        Args:
            asset_id: iCloud asset ID
            version: Version type to download
            original_filename: Original filename from iCloud
            icloud_asset: iCloud asset object
            
        Returns:
            True if successful, False otherwise
        """
        for attempt in range(RETRY_ATTEMPTS):
            try:
                # Get download URL for this version
                download_url = self._get_download_url(icloud_asset, version)
                if not download_url:
                    logger.error(f"No download URL found for asset {asset_id} version {version}")
                    return False
                
                # Download the file
                success = self._download_from_url(download_url, asset_id, version, original_filename)
                if success:
                    logger.debug(f"Successfully downloaded {version} for asset {asset_id}")
                    return True
                
                # If we get here, download failed
                if attempt < RETRY_ATTEMPTS - 1:
                    logger.warning(f"Download failed for asset {asset_id} version {version}, attempt {attempt + 1}/{RETRY_ATTEMPTS}")
                    time.sleep(RETRY_DELAY * (2 ** attempt))  # Exponential backoff
                
            except Exception as e:
                logger.error(f"Download error for asset {asset_id} version {version}, attempt {attempt + 1}: {e}")
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY * (2 ** attempt))
        
        logger.error(f"Failed to download {version} for asset {asset_id} after {RETRY_ATTEMPTS} attempts")
        return False
    
    def _get_download_url(self, icloud_asset: Any, version: str) -> Optional[str]:
        """Get download URL for a specific version.
        
        Args:
            icloud_asset: iCloud asset object
            version: Version type (string like "original", "adjusted", etc.)
            
        Returns:
            Download URL or None if not found
        """
        try:
            # Try to get the specific version from asset.versions
            if hasattr(icloud_asset, 'versions') and icloud_asset.versions:
                # Convert version string to AssetVersionSize enum
                from pyicloud_ipd.version_size import AssetVersionSize
                version_map = {
                    'original': AssetVersionSize.ORIGINAL,
                    'adjusted': AssetVersionSize.ADJUSTED,
                    'alternative': AssetVersionSize.ALTERNATIVE,
                    'medium': AssetVersionSize.MEDIUM,
                    'thumb': AssetVersionSize.THUMB,
                }
                
                if version in version_map:
                    version_enum = version_map[version]
                    if version_enum in icloud_asset.versions:
                        return icloud_asset.versions[version_enum].url
            
            # Fallback to main asset URL for original version
            if version == 'original' and hasattr(icloud_asset, 'url'):
                return icloud_asset.url
            
            # Try to get URL from asset properties
            if hasattr(icloud_asset, 'download_url'):
                return icloud_asset.download_url
            
        except Exception as e:
            logger.error(f"Error getting download URL for version {version}: {e}")
        
        return None
    
    def _download_from_url(self, url: str, asset_id: str, version: str, original_filename: str) -> bool:
        """Download file from URL.
        
        Args:
            url: Download URL
            asset_id: iCloud asset ID
            version: Version type
            original_filename: Original filename from iCloud
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Make request with timeout
            response = self.session.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
            response.raise_for_status()
            
            # Save file from stream
            success = self.file_manager.save_file_from_stream(
                asset_id, version, original_filename, response.raw
            )
            
            return success
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for asset {asset_id} version {version}: {e}")
            return False
        except Exception as e:
            logger.error(f"Download failed for asset {asset_id} version {version}: {e}")
            return False
    
    def download_assets_batch(self, assets: List[Tuple[Dict[str, Any], Any]]) -> Dict[str, Tuple[List[str], List[str]]]:
        """Download a batch of assets in parallel.
        
        Args:
            assets: List of (asset_data, icloud_asset) tuples
            
        Returns:
            Dictionary mapping asset_id to (downloaded_versions, failed_versions)
        """
        results = {}
        
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS) as executor:
            # Submit download tasks for each asset
            future_to_asset = {}
            for asset_data, icloud_asset in assets:
                future = executor.submit(
                    self.download_asset_versions,
                    asset_data, icloud_asset
                )
                future_to_asset[future] = asset_data['asset_id']
            
            # Collect results
            for future in as_completed(future_to_asset):
                asset_id = future_to_asset[future]
                try:
                    downloaded_versions, failed_versions = future.result()
                    results[asset_id] = (downloaded_versions, failed_versions)
                except Exception as e:
                    logger.error(f"Batch download failed for asset {asset_id}: {e}")
                    results[asset_id] = ([], ['all'])
        
        return results
    
    def cleanup(self) -> None:
        """Clean up resources."""
        self.session.close() 