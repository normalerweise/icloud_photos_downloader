#!/usr/bin/env python
"""Timeline directory management for date-based photo organization."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Photo
from .types import CreationDate, DataPath, SymlinkPath, TimelinePath


class TimelineManager:
    """Manages creation of Timeline directory structure and symlinks."""
    
    def __init__(self, timeline_base: TimelinePath) -> None:
        self.timeline_base = timeline_base
    
    def calculate_timeline_path(self, photo: Photo) -> TimelinePath:
        """Calculate the timeline directory path for a photo based on creation date.
        
        Pure function: same inputs always produce same output.
        
        Args:
            photo: Photo with creation date information
            
        Returns:
            TimelinePath where the photo should be linked
        """
        date = self._get_photo_date(photo)
        year = date.year
        month = date.month
        
        return TimelinePath(
            self.timeline_base / str(year) / f"{month:02d}" / photo.filename
        )
    
    def create_timeline_link(self, photo: Photo, source_path: DataPath) -> Optional[str]:
        """Create a symlink in the Timeline hierarchy.
        
        Args:
            photo: Photo to create link for
            source_path: Path to the actual photo file in _Data/
            
        Returns:
            None if successful, error message if failed
        """
        try:
            timeline_path = self.calculate_timeline_path(photo)
            timeline_dir = timeline_path.parent
            
            # Create year/month directory structure
            timeline_dir.mkdir(parents=True, exist_ok=True)
            
            # Create symlink if it doesn't exist
            if not timeline_path.exists():
                # Calculate relative path from timeline to data
                relative_source = os.path.relpath(source_path, timeline_dir)
                timeline_path.symlink_to(relative_source)
                
            return None
            
        except OSError as e:
            return f"Failed to create timeline link for {photo.filename}: {e}"
        except Exception as e:
            return f"Unexpected error creating timeline link for {photo.filename}: {e}"
    
    def _get_photo_date(self, photo: Photo) -> datetime:
        """Get the best available date for timeline organization.
        
        Priority:
        1. EXIF DateTimeOriginal
        2. iCloud creation timestamp
        3. Photo creation date (fallback)
        
        Args:
            photo: Photo to get date from
            
        Returns:
            datetime to use for timeline organization
        """
        # Prefer EXIF date if available
        if photo.exif_date:
            return photo.exif_date
        
        # Fall back to iCloud date
        if photo.icloud_date:
            return photo.icloud_date
        
        # Final fallback to creation date
        return photo.creation_date
    
    def create_year_month_structure(self, years: set[int]) -> Optional[str]:
        """Pre-create year directories for better organization.
        
        Args:
            years: Set of years to create directories for
            
        Returns:
            None if successful, error message if failed
        """
        try:
            for year in years:
                year_dir = self.timeline_base / str(year)
                year_dir.mkdir(parents=True, exist_ok=True)
                
                # Create month directories (1-12)
                for month in range(1, 13):
                    month_dir = year_dir / f"{month:02d}"
                    month_dir.mkdir(exist_ok=True)
                    
            return None
            
        except OSError as e:
            return f"Failed to create year/month structure: {e}"
        except Exception as e:
            return f"Unexpected error creating year/month structure: {e}"


def get_photo_timeline_years(photos: list[Photo]) -> set[int]:
    """Extract all years from a collection of photos for timeline structure.
    
    Pure function that analyzes photo dates.
    
    Args:
        photos: List of photos to analyze
        
    Returns:
        Set of years found in the photos
    """
    years = set()
    
    for photo in photos:
        # Check all available dates
        dates_to_check = [photo.creation_date]
        
        if photo.exif_date:
            dates_to_check.append(photo.exif_date)
        
        if photo.icloud_date:
            dates_to_check.append(photo.icloud_date)
        
        for date in dates_to_check:
            years.add(date.year)
    
    return years