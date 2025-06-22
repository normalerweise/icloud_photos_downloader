#!/usr/bin/env python
"""Composition-based file system service implementing storage protocols."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import FrozenSet, Iterator, Optional, Union

from .models import DirectoryStructure, Photo
from .protocols import LocalStorage, SymlinkManager
from .pure_functions import (
    calculate_data_path,
    calculate_library_paths,
    calculate_relative_symlink_path,
    calculate_timeline_path,
)
from .types import AlbumName, DataPath, Filename, LibraryPath, PhotoId, SymlinkPath, TimelinePath


class FileSystemStorage:
    """Composition-based file system storage implementation.
    
    Replaces inheritance with composition and dependency injection.
    """
    
    def __init__(self, directory_structure: DirectoryStructure) -> None:
        self.structure = directory_structure
    
    def scan_existing_photos(self, data_directory: DataPath) -> FrozenSet[Photo]:
        """Scan existing photos in local storage.
        
        Implementation of LocalStorage protocol.
        """
        photos = set()
        data_path = Path(data_directory)
        
        if not data_path.exists():
            return frozenset()
        
        try:
            for file_path in data_path.iterdir():
                if file_path.is_file() and self._is_photo_file(file_path):
                    photo = self._create_photo_from_file(file_path)
                    if photo:
                        photos.add(photo)
        except OSError:
            # Handle permission errors gracefully
            pass
        
        return frozenset(photos)
    
    def move_to_deleted(self, photo: Photo, source_path: DataPath, deleted_directory: DataPath) -> Union["Ok[None]", "Err[str]"]:
        """Move a photo to the deleted directory.
        
        Implementation of LocalStorage protocol.
        """
        try:
            deleted_path = Path(deleted_directory)
            deleted_path.mkdir(parents=True, exist_ok=True)
            
            target_path = deleted_path / photo.filename
            shutil.move(str(source_path), str(target_path))
            
            return Ok(None)
            
        except OSError as e:
            return Err(f"Failed to move {photo.filename} to deleted: {e}")
        except Exception as e:
            return Err(f"Unexpected error moving {photo.filename}: {e}")
    
    def cleanup_broken_symlinks(self, directory: Union[LibraryPath, TimelinePath]) -> Union["Ok[int]", "Err[str]"]:
        """Clean up broken symlinks and return count removed.
        
        Implementation of LocalStorage protocol.
        """
        try:
            count = 0
            dir_path = Path(directory)
            
            if not dir_path.exists():
                return Ok(0)
            
            for file_path in dir_path.rglob("*"):
                if file_path.is_symlink() and not file_path.exists():
                    file_path.unlink()
                    count += 1
            
            return Ok(count)
            
        except OSError as e:
            return Err(f"Failed to cleanup broken symlinks in {directory}: {e}")
        except Exception as e:
            return Err(f"Unexpected error cleaning up symlinks: {e}")
    
    def _is_photo_file(self, file_path: Path) -> bool:
        """Check if file is a supported photo/video format."""
        photo_extensions = {'.jpg', '.jpeg', '.heic', '.png', '.mov', '.mp4', '.tiff', '.raw'}
        return file_path.suffix.lower() in photo_extensions
    
    def _create_photo_from_file(self, file_path: Path) -> Optional[Photo]:
        """Create a Photo object from a file on disk."""
        try:
            from datetime import datetime
            from .types import CreationDate, FileSizeBytes, PhotoFormat, PhotoId, PhotoType
            
            stat = file_path.stat()
            creation_date = CreationDate(datetime.fromtimestamp(stat.st_ctime))
            
            # Basic photo object - could be enhanced with EXIF reading
            return Photo(
                id=PhotoId(file_path.name),
                filename=Filename(file_path.name),
                creation_date=creation_date,
                modification_date=creation_date,  # Fallback
                size_bytes=FileSizeBytes(stat.st_size),
                format=PhotoFormat.JPEG,  # Default - could be improved
                photo_type=PhotoType.STANDARD,  # Default
                albums=frozenset(),  # Will be populated from iCloud
            )
        except Exception:
            return None


class FileSystemSymlinkManager:
    """Composition-based symlink manager implementation.
    
    Replaces inheritance with composition and pure functions.
    """
    
    def __init__(self, directory_structure: DirectoryStructure) -> None:
        self.structure = directory_structure
    
    def create_timeline_link(self, photo: Photo, source_path: DataPath, timeline_base: TimelinePath) -> Union["Ok[SymlinkPath]", "Err[str]"]:
        """Create a symlink in the Timeline hierarchy.
        
        Implementation of SymlinkManager protocol.
        """
        try:
            timeline_path = calculate_timeline_path(photo, timeline_base)
            timeline_dir = Path(timeline_path).parent
            
            # Create year/month directory structure
            timeline_dir.mkdir(parents=True, exist_ok=True)
            
            # Create symlink if it doesn't exist
            if not Path(timeline_path).exists():
                relative_source = calculate_relative_symlink_path(source_path, SymlinkPath(timeline_path))
                Path(timeline_path).symlink_to(relative_source)
            
            return Ok(SymlinkPath(timeline_path))
            
        except OSError as e:
            return Err(f"Failed to create timeline link for {photo.filename}: {e}")
        except Exception as e:
            return Err(f"Unexpected error creating timeline link: {e}")
    
    def create_library_links(self, photo: Photo, source_path: DataPath, library_base: LibraryPath) -> Union["Ok[FrozenSet[SymlinkPath]]", "Err[str]"]:
        """Create symlinks in the Library hierarchy for all albums containing the photo.
        
        Implementation of SymlinkManager protocol.
        """
        try:
            created_links = set()
            library_paths = calculate_library_paths(photo, library_base)
            
            for library_path in library_paths:
                library_dir = Path(library_path).parent
                
                # Create album directory
                library_dir.mkdir(parents=True, exist_ok=True)
                
                # Create symlink if it doesn't exist
                if not Path(library_path).exists():
                    relative_source = calculate_relative_symlink_path(source_path, SymlinkPath(library_path))
                    Path(library_path).symlink_to(relative_source)
                    created_links.add(SymlinkPath(library_path))
            
            return Ok(frozenset(created_links))
            
        except OSError as e:
            return Err(f"Failed to create library links for {photo.filename}: {e}")
        except Exception as e:
            return Err(f"Unexpected error creating library links: {e}")
    
    def remove_symlinks(self, photo: Photo, timeline_base: TimelinePath, library_base: LibraryPath) -> Union["Ok[int]", "Err[str]"]:
        """Remove all symlinks for a photo and return count removed.
        
        Implementation of SymlinkManager protocol.
        """
        try:
            count = 0
            
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
            
        except OSError as e:
            return Err(f"Failed to remove symlinks for {photo.filename}: {e}")
        except Exception as e:
            return Err(f"Unexpected error removing symlinks: {e}")


class ComposedFileSystemService:
    """High-level service that composes file system operations.
    
    This replaces the inheritance-based LocalPhotosLibrary pattern.
    """
    
    def __init__(self, directory_structure: DirectoryStructure) -> None:
        self.storage = FileSystemStorage(directory_structure)
        self.symlinks = FileSystemSymlinkManager(directory_structure)
        self.structure = directory_structure
    
    def get_storage(self) -> FileSystemStorage:
        """Get the storage implementation for dependency injection."""
        return self.storage
    
    def get_symlink_manager(self) -> FileSystemSymlinkManager:
        """Get the symlink manager for dependency injection."""
        return self.symlinks
    
    def ensure_directory_structure(self) -> Union["Ok[None]", "Err[str]"]:
        """Ensure all required directories exist."""
        try:
            directories = [
                self.structure.data_dir,
                self.structure.library_dir,
                self.structure.timeline_dir,
                self.structure.deleted_dir,
            ]
            
            for directory in directories:
                Path(directory).mkdir(parents=True, exist_ok=True)
            
            return Ok(None)
            
        except OSError as e:
            return Err(f"Failed to create directory structure: {e}")


# Result types
class Ok:
    """Success result."""
    def __init__(self, value):
        self.value = value

class Err:
    """Error result."""
    def __init__(self, error):
        self.error = error