#!/usr/bin/env python
"""Composition-based service implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Iterator, Optional, Union

from .models import ChangeSet, Photo, SyncConfiguration, SyncResult
from .protocols import (
    ChangeDetector,
    ICloudReader,
    LocalStorage,
    PhotoDownloader,
    ProgressReporter,
    SymlinkManager,
)
from .types import AlbumName, DataPath, LibraryPath, PhotoCount, TimelinePath


@dataclass(frozen=True)
class SyncService:
    """Main service that orchestrates photo synchronization using composition."""
    
    icloud_reader: ICloudReader
    photo_downloader: PhotoDownloader
    local_storage: LocalStorage
    symlink_manager: SymlinkManager
    change_detector: ChangeDetector
    progress_reporter: ProgressReporter
    configuration: SyncConfiguration
    
    def sync_photos(self, data_dir: DataPath, library_dir: LibraryPath, timeline_dir: TimelinePath) -> SyncResult:
        """Orchestrate complete photo synchronization.
        
        Pure coordination function that composes smaller operations.
        """
        try:
            # Phase 1: Discover what we have
            local_photos = self.local_storage.scan_existing_photos(data_dir)
            icloud_photos = self._get_icloud_photos()
            
            # Phase 2: Determine what needs to change
            changes = self.change_detector.detect_changes(icloud_photos, local_photos)
            
            if not changes.has_changes:
                return SyncResult(downloaded=frozenset(), linked=frozenset(), errors=frozenset())
            
            # Phase 3: Report progress
            self.progress_reporter.report_sync_start(PhotoCount(changes.total_changes))
            
            # Phase 4: Execute changes
            downloaded = self._download_new_photos(changes.new_photos, data_dir)
            linked = self._create_symlinks(downloaded, library_dir, timeline_dir)
            
            # Phase 5: Clean up deleted photos
            self._handle_deleted_photos(changes.deleted_photos, data_dir, library_dir, timeline_dir)
            
            result = SyncResult(downloaded=downloaded, linked=linked, errors=frozenset())
            self.progress_reporter.report_sync_complete(result)
            
            return result
            
        except Exception as e:
            error_result = SyncResult(downloaded=frozenset(), linked=frozenset(), errors=frozenset([str(e)]))
            self.progress_reporter.report_sync_complete(error_result)
            return error_result
    
    def _get_icloud_photos(self) -> FrozenSet[Photo]:
        """Get photos from iCloud based on configuration."""
        photos = set()
        
        # Handle different photo selection strategies
        if self.configuration.target_albums:
            # Get photos from specific albums
            for album in self.icloud_reader.get_albums():
                if album.name in self.configuration.target_albums:
                    album_photos = list(self.icloud_reader.get_photos_in_album(
                        album, self.configuration.max_photos_per_album
                    ))
                    photos.update(album_photos)
        else:
            # Get all photos or recent photos
            if self.configuration.max_recent_photos:
                photos.update(self.icloud_reader.get_recent_photos(self.configuration.max_recent_photos))
            else:
                photos.update(self.icloud_reader.get_all_photos(self.configuration.max_photos))
        
        return frozenset(photos)
    
    def _download_new_photos(self, new_photos: FrozenSet[Photo], data_dir: DataPath) -> FrozenSet[Photo]:
        """Download new photos to the data directory."""
        downloaded = set()
        
        for photo in new_photos:
            download_result = self.photo_downloader.download_photo(photo, data_dir, self.configuration.size_preference)
            
            if hasattr(download_result, 'value'):  # Ok result
                downloaded.add(photo)
            # Error handling would be logged by the downloader
            
        return frozenset(downloaded)
    
    def _create_symlinks(self, photos: FrozenSet[Photo], library_dir: LibraryPath, timeline_dir: TimelinePath) -> FrozenSet:
        """Create symlinks for photos in both hierarchies."""
        all_links = set()
        
        for photo in photos:
            # Assume photos are stored with original filename in data directory
            source_path = DataPath(self.configuration.base_directory / "_Data" / photo.filename)
            
            # Create timeline link
            timeline_result = self.symlink_manager.create_timeline_link(photo, source_path, timeline_dir)
            if hasattr(timeline_result, 'value'):
                all_links.add(timeline_result.value)
            
            # Create library links
            library_result = self.symlink_manager.create_library_links(photo, source_path, library_dir)
            if hasattr(library_result, 'value'):
                all_links.update(library_result.value)
        
        return frozenset(all_links)
    
    def _handle_deleted_photos(self, deleted_photos: FrozenSet[Photo], data_dir: DataPath, library_dir: LibraryPath, timeline_dir: TimelinePath) -> None:
        """Handle photos that were deleted from iCloud."""
        deleted_dir = DataPath(self.configuration.base_directory / "_Deleted")
        
        for photo in deleted_photos:
            source_path = DataPath(data_dir / photo.filename)
            
            # Move to deleted directory
            self.local_storage.move_to_deleted(photo, source_path, deleted_dir)
            
            # Remove symlinks
            self.symlink_manager.remove_symlinks(photo, timeline_dir, library_dir)


@dataclass(frozen=True)
class FilterService:
    """Service for filtering photos based on various criteria."""
    
    change_detector: ChangeDetector
    configuration: SyncConfiguration
    
    def apply_filters(self, photos: FrozenSet[Photo]) -> FrozenSet[Photo]:
        """Apply all configured filters to photos."""
        filtered = photos
        
        # Apply album filter
        if self.configuration.target_albums:
            filtered = self.change_detector.filter_by_albums(filtered, self.configuration.target_albums)
        
        # Apply count filter
        if self.configuration.max_photos:
            filtered = self.change_detector.filter_by_count(filtered, self.configuration.max_photos)
        
        # Apply exclude albums filter
        if self.configuration.exclude_albums:
            # Filter out photos from excluded albums
            filtered = frozenset(
                photo for photo in filtered
                if not any(album in self.configuration.exclude_albums for album in photo.albums)
            )
        
        return filtered
    
    def filter_recent_photos(self, photos: FrozenSet[Photo]) -> FrozenSet[Photo]:
        """Filter photos by recent days configuration."""
        if not self.configuration.recent_days_only:
            return photos
        
        from datetime import datetime, timedelta
        cutoff_date = datetime.now() - timedelta(days=self.configuration.recent_days_only)
        
        return frozenset(
            photo for photo in photos
            if photo.creation_date >= cutoff_date
        )


@dataclass(frozen=True)
class ValidationService:
    """Service for validating photos and operations."""
    
    configuration: SyncConfiguration
    
    def validate_sync_configuration(self) -> Union["Ok[None]", "Err[str]"]:
        """Validate that sync configuration is valid."""
        errors = []
        
        # Check directory exists
        if not self.configuration.base_directory.exists():
            errors.append(f"Base directory does not exist: {self.configuration.base_directory}")
        
        # Check conflicting options
        if (self.configuration.target_albums and 
            self.configuration.max_recent_photos and 
            self.configuration.max_photos):
            errors.append("Cannot specify target_albums, max_recent_photos, and max_photos together")
        
        # Check test mode limits
        if self.configuration.max_photos and self.configuration.max_photos > PhotoCount(1000):
            if not self.configuration.dry_run:
                errors.append("Large photo counts require dry_run mode for safety")
        
        if errors:
            return Err("; ".join(errors))
        
        return Ok(None)
    
    def validate_photo_limits(self, photo_count: PhotoCount) -> Union["Ok[None]", "Err[str]"]:
        """Validate photo count against safety limits."""
        MAX_SAFE_PHOTOS = PhotoCount(1000)
        
        if photo_count > MAX_SAFE_PHOTOS and not self.configuration.dry_run:
            return Err(f"Photo count {photo_count} exceeds safety limit {MAX_SAFE_PHOTOS}. Use --dry-run first.")
        
        return Ok(None)


# Simple Result type implementations
class Ok:
    """Success result."""
    def __init__(self, value):
        self.value = value

class Err:
    """Error result."""
    def __init__(self, error):
        self.error = error