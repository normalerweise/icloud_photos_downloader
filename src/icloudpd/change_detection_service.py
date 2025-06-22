#!/usr/bin/env python
"""Composition-based change detection service."""

from __future__ import annotations

from typing import FrozenSet

from .models import ChangeSet, Photo
from .protocols import ChangeDetector
from .pure_functions import (
    detect_changes_pure,
    exclude_photos_by_albums,
    filter_photos_by_albums,
    filter_photos_by_count,
)
from .types import AlbumName, PhotoCount


class PureChangeDetector:
    """Composition-based change detector using pure functions.
    
    Replaces inheritance with composition of pure functions.
    """
    
    def detect_changes(self, icloud_photos: FrozenSet[Photo], local_photos: FrozenSet[Photo]) -> ChangeSet:
        """Detect changes between iCloud and local photo sets.
        
        Implementation of ChangeDetector protocol using pure functions.
        """
        return detect_changes_pure(icloud_photos, local_photos)
    
    def filter_by_albums(self, photos: FrozenSet[Photo], target_albums: FrozenSet[AlbumName]) -> FrozenSet[Photo]:
        """Filter photos by target albums.
        
        Implementation of ChangeDetector protocol using pure functions.
        """
        return filter_photos_by_albums(photos, target_albums)
    
    def filter_by_count(self, photos: FrozenSet[Photo], max_count: PhotoCount) -> FrozenSet[Photo]:
        """Filter photos by maximum count.
        
        Implementation of ChangeDetector protocol using pure functions.
        """
        return filter_photos_by_count(photos, max_count)
    
    def exclude_by_albums(self, photos: FrozenSet[Photo], exclude_albums: FrozenSet[AlbumName]) -> FrozenSet[Photo]:
        """Exclude photos by album names."""
        return exclude_photos_by_albums(photos, exclude_albums)


class SmartChangeDetector:
    """Enhanced change detector with additional capabilities.
    
    Composes the pure change detector with additional functionality.
    """
    
    def __init__(self) -> None:
        self.pure_detector = PureChangeDetector()
    
    def detect_changes(self, icloud_photos: FrozenSet[Photo], local_photos: FrozenSet[Photo]) -> ChangeSet:
        """Detect changes with enhanced logic."""
        return self.pure_detector.detect_changes(icloud_photos, local_photos)
    
    def filter_by_albums(self, photos: FrozenSet[Photo], target_albums: FrozenSet[AlbumName]) -> FrozenSet[Photo]:
        """Filter by albums."""
        return self.pure_detector.filter_by_albums(photos, target_albums)
    
    def filter_by_count(self, photos: FrozenSet[Photo], max_count: PhotoCount) -> FrozenSet[Photo]:
        """Filter by count."""
        return self.pure_detector.filter_by_count(photos, max_count)
    
    def detect_changes_with_filters(
        self,
        icloud_photos: FrozenSet[Photo],
        local_photos: FrozenSet[Photo],
        target_albums: FrozenSet[AlbumName] | None = None,
        exclude_albums: FrozenSet[AlbumName] | None = None,
        max_count: PhotoCount | None = None,
    ) -> ChangeSet:
        """Detect changes with filtering applied to iCloud photos."""
        # Apply filters to iCloud photos first
        filtered_icloud = icloud_photos
        
        if target_albums:
            filtered_icloud = self.pure_detector.filter_by_albums(filtered_icloud, target_albums)
        
        if exclude_albums:
            filtered_icloud = self.pure_detector.exclude_by_albums(filtered_icloud, exclude_albums)
        
        if max_count:
            filtered_icloud = self.pure_detector.filter_by_count(filtered_icloud, max_count)
        
        # Detect changes with filtered set
        return self.pure_detector.detect_changes(filtered_icloud, local_photos)
    
    def analyze_changeset(self, changeset: ChangeSet) -> dict[str, int]:
        """Analyze a changeset and return statistics."""
        return {
            'new_photos': len(changeset.new_photos),
            'deleted_photos': len(changeset.deleted_photos),
            'moved_photos': len(changeset.moved_photos),
            'total_changes': changeset.total_changes,
        }
    
    def prioritize_changes(self, changeset: ChangeSet) -> ChangeSet:
        """Prioritize changes by importance (newest photos first)."""
        from .pure_functions import get_best_photo_date
        
        # Sort new photos by date (newest first)
        sorted_new = sorted(changeset.new_photos, key=get_best_photo_date, reverse=True)
        
        # Sort deleted photos by date (oldest first for cleanup)
        sorted_deleted = sorted(changeset.deleted_photos, key=get_best_photo_date)
        
        return ChangeSet(
            new_photos=frozenset(sorted_new),
            deleted_photos=frozenset(sorted_deleted),
            moved_photos=changeset.moved_photos,  # Keep as-is for now
        )


class ConfigurableChangeDetector:
    """Change detector that can be configured with different strategies.
    
    Uses composition to allow different detection strategies.
    """
    
    def __init__(self, detector: ChangeDetector | None = None) -> None:
        self.detector = detector or PureChangeDetector()
    
    def detect_changes(self, icloud_photos: FrozenSet[Photo], local_photos: FrozenSet[Photo]) -> ChangeSet:
        """Delegate to configured detector."""
        return self.detector.detect_changes(icloud_photos, local_photos)
    
    def filter_by_albums(self, photos: FrozenSet[Photo], target_albums: FrozenSet[AlbumName]) -> FrozenSet[Photo]:
        """Delegate to configured detector."""
        return self.detector.filter_by_albums(photos, target_albums)
    
    def filter_by_count(self, photos: FrozenSet[Photo], max_count: PhotoCount) -> FrozenSet[Photo]:
        """Delegate to configured detector."""
        return self.detector.filter_by_count(photos, max_count)
    
    def with_detector(self, detector: ChangeDetector) -> 'ConfigurableChangeDetector':
        """Return new instance with different detector (immutable pattern)."""
        return ConfigurableChangeDetector(detector)


# Factory function for creating change detectors
def create_change_detector(strategy: str = "smart") -> ChangeDetector:
    """Factory function to create change detectors.
    
    Args:
        strategy: "pure", "smart", or "configurable"
        
    Returns:
        ChangeDetector implementation
    """
    if strategy == "pure":
        return PureChangeDetector()
    elif strategy == "smart":
        return SmartChangeDetector()
    elif strategy == "configurable":
        return ConfigurableChangeDetector()
    else:
        return SmartChangeDetector()  # Default to smart