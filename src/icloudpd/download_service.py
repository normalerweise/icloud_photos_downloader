#!/usr/bin/env python
"""Functional download pipeline with typed structures."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, FrozenSet, Iterator, Optional, Union

from .models import Photo, SyncConfiguration
from .protocols import PhotoDownloader, ProgressReporter
from .pure_functions import calculate_data_path
from .types import (
    DataPath,
    DownloadProgressPercent,
    FileSizeBytes,
    Filename,
    PhotoCount,
    PhotoFormat,
    PhotoType,
    SizeOption,
)


@dataclass(frozen=True)
class DownloadResult:
    """Immutable result of a download operation."""
    
    photo: Photo
    success: bool
    local_path: Optional[DataPath] = None
    error_message: Optional[str] = None
    bytes_downloaded: FileSizeBytes = FileSizeBytes(0)
    
    @property
    def is_success(self) -> bool:
        """Check if download was successful."""
        return self.success and self.local_path is not None
    
    @property
    def is_error(self) -> bool:
        """Check if download had an error."""
        return not self.success or self.error_message is not None


@dataclass(frozen=True)
class DownloadBatch:
    """Immutable batch of download results."""
    
    results: FrozenSet[DownloadResult]
    total_photos: PhotoCount
    successful_downloads: PhotoCount
    failed_downloads: PhotoCount
    total_bytes: FileSizeBytes
    
    @classmethod
    def from_results(cls, results: FrozenSet[DownloadResult]) -> DownloadBatch:
        """Create batch from download results."""
        successful = sum(1 for r in results if r.is_success)
        failed = sum(1 for r in results if r.is_error)
        total_bytes = sum(r.bytes_downloaded for r in results)
        
        return cls(
            results=results,
            total_photos=PhotoCount(len(results)),
            successful_downloads=PhotoCount(successful),
            failed_downloads=PhotoCount(failed),
            total_bytes=FileSizeBytes(total_bytes),
        )
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total_photos == 0:
            return 100.0
        return (self.successful_downloads / self.total_photos) * 100.0


class FunctionalDownloader:
    """Functional download pipeline using composition and pure functions."""
    
    def __init__(
        self,
        existing_downloader: Callable,  # Existing download function from current codebase
        progress_reporter: Optional[ProgressReporter] = None,
        configuration: Optional[SyncConfiguration] = None,
    ) -> None:
        self.existing_downloader = existing_downloader
        self.progress_reporter = progress_reporter
        self.configuration = configuration or SyncConfiguration(base_directory=DataPath(Path("/tmp")))
    
    def download_photo(self, photo: Photo, target_path: DataPath, size: SizeOption = "original") -> Union["Ok[DataPath]", "Err[str]"]:
        """Download a single photo to target path.
        
        Implementation of PhotoDownloader protocol.
        """
        try:
            # Calculate the exact target file path
            target_file_path = calculate_data_path(photo, target_path)
            
            # Ensure target directory exists
            target_file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Handle different photo types
            download_paths = self._get_download_paths(photo, target_file_path, size)
            
            for source_photo, dest_path in download_paths:
                # Use existing downloader with progress reporting
                success = self._download_single_file(source_photo, dest_path, size)
                
                if not success:
                    return Err(f"Failed to download {photo.filename}")
            
            return Ok(target_file_path)
            
        except Exception as e:
            return Err(f"Download error for {photo.filename}: {e}")
    
    def download_photos(self, photos: Iterator[Photo], target_directory: DataPath, size: SizeOption = "original") -> Iterator[Union["Ok[DataPath]", "Err[str]"]]:
        """Download multiple photos to target directory.
        
        Implementation of PhotoDownloader protocol.
        """
        for photo in photos:
            result = self.download_photo(photo, target_directory, size)
            yield result
    
    def download_batch_functional(self, photos: FrozenSet[Photo], target_directory: DataPath, size: SizeOption = "original") -> DownloadBatch:
        """Download photos using functional pipeline.
        
        Pure functional approach with immutable results.
        """
        # Report start of batch
        if self.progress_reporter:
            self.progress_reporter.report_sync_start(PhotoCount(len(photos)))
        
        # Process photos functionally
        results = set()
        
        for photo in photos:
            result = self._download_photo_functional(photo, target_directory, size)
            results.add(result)
            
            # Report individual progress
            if self.progress_reporter and result.is_success:
                self.progress_reporter.report_download_progress(photo, DownloadProgressPercent(100))
        
        # Create immutable batch result
        batch = DownloadBatch.from_results(frozenset(results))
        
        # Report completion
        if self.progress_reporter:
            from .models import SyncResult
            sync_result = SyncResult(
                downloaded=frozenset(r.photo for r in results if r.is_success),
                linked=frozenset(),  # Will be handled by symlink manager
                errors=frozenset(r.error_message for r in results if r.error_message),
            )
            self.progress_reporter.report_sync_complete(sync_result)
        
        return batch
    
    def _download_photo_functional(self, photo: Photo, target_directory: DataPath, size: SizeOption) -> DownloadResult:
        """Download single photo with functional approach."""
        try:
            target_path = calculate_data_path(photo, target_directory)
            
            # Handle different photo types with specialized logic
            if photo.photo_type == PhotoType.LIVE:
                return self._download_live_photo(photo, target_path, size)
            elif photo.photo_type == PhotoType.RAW_PLUS_JPEG:
                return self._download_raw_plus_jpeg(photo, target_path, size)
            else:
                return self._download_standard_photo(photo, target_path, size)
                
        except Exception as e:
            return DownloadResult(
                photo=photo,
                success=False,
                error_message=str(e),
            )
    
    def _download_standard_photo(self, photo: Photo, target_path: DataPath, size: SizeOption) -> DownloadResult:
        """Download a standard photo."""
        try:
            # Ensure directory exists
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Use existing downloader
            success = self._download_single_file(photo, target_path, size)
            
            if success:
                # Get actual file size
                file_size = FileSizeBytes(Path(target_path).stat().st_size) if Path(target_path).exists() else FileSizeBytes(0)
                
                return DownloadResult(
                    photo=photo,
                    success=True,
                    local_path=target_path,
                    bytes_downloaded=file_size,
                )
            else:
                return DownloadResult(
                    photo=photo,
                    success=False,
                    error_message="Download failed",
                )
                
        except Exception as e:
            return DownloadResult(
                photo=photo,
                success=False,
                error_message=str(e),
            )
    
    def _download_live_photo(self, photo: Photo, target_path: DataPath, size: SizeOption) -> DownloadResult:
        """Download a Live Photo (both image and video components)."""
        try:
            # Live photos have both .heic/.jpg and .mov files
            base_path = Path(target_path)
            image_path = base_path.with_suffix('.heic' if photo.format == PhotoFormat.HEIC else '.jpg')
            video_path = base_path.with_suffix('.mov')
            
            # Ensure directory exists
            image_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Download both components
            image_success = self._download_single_file(photo, DataPath(image_path), size)
            video_success = self._download_single_file(photo, DataPath(video_path), size)
            
            total_bytes = FileSizeBytes(0)
            if image_path.exists():
                total_bytes = FileSizeBytes(total_bytes + image_path.stat().st_size)
            if video_path.exists():
                total_bytes = FileSizeBytes(total_bytes + video_path.stat().st_size)
            
            if image_success and video_success:
                return DownloadResult(
                    photo=photo,
                    success=True,
                    local_path=DataPath(image_path),  # Primary path is the image
                    bytes_downloaded=total_bytes,
                )
            else:
                return DownloadResult(
                    photo=photo,
                    success=False,
                    error_message="Failed to download Live Photo components",
                    bytes_downloaded=total_bytes,
                )
                
        except Exception as e:
            return DownloadResult(
                photo=photo,
                success=False,
                error_message=f"Live Photo download error: {e}",
            )
    
    def _download_raw_plus_jpeg(self, photo: Photo, target_path: DataPath, size: SizeOption) -> DownloadResult:
        """Download RAW+JPEG photo (both RAW and JPEG files)."""
        try:
            # RAW+JPEG has both .raw/.heic and .jpg files
            base_path = Path(target_path)
            raw_path = base_path.with_suffix('.heic')  # or .raw depending on format
            jpeg_path = base_path.with_suffix('.jpg')
            
            # Ensure directory exists
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Download both components
            raw_success = self._download_single_file(photo, DataPath(raw_path), size)
            jpeg_success = self._download_single_file(photo, DataPath(jpeg_path), size)
            
            total_bytes = FileSizeBytes(0)
            if raw_path.exists():
                total_bytes = FileSizeBytes(total_bytes + raw_path.stat().st_size)
            if jpeg_path.exists():
                total_bytes = FileSizeBytes(total_bytes + jpeg_path.stat().st_size)
            
            if raw_success and jpeg_success:
                return DownloadResult(
                    photo=photo,
                    success=True,
                    local_path=DataPath(raw_path),  # Primary path is the RAW
                    bytes_downloaded=total_bytes,
                )
            else:
                return DownloadResult(
                    photo=photo,
                    success=False,
                    error_message="Failed to download RAW+JPEG components",
                    bytes_downloaded=total_bytes,
                )
                
        except Exception as e:
            return DownloadResult(
                photo=photo,
                success=False,
                error_message=f"RAW+JPEG download error: {e}",
            )
    
    def _get_download_paths(self, photo: Photo, target_path: DataPath, size: SizeOption) -> list[tuple[Photo, DataPath]]:
        """Get all paths that need to be downloaded for a photo."""
        paths = [(photo, target_path)]
        
        if photo.photo_type == PhotoType.LIVE:
            # Live photos need both image and video
            video_path = DataPath(Path(target_path).with_suffix('.mov'))
            paths.append((photo, video_path))
        elif photo.photo_type == PhotoType.RAW_PLUS_JPEG:
            # RAW+JPEG needs both files
            jpeg_path = DataPath(Path(target_path).with_suffix('.jpg'))
            paths.append((photo, jpeg_path))
        
        return paths
    
    def _download_single_file(self, photo: Photo, target_path: DataPath, size: SizeOption) -> bool:
        """Download a single file using existing downloader."""
        try:
            # This would integrate with the existing download functionality
            # For now, simulate with a placeholder
            if self.configuration.dry_run:
                print(f"[DRY RUN] Would download {photo.filename} to {target_path}")
                return True
            
            # In real implementation, this would call the existing downloader
            # return self.existing_downloader(photo, target_path, size)
            
            # Placeholder: create empty file for testing
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).touch()
            return True
            
        except Exception as e:
            print(f"Download failed for {photo.filename}: {e}")
            return False


# Result types
class Ok:
    """Success result."""
    def __init__(self, value):
        self.value = value

class Err:
    """Error result."""
    def __init__(self, error):
        self.error = error


# Factory functions for creating downloaders
def create_functional_downloader(
    existing_downloader: Callable,
    configuration: SyncConfiguration,
    progress_reporter: Optional[ProgressReporter] = None,
) -> FunctionalDownloader:
    """Factory function to create a functional downloader."""
    return FunctionalDownloader(
        existing_downloader=existing_downloader,
        progress_reporter=progress_reporter,
        configuration=configuration,
    )


def create_download_pipeline(configuration: SyncConfiguration) -> Callable[[FrozenSet[Photo], DataPath], DownloadBatch]:
    """Create a download pipeline function with configuration."""
    def download_pipeline(photos: FrozenSet[Photo], target_directory: DataPath) -> DownloadBatch:
        downloader = FunctionalDownloader(
            existing_downloader=lambda photo, path, size: True,  # Placeholder
            configuration=configuration,
        )
        return downloader.download_batch_functional(photos, target_directory, configuration.size_preference)
    
    return download_pipeline