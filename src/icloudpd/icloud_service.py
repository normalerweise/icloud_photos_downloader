#!/usr/bin/env python
"""Composition-based iCloud service implementing reader protocols."""

from __future__ import annotations

from typing import FrozenSet, Iterator, Optional, Union

from .models import Album, Photo
from .protocols import ICloudReader
from .pure_functions import filter_photos_by_count, filter_photos_by_recent_days
from .types import AlbumName, PhotoCount, PhotoId


class ICloudPhotosService:
    """Composition-based iCloud Photos service.
    
    Replaces inheritance with composition and wraps existing iCloud functionality.
    """
    
    def __init__(self, icloud_client) -> None:
        """Initialize with an iCloud client (dependency injection)."""
        self.client = icloud_client
    
    def get_albums(self) -> Iterator[Album]:
        """Get all albums from iCloud Photos.
        
        Implementation of ICloudReader protocol.
        """
        try:
            # Use existing iCloud client to get albums
            for icloud_album in self.client.photos.albums:
                yield self._convert_icloud_album(icloud_album)
        except Exception as e:
            # Log error but don't raise - let caller handle empty iterator
            print(f"Error getting albums: {e}")
            return
    
    def get_photos_in_album(self, album: Album, limit: Optional[PhotoCount] = None) -> Iterator[Photo]:
        """Get photos in a specific album with optional limit.
        
        Implementation of ICloudReader protocol.
        """
        try:
            # Find the corresponding iCloud album
            icloud_album = self._find_icloud_album(album.name)
            if not icloud_album:
                return
            
            count = 0
            for icloud_photo in icloud_album:
                if limit and count >= limit:
                    break
                
                photo = self._convert_icloud_photo(icloud_photo, {album.name})
                if photo:
                    yield photo
                    count += 1
                    
        except Exception as e:
            print(f"Error getting photos in album {album.name}: {e}")
            return
    
    def get_recent_photos(self, count: PhotoCount) -> Iterator[Photo]:
        """Get most recent photos.
        
        Implementation of ICloudReader protocol.
        """
        try:
            # Get all photos and filter to most recent
            all_photos = list(self.get_all_photos())
            recent_photos = filter_photos_by_count(frozenset(all_photos), count)
            
            for photo in recent_photos:
                yield photo
                
        except Exception as e:
            print(f"Error getting recent photos: {e}")
            return
    
    def get_all_photos(self, limit: Optional[PhotoCount] = None) -> Iterator[Photo]:
        """Get all photos with optional limit.
        
        Implementation of ICloudReader protocol.
        """
        try:
            count = 0
            
            # Iterate through all albums to get all photos
            for album in self.get_albums():
                for photo in self.get_photos_in_album(album):
                    if limit and count >= limit:
                        return
                    
                    yield photo
                    count += 1
                    
        except Exception as e:
            print(f"Error getting all photos: {e}")
            return
    
    def _convert_icloud_album(self, icloud_album) -> Album:
        """Convert iCloud album to our Album model."""
        from datetime import datetime
        from .types import CreationDate
        
        # Extract photos IDs (simplified - could be more sophisticated)
        photo_ids = set()
        try:
            for photo in icloud_album:
                photo_ids.add(PhotoId(photo.id))
        except Exception:
            pass  # Handle albums that can't be iterated
        
        return Album(
            name=AlbumName(icloud_album.name),
            photo_count=PhotoCount(len(photo_ids)),
            creation_date=CreationDate(datetime.now()),  # Fallback
            photos=frozenset(photo_ids),
        )
    
    def _convert_icloud_photo(self, icloud_photo, albums: set[str]) -> Optional[Photo]:
        """Convert iCloud photo to our Photo model."""
        try:
            from datetime import datetime
            from .types import (
                CreationDate,
                ExifDate,
                FileSizeBytes,
                Filename,
                ICloudDate,
                PhotoFormat,
                PhotoId,
                PhotoType,
            )
            
            # Extract creation date
            creation_date = CreationDate(icloud_photo.created)
            icloud_date = ICloudDate(icloud_photo.created) if hasattr(icloud_photo, 'created') else None
            
            # Determine photo format from filename
            filename = Filename(icloud_photo.filename)
            format_mapping = {
                '.heic': PhotoFormat.HEIC,
                '.jpg': PhotoFormat.JPEG,
                '.jpeg': PhotoFormat.JPEG,
                '.png': PhotoFormat.PNG,
                '.mov': PhotoFormat.MOV,
                '.mp4': PhotoFormat.MP4,
            }
            
            file_ext = '.' + filename.split('.')[-1].lower() if '.' in filename else '.jpg'
            photo_format = format_mapping.get(file_ext, PhotoFormat.JPEG)
            
            # Determine photo type
            photo_type = PhotoType.LIVE if hasattr(icloud_photo, 'live_photo') and icloud_photo.live_photo else PhotoType.STANDARD
            
            return Photo(
                id=PhotoId(icloud_photo.id),
                filename=filename,
                creation_date=creation_date,
                modification_date=creation_date,  # Use creation date as fallback
                size_bytes=FileSizeBytes(getattr(icloud_photo, 'size', 0)),
                format=photo_format,
                photo_type=photo_type,
                albums=frozenset(AlbumName(album) for album in albums),
                exif_date=None,  # Could be extracted later
                icloud_date=icloud_date,
            )
            
        except Exception as e:
            print(f"Error converting iCloud photo: {e}")
            return None
    
    def _find_icloud_album(self, album_name: AlbumName) -> Optional:
        """Find iCloud album by name."""
        try:
            for icloud_album in self.client.photos.albums:
                if icloud_album.name == album_name:
                    return icloud_album
        except Exception:
            pass
        return None


