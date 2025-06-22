#!/usr/bin/env python
"""Immutable data structures for iCloud Photos Downloader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional

from .types import (
    AlbumName,
    CreationDate,
    DataPath,
    ExifDate,
    FileSizeBytes,
    Filename,
    ICloudDate,
    LibraryPath,
    LogLevel,
    ModificationDate,
    PhotoCount,
    PhotoFormat,
    PhotoId,
    PhotoType,
    SizeOption,
    SymlinkPath,
    TimelinePath,
)


@dataclass(frozen=True)
class Photo:
    """Immutable representation of a photo from iCloud."""
    
    id: PhotoId
    filename: Filename
    creation_date: CreationDate
    modification_date: ModificationDate
    size_bytes: FileSizeBytes
    format: PhotoFormat
    photo_type: PhotoType
    albums: FrozenSet[AlbumName]
    exif_date: Optional[ExifDate] = None
    icloud_date: Optional[ICloudDate] = None
    
    def with_albums(self, albums: FrozenSet[AlbumName]) -> Photo:
        """Return new Photo instance with updated albums."""
        return Photo(
            id=self.id,
            filename=self.filename,
            creation_date=self.creation_date,
            modification_date=self.modification_date,
            size_bytes=self.size_bytes,
            format=self.format,
            photo_type=self.photo_type,
            albums=albums,
            exif_date=self.exif_date,
            icloud_date=self.icloud_date,
        )


@dataclass(frozen=True)
class Album:
    """Immutable representation of an iCloud album."""
    
    name: AlbumName
    photo_count: PhotoCount
    creation_date: CreationDate
    photos: FrozenSet[PhotoId]
    
    def with_photos(self, photos: FrozenSet[PhotoId]) -> Album:
        """Return new Album instance with updated photos."""
        return Album(
            name=self.name,
            photo_count=PhotoCount(len(photos)),
            creation_date=self.creation_date,
            photos=photos,
        )


@dataclass(frozen=True)
class SyncConfiguration:
    """Immutable configuration for sync operations."""
    
    base_directory: DataPath
    size_preference: SizeOption = "original"
    create_timeline: bool = True
    create_library: bool = True
    max_concurrent_downloads: int = 4
    log_level: LogLevel = "info"
    dry_run: bool = False
    
    # Testing parameters
    max_photos: Optional[PhotoCount] = None
    max_photos_per_album: Optional[PhotoCount] = None
    max_recent_photos: Optional[PhotoCount] = None
    target_albums: Optional[FrozenSet[AlbumName]] = None
    exclude_albums: Optional[FrozenSet[AlbumName]] = None
    recent_days_only: Optional[int] = None
    
    def with_base_directory(self, new_dir: DataPath) -> SyncConfiguration:
        """Return new instance with updated base directory."""
        return SyncConfiguration(
            base_directory=new_dir,
            size_preference=self.size_preference,
            create_timeline=self.create_timeline,
            create_library=self.create_library,
            max_concurrent_downloads=self.max_concurrent_downloads,
            log_level=self.log_level,
            dry_run=self.dry_run,
            max_photos=self.max_photos,
            max_photos_per_album=self.max_photos_per_album,
            max_recent_photos=self.max_recent_photos,
            target_albums=self.target_albums,
            exclude_albums=self.exclude_albums,
            recent_days_only=self.recent_days_only,
        )
    
    def with_test_mode(self, enabled: bool = True) -> SyncConfiguration:
        """Return new instance configured for safe testing."""
        if not enabled:
            return self
            
        return SyncConfiguration(
            base_directory=self.base_directory,
            size_preference=self.size_preference,
            create_timeline=self.create_timeline,
            create_library=self.create_library,
            max_concurrent_downloads=1,  # Single threaded for testing
            log_level="debug",
            dry_run=True,
            max_photos=PhotoCount(10),  # Limit to 10 photos
            max_photos_per_album=PhotoCount(5),
            max_recent_photos=None,
            target_albums=self.target_albums,
            exclude_albums=self.exclude_albums,
            recent_days_only=7,  # Only recent photos
        )


@dataclass(frozen=True)
class DirectoryStructure:
    """Immutable representation of the dual hierarchy directory structure."""
    
    base_directory: DataPath
    data_dir: DataPath
    library_dir: LibraryPath
    timeline_dir: TimelinePath
    deleted_dir: DataPath
    
    @classmethod
    def from_base(cls, base_directory: DataPath) -> DirectoryStructure:
        """Create directory structure from base directory."""
        base_path = base_directory
        return cls(
            base_directory=base_directory,
            data_dir=DataPath(base_path / "_Data"),
            library_dir=LibraryPath(base_path / "Library"),
            timeline_dir=TimelinePath(base_path / "Timeline"),
            deleted_dir=DataPath(base_path / "_Deleted"),
        )


@dataclass(frozen=True)
class ChangeSet:
    """Immutable representation of changes between local and iCloud state."""
    
    new_photos: FrozenSet[Photo]
    deleted_photos: FrozenSet[Photo]
    moved_photos: FrozenSet[tuple[Photo, AlbumName]]  # (photo, old_album)
    
    @property
    def has_changes(self) -> bool:
        """Check if there are any changes to process."""
        return bool(self.new_photos or self.deleted_photos or self.moved_photos)
    
    @property
    def total_changes(self) -> int:
        """Total number of changes."""
        return len(self.new_photos) + len(self.deleted_photos) + len(self.moved_photos)


@dataclass(frozen=True)
class SyncResult:
    """Immutable result of a sync operation."""
    
    downloaded: FrozenSet[Photo]
    linked: FrozenSet[SymlinkPath]
    errors: FrozenSet[str]
    
    @property
    def success(self) -> bool:
        """Check if sync was successful (no errors)."""
        return not self.errors
    
    @property
    def photos_downloaded(self) -> int:
        """Number of photos successfully downloaded."""
        return len(self.downloaded)
    
    @property
    def links_created(self) -> int:
        """Number of symlinks successfully created."""
        return len(self.linked)