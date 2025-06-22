#!/usr/bin/env python
"""Specialized handling for Live Photos, RAW+JPEG, and multi-format media."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Optional, Union

from .models import Photo
from .types import (
    DataPath,
    FileSizeBytes,
    Filename,
    PhotoFormat,
    PhotoType,
    SymlinkPath,
)


@dataclass(frozen=True)
class MediaComponent:
    """Immutable representation of a media file component."""
    
    filename: Filename
    format: PhotoFormat
    component_type: str  # "image", "video", "raw", "jpeg"
    relative_path: DataPath
    size_bytes: Optional[FileSizeBytes] = None
    
    @property
    def is_primary(self) -> bool:
        """Check if this is the primary component of a multi-component media."""
        return self.component_type in ("image", "raw")
    
    @property
    def is_secondary(self) -> bool:
        """Check if this is a secondary component."""
        return self.component_type in ("video", "jpeg")


@dataclass(frozen=True)
class MultiComponentMedia:
    """Immutable representation of media with multiple file components."""
    
    photo: Photo
    primary_component: MediaComponent
    secondary_components: FrozenSet[MediaComponent]
    
    @property
    def all_components(self) -> FrozenSet[MediaComponent]:
        """Get all components (primary + secondary)."""
        return frozenset([self.primary_component]) | self.secondary_components
    
    @property
    def total_size(self) -> FileSizeBytes:
        """Calculate total size of all components."""
        total = 0
        for component in self.all_components:
            if component.size_bytes:
                total += component.size_bytes
        return FileSizeBytes(total)
    
    @property
    def component_count(self) -> int:
        """Get total number of components."""
        return len(self.all_components)


class MediaTypeAnalyzer:
    """Analyzer for determining media types and components."""
    
    @staticmethod
    def analyze_photo(photo: Photo) -> MultiComponentMedia:
        """Analyze a photo and determine its components."""
        if photo.photo_type == PhotoType.LIVE:
            return MediaTypeAnalyzer._analyze_live_photo(photo)
        elif photo.photo_type == PhotoType.RAW_PLUS_JPEG:
            return MediaTypeAnalyzer._analyze_raw_plus_jpeg(photo)
        else:
            return MediaTypeAnalyzer._analyze_standard_photo(photo)
    
    @staticmethod
    def _analyze_live_photo(photo: Photo) -> MultiComponentMedia:
        """Analyze a Live Photo (image + video components)."""
        # Primary: Image component (.heic or .jpg)
        image_format = photo.format if photo.format in [PhotoFormat.HEIC, PhotoFormat.JPEG] else PhotoFormat.HEIC
        primary = MediaComponent(
            filename=photo.filename,
            format=image_format,
            component_type="image",
            relative_path=DataPath(Path(photo.filename)),
        )
        
        # Secondary: Video component (.mov)
        video_filename = Filename(str(Path(photo.filename).with_suffix('.mov')))
        secondary = MediaComponent(
            filename=video_filename,
            format=PhotoFormat.MOV,
            component_type="video",
            relative_path=DataPath(Path(video_filename)),
        )
        
        return MultiComponentMedia(
            photo=photo,
            primary_component=primary,
            secondary_components=frozenset([secondary]),
        )
    
    @staticmethod
    def _analyze_raw_plus_jpeg(photo: Photo) -> MultiComponentMedia:
        """Analyze a RAW+JPEG photo (raw + jpeg components)."""
        # Primary: RAW component (.heic, .raw, etc.)
        raw_format = photo.format if photo.format in [PhotoFormat.HEIC, PhotoFormat.RAW] else PhotoFormat.HEIC
        primary = MediaComponent(
            filename=photo.filename,
            format=raw_format,
            component_type="raw",
            relative_path=DataPath(Path(photo.filename)),
        )
        
        # Secondary: JPEG component (.jpg)
        jpeg_filename = Filename(str(Path(photo.filename).with_suffix('.jpg')))
        secondary = MediaComponent(
            filename=jpeg_filename,
            format=PhotoFormat.JPEG,
            component_type="jpeg",
            relative_path=DataPath(Path(jpeg_filename)),
        )
        
        return MultiComponentMedia(
            photo=photo,
            primary_component=primary,
            secondary_components=frozenset([secondary]),
        )
    
    @staticmethod
    def _analyze_standard_photo(photo: Photo) -> MultiComponentMedia:
        """Analyze a standard single-file photo."""
        primary = MediaComponent(
            filename=photo.filename,
            format=photo.format,
            component_type="image",
            relative_path=DataPath(Path(photo.filename)),
        )
        
        return MultiComponentMedia(
            photo=photo,
            primary_component=primary,
            secondary_components=frozenset(),
        )


class MultiComponentDownloader:
    """Downloader specialized for multi-component media."""
    
    def __init__(self, base_downloader, dry_run: bool = False) -> None:
        self.base_downloader = base_downloader
        self.dry_run = dry_run
    
    def download_multi_component(self, media: MultiComponentMedia, target_directory: DataPath) -> Union["Ok[FrozenSet[DataPath]]", "Err[str]"]:
        """Download all components of a multi-component media."""
        try:
            downloaded_paths = set()
            
            # Download all components
            for component in media.all_components:
                target_path = DataPath(Path(target_directory) / component.filename)
                
                if self.dry_run:
                    print(f"[DRY RUN] Would download {component.filename} ({component.component_type})")
                    downloaded_paths.add(target_path)
                    continue
                
                # Download the component
                success = self._download_component(component, target_path)
                if success:
                    downloaded_paths.add(target_path)
                else:
                    return Err(f"Failed to download component {component.filename}")
            
            return Ok(frozenset(downloaded_paths))
            
        except Exception as e:
            return Err(f"Multi-component download failed: {e}")
    
    def _download_component(self, component: MediaComponent, target_path: DataPath) -> bool:
        """Download a single component."""
        try:
            # Ensure target directory exists
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Use base downloader (placeholder - would integrate with existing downloader)
            # return self.base_downloader(component, target_path)
            
            # Placeholder: create file for testing
            Path(target_path).touch()
            return True
            
        except Exception:
            return False


class MultiComponentSymlinker:
    """Symlink manager specialized for multi-component media."""
    
    def __init__(self, base_symlinker, dry_run: bool = False) -> None:
        self.base_symlinker = base_symlinker
        self.dry_run = dry_run
    
    def create_multi_component_links(
        self, 
        media: MultiComponentMedia, 
        data_directory: DataPath, 
        timeline_base: DataPath, 
        library_base: DataPath
    ) -> Union["Ok[FrozenSet[SymlinkPath]]", "Err[str]"]:
        """Create symlinks for all components of multi-component media."""
        try:
            created_links = set()
            
            # Create links for all components
            for component in media.all_components:
                source_path = DataPath(Path(data_directory) / component.filename)
                
                # Create timeline links
                timeline_links = self._create_component_timeline_links(
                    component, source_path, timeline_base, media.photo
                )
                created_links.update(timeline_links)
                
                # Create library links
                library_links = self._create_component_library_links(
                    component, source_path, library_base, media.photo
                )
                created_links.update(library_links)
            
            return Ok(frozenset(created_links))
            
        except Exception as e:
            return Err(f"Multi-component linking failed: {e}")
    
    def _create_component_timeline_links(
        self, 
        component: MediaComponent, 
        source_path: DataPath, 
        timeline_base: DataPath, 
        photo: Photo
    ) -> FrozenSet[SymlinkPath]:
        """Create timeline links for a component."""
        links = set()
        
        try:
            from .pure_functions import calculate_timeline_path
            
            # Calculate timeline path for this component
            timeline_path = calculate_timeline_path(photo, timeline_base)
            
            # Adjust filename for component
            component_timeline_path = Path(timeline_path).with_name(component.filename)
            
            if self.dry_run:
                print(f"[DRY RUN] Would create timeline link: {component_timeline_path} -> {source_path}")
                links.add(SymlinkPath(component_timeline_path))
                return frozenset(links)
            
            # Create directory and symlink
            component_timeline_path.parent.mkdir(parents=True, exist_ok=True)
            if not component_timeline_path.exists():
                relative_source = os.path.relpath(source_path, component_timeline_path.parent)
                component_timeline_path.symlink_to(relative_source)
                links.add(SymlinkPath(component_timeline_path))
        
        except Exception as e:
            print(f"Failed to create timeline link for {component.filename}: {e}")
        
        return frozenset(links)
    
    def _create_component_library_links(
        self, 
        component: MediaComponent, 
        source_path: DataPath, 
        library_base: DataPath, 
        photo: Photo
    ) -> FrozenSet[SymlinkPath]:
        """Create library links for a component."""
        links = set()
        
        try:
            # Create links in all albums
            for album in photo.albums:
                album_dir = Path(library_base) / album
                component_library_path = album_dir / component.filename
                
                if self.dry_run:
                    print(f"[DRY RUN] Would create library link: {component_library_path} -> {source_path}")
                    links.add(SymlinkPath(component_library_path))
                    continue
                
                # Create directory and symlink
                album_dir.mkdir(parents=True, exist_ok=True)
                if not component_library_path.exists():
                    relative_source = os.path.relpath(source_path, album_dir)
                    component_library_path.symlink_to(relative_source)
                    links.add(SymlinkPath(component_library_path))
        
        except Exception as e:
            print(f"Failed to create library links for {component.filename}: {e}")
        
        return frozenset(links)


class MediaTypeService:
    """High-level service for handling all media types."""
    
    def __init__(self, downloader, symlinker, dry_run: bool = False) -> None:
        self.analyzer = MediaTypeAnalyzer()
        self.multi_downloader = MultiComponentDownloader(downloader, dry_run)
        self.multi_symlinker = MultiComponentSymlinker(symlinker, dry_run)
        self.dry_run = dry_run
    
    def process_photo(
        self, 
        photo: Photo, 
        data_directory: DataPath, 
        timeline_base: DataPath, 
        library_base: DataPath
    ) -> Union["Ok[MultiComponentMedia]", "Err[str]"]:
        """Process a photo with full type-aware handling."""
        try:
            # Analyze the photo type
            media = self.analyzer.analyze_photo(photo)
            
            # Download all components
            download_result = self.multi_downloader.download_multi_component(media, data_directory)
            if hasattr(download_result, 'error'):
                return Err(download_result.error)
            
            # Create symlinks for all components
            symlink_result = self.multi_symlinker.create_multi_component_links(
                media, data_directory, timeline_base, library_base
            )
            if hasattr(symlink_result, 'error'):
                return Err(symlink_result.error)
            
            return Ok(media)
            
        except Exception as e:
            return Err(f"Photo processing failed: {e}")
    
    def get_media_info(self, photo: Photo) -> dict[str, Union[str, int]]:
        """Get comprehensive media information."""
        media = self.analyzer.analyze_photo(photo)
        
        return {
            'photo_type': photo.photo_type.value,
            'primary_format': media.primary_component.format.value,
            'component_count': media.component_count,
            'total_size': media.total_size,
            'has_video': any(c.component_type == "video" for c in media.all_components),
            'has_raw': any(c.component_type == "raw" for c in media.all_components),
        }


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
def create_media_service(downloader, symlinker, dry_run: bool = False) -> MediaTypeService:
    """Factory function to create a media type service."""
    return MediaTypeService(downloader, symlinker, dry_run)


def analyze_photo_components(photo: Photo) -> MultiComponentMedia:
    """Analyze a photo and return its component structure."""
    return MediaTypeAnalyzer.analyze_photo(photo)