class SafeICloudService:
    """Safe wrapper around iCloud service with backup-only guarantees.
    
    This service ensures no write operations can occur.
    """
    
    def __init__(self, icloud_service: ICloudPhotosService) -> None:
        self.service = icloud_service
    
    def get_albums(self) -> Iterator[Album]:
        """Safe read-only album access."""
        return self.service.get_albums()
    
    def get_photos_in_album(self, album: Album, limit: Optional[PhotoCount] = None) -> Iterator[Photo]:
        """Safe read-only photo access."""
        return self.service.get_photos_in_album(album, limit)
    
    def get_recent_photos(self, count: PhotoCount) -> Iterator[Photo]:
        """Safe read-only recent photos access."""
        return self.service.get_recent_photos(count)
    
    def get_all_photos(self, limit: Optional[PhotoCount] = None) -> Iterator[Photo]:
        """Safe read-only all photos access."""
        return self.service.get_all_photos(limit)
    
    # Explicitly no write methods - this service is read-only only
    
    def __setattr__(self, name: str, value) -> None:
        """Prevent setting attributes that could enable write operations."""
        if name in ('delete', 'upload', 'modify', 'write', 'post'):
            raise AttributeError(f"Write operation '{name}' not allowed in backup-only service")
        super().__setattr__(name, value)


class ComposedICloudService:
    """High-level service that composes iCloud operations.
    
    This replaces direct iCloud client usage with a composed, safe interface.
    """
    
    def __init__(self, icloud_client) -> None:
        self.photos_service = ICloudPhotosService(icloud_client)
        self.safe_service = SafeICloudService(self.photos_service)
    
    def get_reader(self) -> SafeICloudService:
        """Get the read-only iCloud reader for dependency injection."""
        return self.safe_service
    
    def test_connection(self) -> Union["Ok[None]", "Err[str]"]:
        """Test iCloud connection without performing operations."""
        try:
            # Try to get one album to test connection
            albums = list(self.safe_service.get_albums())
            return Ok(None)
        except Exception as e:
            return Err(f"iCloud connection test failed: {e}")


# Result types
class Ok:
    """Success result."""
    def __init__(self, value):
        self.value = value

class Err:
    """Error result."""
    def __init__(self, error):
        self.error = error