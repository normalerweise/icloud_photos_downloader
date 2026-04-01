"""Parallel download management with retry logic."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

from icloudpd.new_download.database import ICloudAssetRecord
from pyicloud_ipd.services.photos import PhotoAsset
from pyicloud_ipd.session import PyiCloudSession
from pyicloud_ipd.version_size import VersionSize

from .constants import MAX_CONCURRENT_DOWNLOADS, RETRY_ATTEMPTS, RETRY_DELAY
from .file_manager import FileManager

logger = logging.getLogger(__name__)


class DownloadManager:
    """Manage parallel downloads with retry logic."""

    def __init__(self, file_manager: FileManager, session: PyiCloudSession):
        """Initialize download manager.

        Args:
            file_manager: File manager instance
            session: Authenticated iCloud session for downloads
        """
        self.file_manager = file_manager
        self.session = session

    def download_asset_versions(
        self,
        asset: ICloudAssetRecord,
        icloud_asset: PhotoAsset,
        versions_to_download: List[VersionSize],
    ) -> Tuple[List[str], List[str]]:
        """Download all requested versions for an asset.

        Args:
            asset: ICloudAssetRecord from database
            icloud_asset: PhotoAsset object from iCloud (provides authenticated download)
            versions_to_download: List of VersionSize enums to download

        Returns:
            Tuple of (downloaded_version_values, failed_version_values)
        """
        asset_id: str = asset.asset_id

        if not versions_to_download:
            logger.debug(f"All versions already downloaded for asset {asset_id}")
            return [], []

        logger.info(f"Downloading {len(versions_to_download)} versions for asset {asset_id}")

        # Download versions in parallel
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS) as executor:
            future_to_version: Dict[Any, VersionSize] = {}
            for version in versions_to_download:
                future = executor.submit(self._download_single_version, version, icloud_asset)
                future_to_version[future] = version

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

    def _download_single_version(self, version: VersionSize, icloud_asset: PhotoAsset) -> bool:
        """Download a single version of an asset with retry logic.

        Args:
            version: Version type to download
            icloud_asset: iCloud asset object (provides authenticated session)

        Returns:
            True if successful, False otherwise
        """
        asset_id = icloud_asset.id
        for attempt in range(RETRY_ATTEMPTS):
            try:
                download_url = self._get_download_url(icloud_asset, version)
                if not download_url:
                    logger.error(f"No download URL found for asset {asset_id} version {version}")
                    return False

                file_path = self.file_manager.get_file_path(icloud_asset, version)

                success = self._download_from_asset(icloud_asset, download_url, file_path)
                if success:
                    logger.debug(f"Successfully downloaded {version} for asset {asset_id}")
                    return True

                if attempt < RETRY_ATTEMPTS - 1:
                    logger.warning(
                        f"Download failed for asset {asset_id} version {version}, "
                        f"attempt {attempt + 1}/{RETRY_ATTEMPTS}"
                    )
                    time.sleep(RETRY_DELAY * (2**attempt))

            except Exception as e:
                logger.error(
                    f"Download error for asset {asset_id} version {version}, "
                    f"attempt {attempt + 1}: {e}"
                )
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY * (2**attempt))

        logger.error(
            f"Failed to download {version} for asset {asset_id} after {RETRY_ATTEMPTS} attempts"
        )
        return False

    def _get_download_url(self, icloud_asset: PhotoAsset, version: VersionSize) -> str | None:
        """Get download URL for a specific version.

        Args:
            icloud_asset: iCloud asset object
            version: VersionSize enum value

        Returns:
            Download URL or None if not found
        """
        try:
            if version in icloud_asset.versions:
                return icloud_asset.versions[version].url
        except Exception as e:
            logger.error(f"Error getting download URL for version {version}: {e}")
        return None

    def _download_from_asset(self, icloud_asset: PhotoAsset, url: str, file_path: Path) -> bool:
        """Download file using the authenticated iCloud session.

        Args:
            icloud_asset: PhotoAsset providing authenticated download
            url: Download URL
            file_path: Path to save the file

        Returns:
            True if successful, False otherwise
        """
        try:
            response = icloud_asset.download(self.session, url)
            response.raise_for_status()
            return self.file_manager.save_file_from_stream(file_path, response.raw)
        except Exception as e:
            logger.error(f"Download failed for {file_path.name}: {e}")
            return False

    def cleanup(self) -> None:
        """Clean up resources."""
        pass
