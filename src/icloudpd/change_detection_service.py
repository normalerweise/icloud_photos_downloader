#!/usr/bin/env python
"""Immutable change detection for iCloud photo synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import FrozenSet, Optional, Union

from .models import Photo
from .pure_functions import filter_photos_by_recent_days, get_best_photo_date
from .types import AlbumName, CreationDate, DataPath, FileSizeBytes, PhotoCount


@dataclass(frozen=True)
class PhotoChanges:
    """Immutable representation of detected photo changes."""
    
    new_photos: FrozenSet[Photo]
    deleted_photos: FrozenSet[Photo]
    modified_photos: FrozenSet[Photo]
    unchanged_photos: FrozenSet[Photo]
    
    @property
    def has_changes(self) -> bool:
        """Check if any changes were detected."""
        return (len(self.new_photos) > 0 or 
                len(self.deleted_photos) > 0 or 
                len(self.modified_photos) > 0)
    
    @property
    def total_changes(self) -> PhotoCount:
        """Get total number of changes."""
        return PhotoCount(len(self.new_photos) + len(self.deleted_photos) + len(self.modified_photos))
    
    @property
    def summary(self) -> str:
        """Get human-readable summary of changes."""
        return (f"New: {len(self.new_photos)}, "
                f"Deleted: {len(self.deleted_photos)}, "
                f"Modified: {len(self.modified_photos)}, "
                f"Unchanged: {len(self.unchanged_photos)}")


@dataclass(frozen=True)
class LocalPhotoState:
    """Immutable representation of local photo state."""
    
    existing_photos: FrozenSet[Photo]
    total_size_bytes: FileSizeBytes
    total_count: PhotoCount
    last_scan_time: datetime
    
    @classmethod
    def empty(cls) -> LocalPhotoState:
        """Create empty local state."""
        return cls(
            existing_photos=frozenset(),
            total_size_bytes=FileSizeBytes(0),
            total_count=PhotoCount(0),
            last_scan_time=datetime.now(),
        )
    
    @classmethod
    def from_photos(cls, photos: FrozenSet[Photo]) -> LocalPhotoState:
        """Create local state from photo set."""
        total_size = sum(photo.size_bytes for photo in photos)
        return cls(
            existing_photos=photos,
            total_size_bytes=FileSizeBytes(total_size),
            total_count=PhotoCount(len(photos)),
            last_scan_time=datetime.now(),
        )


@dataclass(frozen=True)
class ICloudPhotoState:
    """Immutable representation of iCloud photo state."""
    
    available_photos: FrozenSet[Photo]
    total_size_bytes: FileSizeBytes
    total_count: PhotoCount
    albums_scanned: FrozenSet[AlbumName]
    scan_time: datetime
    
    @classmethod
    def from_photos_and_albums(cls, photos: FrozenSet[Photo], albums: FrozenSet[AlbumName]) -> ICloudPhotoState:
        """Create iCloud state from photos and albums."""
        total_size = sum(photo.size_bytes for photo in photos)
        return cls(
            available_photos=photos,
            total_size_bytes=FileSizeBytes(total_size),
            total_count=PhotoCount(len(photos)),
            albums_scanned=albums,
            scan_time=datetime.now(),
        )


class SmartChangeDetector:
    """Immutable change detection using functional comparison."""
    
    def detect_changes(self, icloud_photos: FrozenSet[Photo], local_photos: FrozenSet[Photo]) -> PhotoChanges:
        """Detect changes between iCloud and local photo sets.
        
        Uses immutable data structures and functional comparison.
        """
        # Create photo ID sets for efficient comparison
        icloud_ids = frozenset(photo.id for photo in icloud_photos)
        local_ids = frozenset(photo.id for photo in local_photos)
        
        # Create photo lookup dictionaries
        icloud_by_id = {photo.id: photo for photo in icloud_photos}
        local_by_id = {photo.id: photo for photo in local_photos}
        
        # Detect new photos (in iCloud but not local)
        new_photo_ids = icloud_ids - local_ids
        new_photos = frozenset(icloud_by_id[photo_id] for photo_id in new_photo_ids)
        
        # Detect deleted photos (in local but not in iCloud)
        deleted_photo_ids = local_ids - icloud_ids
        deleted_photos = frozenset(local_by_id[photo_id] for photo_id in deleted_photo_ids)
        
        # Detect modified photos (same ID but different content)
        common_ids = icloud_ids & local_ids
        modified_photos = frozenset(
            icloud_by_id[photo_id] 
            for photo_id in common_ids
            if self._is_photo_modified(icloud_by_id[photo_id], local_by_id[photo_id])
        )
        
        # Detect unchanged photos
        unchanged_photos = frozenset(
            icloud_by_id[photo_id]
            for photo_id in common_ids
            if not self._is_photo_modified(icloud_by_id[photo_id], local_by_id[photo_id])
        )
        
        return PhotoChanges(
            new_photos=new_photos,
            deleted_photos=deleted_photos,
            modified_photos=modified_photos,
            unchanged_photos=unchanged_photos,
        )
    
    def _is_photo_modified(self, icloud_photo: Photo, local_photo: Photo) -> bool:
        """Check if a photo has been modified between iCloud and local versions."""
        # Compare modification dates
        if icloud_photo.modification_date != local_photo.modification_date:
            return True
        
        # Compare file sizes
        if icloud_photo.size_bytes != local_photo.size_bytes:
            return True
        
        # Compare album memberships
        if icloud_photo.albums != local_photo.albums:
            return True
        
        return False
    
    def filter_by_date_range(
        self, 
        photos: FrozenSet[Photo], 
        start_date: Optional[CreationDate] = None,
        end_date: Optional[CreationDate] = None
    ) -> FrozenSet[Photo]:
        """Filter photos by date range using immutable operations."""
        filtered = photos
        
        if start_date:
            filtered = frozenset(
                photo for photo in filtered 
                if get_best_photo_date(photo) >= start_date
            )
        
        if end_date:
            filtered = frozenset(
                photo for photo in filtered 
                if get_best_photo_date(photo) <= end_date
            )
        
        return filtered
    
    def filter_by_recent_days(self, photos: FrozenSet[Photo], days: int) -> FrozenSet[Photo]:
        """Filter photos to only recent days using pure function."""
        return filter_photos_by_recent_days(photos, days)
    
    def exclude_by_albums(self, photos: FrozenSet[Photo], exclude_albums: FrozenSet[AlbumName]) -> FrozenSet[Photo]:
        """Exclude photos that are only in specified albums."""
        return frozenset(
            photo for photo in photos
            if not photo.albums.issubset(exclude_albums)
        )
    
    def filter_by_target_albums(self, photos: FrozenSet[Photo], target_albums: FrozenSet[AlbumName]) -> FrozenSet[Photo]:
        """Filter photos to only those in target albums."""
        return frozenset(
            photo for photo in photos
            if photo.albums & target_albums  # Photos in any target album
        )
    
    def filter_by_size_threshold(
        self, 
        photos: FrozenSet[Photo], 
        min_size: Optional[FileSizeBytes] = None,
        max_size: Optional[FileSizeBytes] = None
    ) -> FrozenSet[Photo]:
        """Filter photos by file size thresholds."""
        filtered = photos
        
        if min_size:
            filtered = frozenset(photo for photo in filtered if photo.size_bytes >= min_size)
        
        if max_size:
            filtered = frozenset(photo for photo in filtered if photo.size_bytes <= max_size)
        
        return filtered
    
    def _limit_photo_count(self, photos: FrozenSet[Photo], max_count: PhotoCount) -> FrozenSet[Photo]:
        """Limit total number of photos, prioritizing most recent."""
        if len(photos) <= max_count:
            return photos
        
        # Sort by creation date (most recent first) and take top N
        sorted_photos = sorted(photos, key=lambda p: get_best_photo_date(p), reverse=True)
        return frozenset(sorted_photos[:max_count])


class IncrementalChangeDetector:
    """Advanced change detector with incremental state management."""
    
    def __init__(self) -> None:
        self.base_detector = SmartChangeDetector()
    
    def detect_incremental_changes(
        self, 
        current_icloud_state: ICloudPhotoState,
        current_local_state: LocalPhotoState,
        previous_local_state: Optional[LocalPhotoState] = None
    ) -> PhotoChanges:
        """Detect changes with consideration of previous state."""
        # Primary change detection
        changes = self.base_detector.detect_changes(
            current_icloud_state.available_photos,
            current_local_state.existing_photos
        )
        
        # If we have previous state, refine the detection
        if previous_local_state:
            changes = self._refine_with_previous_state(changes, previous_local_state, current_local_state)
        
        return changes
    
    def _refine_with_previous_state(
        self, 
        changes: PhotoChanges, 
        previous_state: LocalPhotoState, 
        current_state: LocalPhotoState
    ) -> PhotoChanges:
        """Refine change detection using previous local state."""
        # Detect photos that were deleted locally since last sync
        previously_local_ids = frozenset(photo.id for photo in previous_state.existing_photos)
        currently_local_ids = frozenset(photo.id for photo in current_state.existing_photos)
        
        locally_deleted_ids = previously_local_ids - currently_local_ids
        
        # Remove locally deleted photos from "deleted_photos" (they weren't deleted from iCloud)
        previously_local_by_id = {photo.id: photo for photo in previous_state.existing_photos}
        locally_deleted_photos = frozenset(
            previously_local_by_id[photo_id] for photo_id in locally_deleted_ids
        )
        
        # Filter out locally deleted photos from the deleted set
        refined_deleted_photos = changes.deleted_photos - locally_deleted_photos
        
        return PhotoChanges(
            new_photos=changes.new_photos,
            deleted_photos=refined_deleted_photos,
            modified_photos=changes.modified_photos,
            unchanged_photos=changes.unchanged_photos,
        )
    
    def create_smart_sync_plan(
        self, 
        changes: PhotoChanges,
        max_downloads: Optional[PhotoCount] = None,
        prioritize_recent: bool = True
    ) -> PhotoChanges:
        """Create an optimized sync plan from detected changes."""
        new_photos = changes.new_photos
        
        # Apply download limits with smart prioritization
        if max_downloads and len(new_photos) > max_downloads:
            if prioritize_recent:
                # Sort by creation date (most recent first)
                sorted_photos = sorted(
                    new_photos, 
                    key=lambda p: get_best_photo_date(p),
                    reverse=True
                )
                new_photos = frozenset(sorted_photos[:max_downloads])
            else:
                # Take first N photos (arbitrary order)
                new_photos = frozenset(list(new_photos)[:max_downloads])
        
        return PhotoChanges(
            new_photos=new_photos,
            deleted_photos=changes.deleted_photos,
            modified_photos=changes.modified_photos,
            unchanged_photos=changes.unchanged_photos,
        )


# Factory functions
def create_change_detector() -> SmartChangeDetector:
    """Factory function to create a change detector."""
    return SmartChangeDetector()


def create_incremental_detector() -> IncrementalChangeDetector:
    """Factory function to create an incremental change detector."""
    return IncrementalChangeDetector()


def detect_photo_changes(icloud_photos: FrozenSet[Photo], local_photos: FrozenSet[Photo]) -> PhotoChanges:
    """Functional interface for detecting photo changes."""
    detector = SmartChangeDetector()
    return detector.detect_changes(icloud_photos, local_photos)