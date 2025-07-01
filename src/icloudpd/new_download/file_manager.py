"""File operations and naming for the new download architecture."""

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional
import re
import base64

from pyicloud_ipd.services.photos import PhotoAsset
from pyicloud_ipd.version_size import VersionSize

from .constants import DATA_DIRECTORY, DOWNLOAD_VERSIONS

logger = logging.getLogger(__name__)


class FileManager:
    """Handle file operations and naming for downloaded assets."""
    
    def __init__(self, base_directory: Path):
        """Initialize file manager.
        
        Args:
            base_directory: Base directory for downloads
        """
        self.base_directory = Path(base_directory)
        self.data_directory = self.base_directory / DATA_DIRECTORY
        self._ensure_directories()
    
    def _ensure_directories(self) -> None:
        """Ensure required directories exist."""
        self.data_directory.mkdir(parents=True, exist_ok=True)
    
    def get_file_path(self, icloud_asset: PhotoAsset, version: VersionSize) -> Path:
        """Generate file path for an asset version.
        
        Args:
            icloud_asset: iCloud asset
            version: Version type (original, adjusted, alternative)
            
        Returns:
            Path where the file should be saved
        """
        # Encode asset_id as URL-safe base64, strip padding
        safe_asset_id = base64.urlsafe_b64encode(icloud_asset.id.encode()).decode().rstrip('=')
        # Get file extension from original filename
        version_data = icloud_asset.versions[version]
        extension = version_data.file_extension.lower()
        size_indicator = version.value
        
        # Generate filename: base64(asset_id)-version.extension
        filename = f"{safe_asset_id}-{size_indicator}.{extension}"
        
        return self.data_directory / filename
    
    def file_exists(self, icloud_asset: PhotoAsset, version: VersionSize) -> bool:
        """Check if a file already exists.
        
        Args:
            asset_id: iCloud asset ID
            version: Version type
            original_filename: Original filename from iCloud
            
        Returns:
            True if file exists, False otherwise
        """
        file_path = self.get_file_path(icloud_asset, version)
        return file_path.exists()
    
    def save_file(self, icloud_asset: PhotoAsset, version: VersionSize, 
                  content: bytes, overwrite: bool = False) -> bool:
        """Save file content to disk.
        
        Args:
            asset_id: iCloud asset ID
            version: Version type
            original_filename: Original filename from iCloud
            content: File content as bytes
            overwrite: Whether to overwrite existing file
            
        Returns:
            True if successful, False otherwise
        """
        file_path = self.get_file_path(icloud_asset, version)
        
        # Check if file exists and we're not overwriting
        if file_path.exists() and not overwrite:
            logger.warning(f"File already exists: {file_path}")
            return False
        
        try:
            # Write file atomically using temporary file
            temp_path = file_path.with_suffix(file_path.suffix + '.tmp')
            
            with open(temp_path, 'wb') as f:
                f.write(content)
            
            # Atomic rename
            temp_path.rename(file_path)
            
            logger.debug(f"Saved file: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save file {file_path}: {e}")
            # Clean up temp file if it exists
            if temp_path.exists():
                temp_path.unlink()
            return False
    
    def save_file_from_stream(self, file_path: Path,
                             stream, overwrite: bool = False) -> bool:
        """Save file from a stream (e.g., HTTP response).
        
        Args:
            asset_id: iCloud asset ID
            version: Version type
            original_filename: Original filename from iCloud
            stream: File stream to read from
            overwrite: Whether to overwrite existing file
            
        Returns:
            True if successful, False otherwise
        """
        
        # Check if file exists and we're not overwriting
        if file_path.exists() and not overwrite:
            logger.warning(f"File already exists: {file_path}")
            return False
        
        try:
            # Write file atomically using temporary file
            temp_path = file_path.with_suffix(file_path.suffix + '.tmp')
            
            with open(temp_path, 'wb') as f:
                shutil.copyfileobj(stream, f)
            
            # Atomic rename
            temp_path.rename(file_path)
            
            logger.debug(f"Saved file from stream: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save file from stream {file_path}: {e}")
            # Clean up temp file if it exists
            if temp_path.exists():
                temp_path.unlink()
            return False
    
    def delete_file(self, icloud_asset: PhotoAsset, version: VersionSize) -> bool:
        """Delete a file.
        
        Args:
            asset_id: iCloud asset ID
            version: Version type
            original_filename: Original filename from iCloud
            
        Returns:
            True if successful, False otherwise
        """
        file_path = self.get_file_path(icloud_asset, version)
        
        try:
            if file_path.exists():
                file_path.unlink()
                logger.debug(f"Deleted file: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete file {file_path}: {e}")
            return False
    
    def get_file_size(self, icloud_asset: PhotoAsset, version: VersionSize) -> Optional[int]:
        """Get file size in bytes.
        
        Args:
            asset_id: iCloud asset ID
            version: Version type
            original_filename: Original filename from iCloud
            
        Returns:
            File size in bytes or None if file doesn't exist
        """
        file_path = self.get_file_path(icloud_asset, version)
        
        try:
            if file_path.exists():
                return file_path.stat().st_size
        except Exception as e:
            logger.error(f"Failed to get file size for {file_path}: {e}")
        
        return None
    
    def list_downloaded_files(self, asset_id: str) -> List[str]:
        """List all downloaded versions for an asset.
        
        Args:
            asset_id: iCloud asset ID
            
        Returns:
            List of downloaded version types
        """
        downloaded_versions = []
        
        for version in DOWNLOAD_VERSIONS:
            # Try to find files with this version
            pattern = f"{asset_id}-{version}.*"
            matching_files = list(self.data_directory.glob(pattern))
            
            if matching_files:
                downloaded_versions.append(version)
        
        return downloaded_versions
    
    def cleanup_incomplete_downloads(self) -> int:
        """Clean up incomplete downloads (temporary files).
        
        Returns:
            Number of files cleaned up
        """
        cleaned_count = 0
        
        for temp_file in self.data_directory.glob("*.tmp"):
            try:
                temp_file.unlink()
                cleaned_count += 1
                logger.debug(f"Cleaned up incomplete download: {temp_file}")
            except Exception as e:
                logger.error(f"Failed to clean up {temp_file}: {e}")
        
        return cleaned_count
    
    def get_disk_usage(self) -> int:
        """Get total disk usage of data directory in bytes.
        
        Returns:
            Total size in bytes
        """
        total_size = 0
        
        try:
            for file_path in self.data_directory.rglob("*"):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
        except Exception as e:
            logger.error(f"Failed to calculate disk usage: {e}")
        
        return total_size 