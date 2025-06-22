#!/usr/bin/env python
"""Pure functions for path calculations, date handling, and data transformations."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import FrozenSet, Optional, Sequence

from .models import Album, ChangeSet, Photo
from .types import (
    AlbumName,
    CreationDate,
    DataPath,
    ExifDate,
    Filename,
    ICloudDate,
    LibraryPath,
    PhotoCount,
    SymlinkPath,
    TimelinePath,
)


# Pure path calculation functions
def calculate_data_path(photo: Photo, data_directory: DataPath) -> DataPath:
    """Calculate the path where a photo should be stored in _Data/ directory.
    
    Pure function: same inputs always produce same output.
    
    Args:
        photo: Photo to calculate path for
        data_directory: Base _Data/ directory
        
    Returns:
        Complete path where photo should be stored
    """
    return DataPath(Path(data_directory) / photo.filename)


def calculate_timeline_path(photo: Photo, timeline_base: TimelinePath) -> TimelinePath:
    """Calculate the timeline directory path for a photo based on creation date.
    
    Pure function: same inputs always produce same output.
    
    Args:
        photo: Photo with creation date information  
        timeline_base: Base Timeline/ directory
        
    Returns:
        Complete timeline path where photo should be linked
    """
    date = get_best_photo_date(photo)
    year = date.year
    month = date.month
    
    return TimelinePath(
        Path(timeline_base) / str(year) / f"{month:02d}" / photo.filename
    )


def calculate_library_paths(photo: Photo, library_base: LibraryPath) -> FrozenSet[LibraryPath]:
    """Calculate all library paths for a photo based on its albums.
    
    Pure function: same inputs always produce same output.
    
    Args:
        photo: Photo with album information
        library_base: Base Library/ directory
        
    Returns:
        Set of all library paths where photo should be linked
    """
    paths = set()
    
    for album in photo.albums:
        album_path = LibraryPath(Path(library_base) / album / photo.filename)
        paths.add(album_path)
    
    return frozenset(paths)


def calculate_relative_symlink_path(source_path: DataPath, target_path: SymlinkPath) -> str:
    """Calculate relative path for symlink creation.
    
    Pure function: calculates relative path without file system access.
    
    Args:
        source_path: Actual file location in _Data/
        target_path: Where symlink will be created
        
    Returns:
        Relative path string for symlink creation
    """
    return os.path.relpath(source_path, Path(target_path).parent)


# Pure date handling functions
def get_best_photo_date(photo: Photo) -> datetime:
    """Get the best available date for a photo using priority order.
    
    Pure function: consistent date selection logic.
    
    Priority:
    1. EXIF DateTimeOriginal
    2. iCloud creation timestamp  
    3. Photo creation date (fallback)
    
    Args:
        photo: Photo to get date from
        
    Returns:
        Best available datetime for the photo
    """
    # Prefer EXIF date if available
    if photo.exif_date:
        return photo.exif_date
    
    # Fall back to iCloud date
    if photo.icloud_date:
        return photo.icloud_date
    
    # Final fallback to creation date
    return photo.creation_date


def is_photo_recent(photo: Photo, days: int) -> bool:
    """Check if a photo is within the specified number of recent days.
    
    Pure function: deterministic date comparison.
    
    Args:
        photo: Photo to check
        days: Number of days to consider recent
        
    Returns:
        True if photo is within recent days, False otherwise
    """
    cutoff_date = datetime.now() - timedelta(days=days)
    photo_date = get_best_photo_date(photo)
    return photo_date >= cutoff_date


def extract_year_from_photo(photo: Photo) -> int:
    """Extract year from photo's best available date.
    
    Pure function: consistent year extraction.
    
    Args:
        photo: Photo to extract year from
        
    Returns:
        Year as integer
    """
    return get_best_photo_date(photo).year


def extract_month_from_photo(photo: Photo) -> int:
    """Extract month from photo's best available date.
    
    Pure function: consistent month extraction.
    
    Args:
        photo: Photo to extract month from
        
    Returns:
        Month as integer (1-12)
    """
    return get_best_photo_date(photo).month


# Pure data transformation functions
def detect_changes_pure(icloud_photos: FrozenSet[Photo], local_photos: FrozenSet[Photo]) -> ChangeSet:
    """Detect changes between iCloud and local photo sets.
    
    Pure function: same inputs always produce same output.
    
    Args:
        icloud_photos: Current photos in iCloud
        local_photos: Current photos in local storage
        
    Returns:
        ChangeSet describing what needs to be updated
    """
    # Find new photos (in iCloud but not local)
    new_photos = icloud_photos - local_photos
    
    # Find deleted photos (in local but not iCloud)  
    deleted_photos = local_photos - icloud_photos
    
    # For now, no moved photo detection (could be added later)
    moved_photos = frozenset()
    
    return ChangeSet(
        new_photos=new_photos,
        deleted_photos=deleted_photos,
        moved_photos=moved_photos,
    )


def filter_photos_by_albums(photos: FrozenSet[Photo], target_albums: FrozenSet[AlbumName]) -> FrozenSet[Photo]:
    """Filter photos to only include those in target albums.
    
    Pure function: functional filtering.
    
    Args:
        photos: Photos to filter
        target_albums: Albums to include
        
    Returns:
        Filtered set of photos
    """
    return frozenset(
        photo for photo in photos
        if any(album in target_albums for album in photo.albums)
    )


def filter_photos_by_count(photos: FrozenSet[Photo], max_count: PhotoCount) -> FrozenSet[Photo]:
    """Filter photos to maximum count, preserving most recent.
    
    Pure function: deterministic filtering.
    
    Args:
        photos: Photos to filter
        max_count: Maximum number of photos to return
        
    Returns:
        Filtered set of photos (most recent first)
    """
    # Sort by best available date (most recent first)
    sorted_photos = sorted(photos, key=get_best_photo_date, reverse=True)
    
    # Take only the requested count
    limited_photos = sorted_photos[:max_count]
    
    return frozenset(limited_photos)


def filter_photos_by_recent_days(photos: FrozenSet[Photo], days: int) -> FrozenSet[Photo]:
    """Filter photos to only include those from recent days.
    
    Pure function: date-based filtering.
    
    Args:
        photos: Photos to filter
        days: Number of recent days to include
        
    Returns:
        Filtered set of photos
    """
    return frozenset(
        photo for photo in photos
        if is_photo_recent(photo, days)
    )


def exclude_photos_by_albums(photos: FrozenSet[Photo], exclude_albums: FrozenSet[AlbumName]) -> FrozenSet[Photo]:
    """Exclude photos that are in any of the specified albums.
    
    Pure function: functional filtering.
    
    Args:
        photos: Photos to filter
        exclude_albums: Albums to exclude
        
    Returns:
        Filtered set of photos with excluded albums removed
    """
    return frozenset(
        photo for photo in photos
        if not any(album in exclude_albums for album in photo.albums)
    )


# Pure aggregation functions
def group_photos_by_year(photos: FrozenSet[Photo]) -> dict[int, FrozenSet[Photo]]:
    """Group photos by year for timeline organization.
    
    Pure function: deterministic grouping.
    
    Args:
        photos: Photos to group
        
    Returns:
        Dictionary mapping year to photos
    """
    groups: dict[int, set[Photo]] = {}
    
    for photo in photos:
        year = extract_year_from_photo(photo)
        if year not in groups:
            groups[year] = set()
        groups[year].add(photo)
    
    # Convert to frozensets
    return {year: frozenset(photos_set) for year, photos_set in groups.items()}


def group_photos_by_album(photos: FrozenSet[Photo]) -> dict[AlbumName, FrozenSet[Photo]]:
    """Group photos by album for library organization.
    
    Pure function: deterministic grouping.
    
    Args:
        photos: Photos to group
        
    Returns:
        Dictionary mapping album name to photos
    """
    groups: dict[AlbumName, set[Photo]] = {}
    
    for photo in photos:
        for album in photo.albums:
            if album not in groups:
                groups[album] = set()
            groups[album].add(photo)
    
    # Convert to frozensets
    return {album: frozenset(photos_set) for album, photos_set in groups.items()}


def get_all_years_from_photos(photos: FrozenSet[Photo]) -> FrozenSet[int]:
    """Extract all unique years from a collection of photos.
    
    Pure function: data extraction.
    
    Args:
        photos: Photos to analyze
        
    Returns:
        Set of all years found in the photos
    """
    years = {extract_year_from_photo(photo) for photo in photos}
    return frozenset(years)


def get_all_albums_from_photos(photos: FrozenSet[Photo]) -> FrozenSet[AlbumName]:
    """Extract all unique album names from a collection of photos.
    
    Pure function: data extraction.
    
    Args:
        photos: Photos to analyze
        
    Returns:
        Set of all album names found in the photos
    """
    albums = set()
    for photo in photos:
        albums.update(photo.albums)
    return frozenset(albums)