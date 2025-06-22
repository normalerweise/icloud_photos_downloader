#!/usr/bin/env python
"""Functional symlink creation service for Timeline and Library hierarchies."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Iterator, Optional, Union

from .models import DirectoryStructure, Photo
from .protocols import SymlinkManager
from .pure_functions import (
    calculate_library_paths,
    calculate_relative_symlink_path,
    calculate_timeline_path,
    group_photos_by_album,
    group_photos_by_year,
)
from .types import AlbumName, DataPath, LibraryPath, SymlinkPath, TimelinePath


@dataclass(frozen=True)
class SymlinkResult:
    """Immutable result of a symlink creation operation."""
    
    photo: Photo
    symlink_path: SymlinkPath
    success: bool
    error_message: Optional[str] = None
    
    @property
    def is_success(self) -> bool:
        """Check if symlink creation was successful."""
        return self.success and self.error_message is None


@dataclass(frozen=True)
class SymlinkBatch:
    """Immutable batch of symlink creation results."""
    
    timeline_results: FrozenSet[SymlinkResult]
    library_results: FrozenSet[SymlinkResult]
    total_links_created: int
    failed_links: int
    
    @classmethod
    def from_results(
        cls, 
        timeline_results: FrozenSet[SymlinkResult], 
        library_results: FrozenSet[SymlinkResult]
    ) -> SymlinkBatch:
        """Create batch from symlink results."""
        all_results = timeline_results | library_results
        successful = sum(1 for r in all_results if r.is_success)
        failed = len(all_results) - successful
        
        return cls(
            timeline_results=timeline_results,
            library_results=library_results,
            total_links_created=successful,
            failed_links=failed,
        )
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        total = self.total_links_created + self.failed_links
        if total == 0:
            return 100.0
        return (self.total_links_created / total) * 100.0


class FunctionalSymlinkManager:
    """Functional symlink manager using pure functions and composition."""
    
    def __init__(self, directory_structure: DirectoryStructure, dry_run: bool = False) -> None:
        self.structure = directory_structure
        self.dry_run = dry_run
    
    def create_timeline_link(self, photo: Photo, source_path: DataPath, timeline_base: TimelinePath) -> Union["Ok[SymlinkPath]", "Err[str]"]:
        """Create a symlink in the Timeline hierarchy.
        
        Implementation of SymlinkManager protocol.
        """
        try:
            timeline_path = calculate_timeline_path(photo, timeline_base)
            
            if self.dry_run:
                print(f"[DRY RUN] Would create timeline link: {timeline_path} -> {source_path}")
                return Ok(SymlinkPath(timeline_path))
            
            # Create year/month directory structure
            timeline_dir = Path(timeline_path).parent
            timeline_dir.mkdir(parents=True, exist_ok=True)
            
            # Create symlink if it doesn't exist
            if not Path(timeline_path).exists():
                relative_source = calculate_relative_symlink_path(source_path, SymlinkPath(timeline_path))
                Path(timeline_path).symlink_to(relative_source)
            
            return Ok(SymlinkPath(timeline_path))
            
        except Exception as e:
            return Err(f"Failed to create timeline link for {photo.filename}: {e}")
    
    def create_library_links(self, photo: Photo, source_path: DataPath, library_base: LibraryPath) -> Union["Ok[FrozenSet[SymlinkPath]]", "Err[str]"]:
        """Create symlinks in the Library hierarchy for all albums containing the photo.
        
        Implementation of SymlinkManager protocol.
        """
        try:
            created_links = set()
            library_paths = calculate_library_paths(photo, library_base)
            
            for library_path in library_paths:
                if self.dry_run:
                    print(f"[DRY RUN] Would create library link: {library_path} -> {source_path}")
                    created_links.add(SymlinkPath(library_path))
                    continue
                
                library_dir = Path(library_path).parent
                
                # Create album directory
                library_dir.mkdir(parents=True, exist_ok=True)
                
                # Create symlink if it doesn't exist
                if not Path(library_path).exists():
                    relative_source = calculate_relative_symlink_path(source_path, SymlinkPath(library_path))
                    Path(library_path).symlink_to(relative_source)
                    created_links.add(SymlinkPath(library_path))
            
            return Ok(frozenset(created_links))
            
        except Exception as e:
            return Err(f"Failed to create library links for {photo.filename}: {e}")
    
    def remove_symlinks(self, photo: Photo, timeline_base: TimelinePath, library_base: LibraryPath) -> Union["Ok[int]", "Err[str]"]:
        """Remove all symlinks for a photo and return count removed.
        
        Implementation of SymlinkManager protocol.
        """
        try:
            count = 0
            
            if self.dry_run:
                timeline_path = calculate_timeline_path(photo, timeline_base)
                library_paths = calculate_library_paths(photo, library_base)
                total_would_remove = 1 + len(library_paths)
                print(f"[DRY RUN] Would remove {total_would_remove} symlinks for {photo.filename}")
                return Ok(total_would_remove)
            
            # Remove timeline symlink
            timeline_path = calculate_timeline_path(photo, timeline_base)
            if Path(timeline_path).is_symlink():
                Path(timeline_path).unlink()
                count += 1
            
            # Remove library symlinks
            library_paths = calculate_library_paths(photo, library_base)
            for library_path in library_paths:
                if Path(library_path).is_symlink():
                    Path(library_path).unlink()
                    count += 1
            
            return Ok(count)
            
        except Exception as e:
            return Err(f"Failed to remove symlinks for {photo.filename}: {e}")
    
    def create_all_symlinks_functional(self, photos: FrozenSet[Photo], data_directory: DataPath) -> SymlinkBatch:
        """Create all symlinks for a set of photos using functional approach."""
        timeline_results = self._create_timeline_links_batch(photos, data_directory)
        library_results = self._create_library_links_batch(photos, data_directory)
        
        return SymlinkBatch.from_results(timeline_results, library_results)
    
    def _create_timeline_links_batch(self, photos: FrozenSet[Photo], data_directory: DataPath) -> FrozenSet[SymlinkResult]:
        """Create timeline symlinks for a batch of photos."""
        results = set()
        
        for photo in photos:
            source_path = DataPath(Path(data_directory) / photo.filename)
            timeline_result = self.create_timeline_link(photo, source_path, self.structure.timeline_dir)
            
            if hasattr(timeline_result, 'value'):  # Ok result
                result = SymlinkResult(
                    photo=photo,
                    symlink_path=timeline_result.value,
                    success=True,
                )
            else:  # Err result
                result = SymlinkResult(
                    photo=photo,
                    symlink_path=SymlinkPath(Path("/")),  # Placeholder
                    success=False,
                    error_message=timeline_result.error,
                )
            
            results.add(result)
        
        return frozenset(results)
    
    def _create_library_links_batch(self, photos: FrozenSet[Photo], data_directory: DataPath) -> FrozenSet[SymlinkResult]:
        """Create library symlinks for a batch of photos."""
        results = set()
        
        for photo in photos:
            source_path = DataPath(Path(data_directory) / photo.filename)
            library_result = self.create_library_links(photo, source_path, self.structure.library_dir)
            
            if hasattr(library_result, 'value'):  # Ok result
                # Create a result for each created link
                for symlink_path in library_result.value:
                    result = SymlinkResult(
                        photo=photo,
                        symlink_path=symlink_path,
                        success=True,
                    )
                    results.add(result)
            else:  # Err result
                result = SymlinkResult(
                    photo=photo,
                    symlink_path=SymlinkPath(Path("/")),  # Placeholder
                    success=False,
                    error_message=library_result.error,
                )
                results.add(result)
        
        return frozenset(results)


class HierarchicalSymlinkManager:
    """Advanced symlink manager that organizes by hierarchy patterns."""
    
    def __init__(self, base_manager: FunctionalSymlinkManager) -> None:
        self.base_manager = base_manager
    
    def create_organized_timeline(self, photos: FrozenSet[Photo], data_directory: DataPath) -> dict[int, SymlinkBatch]:
        """Create timeline symlinks organized by year."""
        # Group photos by year using pure function
        photos_by_year = group_photos_by_year(photos)
        
        results_by_year = {}
        for year, year_photos in photos_by_year.items():
            year_batch = self.base_manager.create_all_symlinks_functional(year_photos, data_directory)
            results_by_year[year] = year_batch
        
        return results_by_year
    
    def create_organized_library(self, photos: FrozenSet[Photo], data_directory: DataPath) -> dict[AlbumName, SymlinkBatch]:
        """Create library symlinks organized by album."""
        # Group photos by album using pure function
        photos_by_album = group_photos_by_album(photos)
        
        results_by_album = {}
        for album, album_photos in photos_by_album.items():
            album_batch = self.base_manager.create_all_symlinks_functional(album_photos, data_directory)
            results_by_album[album] = album_batch
        
        return results_by_album
    
    def create_complete_hierarchy(self, photos: FrozenSet[Photo], data_directory: DataPath) -> dict[str, Union[SymlinkBatch, dict]]:
        """Create complete dual hierarchy with organization."""
        return {
            'timeline': self.create_organized_timeline(photos, data_directory),
            'library': self.create_organized_library(photos, data_directory),
            'summary': self.base_manager.create_all_symlinks_functional(photos, data_directory),
        }


class LivePhotoSymlinkManager:
    """Specialized symlink manager for Live Photos and multi-file photos."""
    
    def __init__(self, base_manager: FunctionalSymlinkManager) -> None:
        self.base_manager = base_manager
    
    def create_live_photo_links(self, photo: Photo, data_directory: DataPath) -> SymlinkBatch:
        """Create symlinks for Live Photos (both image and video components)."""
        if photo.photo_type != PhotoType.LIVE:
            # Delegate to base manager for non-Live photos
            return self.base_manager.create_all_symlinks_functional(frozenset([photo]), data_directory)
        
        # Live Photos have both .heic/.jpg and .mov files
        results = set()
        
        # Create links for image component
        image_source = DataPath(Path(data_directory) / photo.filename)
        image_batch = self.base_manager.create_all_symlinks_functional(frozenset([photo]), data_directory)
        results.update(image_batch.timeline_results)
        results.update(image_batch.library_results)
        
        # Create links for video component
        video_filename = Path(photo.filename).with_suffix('.mov')
        video_photo = photo.with_albums(photo.albums)  # Same photo but for video component
        
        # Create timeline link for video
        video_source = DataPath(Path(data_directory) / video_filename)
        video_timeline = self.base_manager.create_timeline_link(
            video_photo, video_source, self.base_manager.structure.timeline_dir
        )
        
        if hasattr(video_timeline, 'value'):
            results.add(SymlinkResult(
                photo=video_photo,
                symlink_path=video_timeline.value,
                success=True,
            ))
        
        # Create library links for video
        video_library = self.base_manager.create_library_links(
            video_photo, video_source, self.base_manager.structure.library_dir
        )
        
        if hasattr(video_library, 'value'):
            for symlink_path in video_library.value:
                results.add(SymlinkResult(
                    photo=video_photo,
                    symlink_path=symlink_path,
                    success=True,
                ))
        
        # Separate timeline and library results
        timeline_results = frozenset(r for r in results if 'Timeline' in str(r.symlink_path))
        library_results = frozenset(r for r in results if 'Library' in str(r.symlink_path))
        
        return SymlinkBatch.from_results(timeline_results, library_results)


# Result types
class Ok:
    """Success result."""
    def __init__(self, value):
        self.value = value

class Err:
    """Error result."""
    def __init__(self, error):
        self.error = error


# Factory functions
def create_symlink_manager(directory_structure: DirectoryStructure, dry_run: bool = False) -> FunctionalSymlinkManager:
    """Factory function to create a functional symlink manager."""
    return FunctionalSymlinkManager(directory_structure, dry_run)


def create_hierarchical_manager(directory_structure: DirectoryStructure, dry_run: bool = False) -> HierarchicalSymlinkManager:
    """Factory function to create a hierarchical symlink manager."""
    base_manager = FunctionalSymlinkManager(directory_structure, dry_run)
    return HierarchicalSymlinkManager(base_manager)


def create_live_photo_manager(directory_structure: DirectoryStructure, dry_run: bool = False) -> LivePhotoSymlinkManager:
    """Factory function to create a Live Photo symlink manager."""
    base_manager = FunctionalSymlinkManager(directory_structure, dry_run)
    return LivePhotoSymlinkManager(base_manager)