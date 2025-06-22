#!/usr/bin/env python
"""Typed protocols for dependency injection and composition."""

from __future__ import annotations

from abc import abstractmethod
from typing import FrozenSet, Iterator, Optional, Protocol, Union

from .models import Album, ChangeSet, Photo, SyncResult
from .types import (
    AlbumName,
    DataPath,
    DownloadProgressPercent,
    LibraryPath,
    PhotoCount,
    PhotoId,
    SizeOption,
    SymlinkPath,
    TimelinePath,
)


class ICloudReader(Protocol):
    """Protocol for reading data from iCloud Photos."""
    
    def get_albums(self) -> Iterator[Album]:
        """Get all albums from iCloud Photos."""
        ...
    
    def get_photos_in_album(self, album: Album, limit: Optional[PhotoCount] = None) -> Iterator[Photo]:
        """Get photos in a specific album with optional limit."""
        ...
    
    def get_recent_photos(self, count: PhotoCount) -> Iterator[Photo]:
        """Get most recent photos."""
        ...
    
    def get_all_photos(self, limit: Optional[PhotoCount] = None) -> Iterator[Photo]:
        """Get all photos with optional limit."""
        ...


class PhotoDownloader(Protocol):
    """Protocol for downloading photos from iCloud."""
    
    def download_photo(self, photo: Photo, target_path: DataPath, size: SizeOption = "original") -> Union["Ok[DataPath]", "Err[str]"]:
        """Download a single photo to target path."""
        ...
    
    def download_photos(self, photos: Iterator[Photo], target_directory: DataPath, size: SizeOption = "original") -> Iterator[Union["Ok[DataPath]", "Err[str]"]]:
        """Download multiple photos to target directory."""
        ...


class LocalStorage(Protocol):
    """Protocol for local file system operations."""
    
    def scan_existing_photos(self, data_directory: DataPath) -> FrozenSet[Photo]:
        """Scan existing photos in local storage."""
        ...
    
    def move_to_deleted(self, photo: Photo, source_path: DataPath, deleted_directory: DataPath) -> Union["Ok[None]", "Err[str]"]:
        """Move a photo to the deleted directory."""
        ...
    
    def cleanup_broken_symlinks(self, directory: Union[LibraryPath, TimelinePath]) -> Union["Ok[int]", "Err[str]"]:
        """Clean up broken symlinks and return count removed."""
        ...


class SymlinkManager(Protocol):
    """Protocol for managing symlinks in Timeline and Library hierarchies."""
    
    def create_timeline_link(self, photo: Photo, source_path: DataPath, timeline_base: TimelinePath) -> Union["Ok[SymlinkPath]", "Err[str]"]:
        """Create a symlink in the Timeline hierarchy."""
        ...
    
    def create_library_links(self, photo: Photo, source_path: DataPath, library_base: LibraryPath) -> Union["Ok[FrozenSet[SymlinkPath]]", "Err[str]"]:
        """Create symlinks in the Library hierarchy for all albums containing the photo."""
        ...
    
    def remove_symlinks(self, photo: Photo, timeline_base: TimelinePath, library_base: LibraryPath) -> Union["Ok[int]", "Err[str]"]:
        """Remove all symlinks for a photo and return count removed."""
        ...


class ChangeDetector(Protocol):
    """Protocol for detecting changes between local and iCloud state."""
    
    def detect_changes(self, icloud_photos: FrozenSet[Photo], local_photos: FrozenSet[Photo]) -> ChangeSet:
        """Detect changes between iCloud and local photo sets."""
        ...
    
    def filter_by_albums(self, photos: FrozenSet[Photo], target_albums: FrozenSet[AlbumName]) -> FrozenSet[Photo]:
        """Filter photos by target albums."""
        ...
    
    def filter_by_count(self, photos: FrozenSet[Photo], max_count: PhotoCount) -> FrozenSet[Photo]:
        """Filter photos by maximum count."""
        ...


class ProgressReporter(Protocol):
    """Protocol for reporting sync progress."""
    
    def report_download_progress(self, photo: Photo, progress: DownloadProgressPercent) -> None:
        """Report download progress for a single photo."""
        ...
    
    def report_sync_start(self, total_photos: PhotoCount) -> None:
        """Report start of sync operation."""
        ...
    
    def report_sync_complete(self, result: SyncResult) -> None:
        """Report completion of sync operation."""
        ...


class PhotoValidator(Protocol):
    """Protocol for validating photos and metadata."""
    
    def validate_photo_integrity(self, photo: Photo, file_path: DataPath) -> Union["Ok[None]", "Err[str]"]:
        """Validate that a downloaded photo file is complete and not corrupted."""
        ...
    
    def extract_exif_date(self, file_path: DataPath) -> Union["Ok[Optional[datetime]]", "Err[str]"]:
        """Extract EXIF creation date from photo file."""
        ...
    
    def get_file_size(self, file_path: DataPath) -> Union["Ok[int]", "Err[str]"]:
        """Get file size in bytes."""
        ...


# Result types for protocols
class Ok:
    """Success result."""
    def __init__(self, value):
        self.value = value

class Err:
    """Error result."""
    def __init__(self, error):
        self.error = error


# Higher-order function protocols
class PhotoProcessor(Protocol):
    """Protocol for processing individual photos."""
    
    def __call__(self, photo: Photo) -> Union["Ok[Photo]", "Err[str]"]:
        """Process a single photo."""
        ...


class PhotoFilter(Protocol):
    """Protocol for filtering photos."""
    
    def __call__(self, photos: Iterator[Photo]) -> Iterator[Photo]:
        """Filter photos based on criteria."""
        ...


class PathCalculator(Protocol):
    """Protocol for calculating file paths."""
    
    def __call__(self, photo: Photo, base_directory: DataPath) -> DataPath:
        """Calculate target path for a photo."""
        ...