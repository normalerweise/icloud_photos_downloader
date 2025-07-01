"""Parallel download management with retry logic."""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

import requests

from icloudpd.new_download.database import PhotoAssetRecord
from pyicloud_ipd.services.photos import PhotoAsset
from pyicloud_ipd.version_size import VersionSize

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
    
    def download_asset_versions(
        self,
        asset: PhotoAssetRecord,
        icloud_asset: PhotoAsset,
        versions_to_download: List[VersionSize]
    ) -> Tuple[List[str], List[str]]:
        """Download all available versions for an asset.

        Args:
            asset: PhotoAssetRecord from database
            icloud_asset: PhotoAsset object from iCloud

        Returns:
            Tuple of (downloaded_versions, failed_versions)
        """
        asset_id: str = asset.asset_id

        if not versions_to_download:
            logger.debug(f"All versions already downloaded for asset {asset_id}")
            return [], []

        logger.info(f"Downloading {len(versions_to_download)} versions for asset {asset_id}")

        # Download versions in parallel
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS) as executor:
            # Submit download tasks
            future_to_version: Dict[Any, VersionSize] = {}
            for version in versions_to_download:
                future = executor.submit(
                    self._download_single_version,
                    version, icloud_asset
                )
                future_to_version[future] = version

            # Collect results
            downloaded_versions: List[str] = []
            failed_versions: List[str] = []
            for future in as_completed(future_to_version):
                version = future_to_version[future]
                try:
                    success = future.result()
                    if success:
                        downloaded_versions.append(version.value)
                    else:
                        failed_versions.append(version.value)
                except Exception as e:
                    logger.error(f"Download failed for asset {asset_id} version {version}: {e}")
                    failed_versions.append(version.value)

        return downloaded_versions, failed_versions

    def _download_single_version(self, version: VersionSize ,
                                icloud_asset: PhotoAsset) -> bool:
        """Download a single version of an asset.
        
        Args:
            version: Version type to download
            icloud_asset: iCloud asset object
            
        Returns:
            True if successful, False otherwise
        """
        asset_id = icloud_asset.id
        for attempt in range(RETRY_ATTEMPTS):
            try:
                # Get download URL for this version
                download_url = self._get_download_url(icloud_asset, version)
                if not download_url:
                    logger.error(f"No download URL found for asset {asset_id} version {version}")
                    return False

                file_path = self.file_manager.get_file_path(icloud_asset, version)

                
                # Download the file
                success = self._download_from_url(download_url, file_path, asset_id, version)
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
    
    def _get_download_url(self, icloud_asset: PhotoAsset, version: VersionSize) -> str | None:
        """Get download URL for a specific version.
        
        Args:
            icloud_asset: iCloud asset object
            version: Version type (string like "original", "adjusted", etc.)
            
        Returns:
            Download URL or None if not found
        """
        try:
            # Try to get the specific version from asset.versions
            if version in icloud_asset.versions:
                return icloud_asset.versions[version].url
            
        except Exception as e:
            logger.error(f"Error getting download URL for version {version}: {e}")
        
        return None
    
    # TODO: signature sucks -> 
    def _download_from_url(self, url: str, file_path: Path, asset_id, version) -> bool:
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
                file_path, response.raw
            )
            
            return success
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for asset {asset_id} version {version}: {e}")
            return False
        except Exception as e:
            logger.error(f"Download failed for asset {asset_id} version {version}: {e}")
            return False
    
    # def download_assets_batch(self, assets: List[Tuple[Dict[str, Any], Any]]) -> Dict[str, Tuple[List[str], List[str]]]:
    #     """Download a batch of assets in parallel.
        
    #     Args:
    #         assets: List of (asset_data, icloud_asset) tuples
            
    #     Returns:
    #         Dictionary mapping asset_id to (downloaded_versions, failed_versions)
    #     """
    #     results = {}
        
    #     with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS) as executor:
    #         # Submit download tasks for each asset
    #         future_to_asset = {}
    #         for asset_data, icloud_asset in assets:
    #             future = executor.submit(
    #                 self.download_asset_versions,
    #                 asset_data, icloud_asset
    #             )
    #             future_to_asset[future] = asset_data['asset_id']
            
    #         # Collect results
    #         for future in as_completed(future_to_asset):
    #             asset_id = future_to_asset[future]
    #             try:
    #                 downloaded_versions, failed_versions = future.result()
    #                 results[asset_id] = (downloaded_versions, failed_versions)
    #             except Exception as e:
    #                 logger.error(f"Batch download failed for asset {asset_id}: {e}")
    #                 results[asset_id] = ([], ['all'])
        
    #     return results
    
    def cleanup(self) -> None:
        """Clean up resources."""
        self.session.close() 