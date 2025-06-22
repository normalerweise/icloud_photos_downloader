#!/usr/bin/env python
"""Domain-specific types for type-safe iCloud Photos Downloader."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal, NewType

# File System Types - Replace plain strings with domain-specific types
PhotoId = NewType("PhotoId", str)
AlbumName = NewType("AlbumName", str)
Filename = NewType("Filename", str)
FileExtension = NewType("FileExtension", str)
PhotoSize = NewType("PhotoSize", str)  # "original", "medium", "thumb"

# Path Types - Replace Path with more specific types
DataPath = NewType("DataPath", Path)
LibraryPath = NewType("LibraryPath", Path)
TimelinePath = NewType("TimelinePath", Path)
SymlinkPath = NewType("SymlinkPath", Path)
ConfigPath = NewType("ConfigPath", Path)

# Measurement Types - Replace plain ints with domain-specific measurements
FileSizeBytes = NewType("FileSizeBytes", int)
DownloadProgressPercent = NewType("DownloadProgressPercent", int)
PhotoCount = NewType("PhotoCount", int)
TimestampSeconds = NewType("TimestampSeconds", int)

# Date and Time Types - Specific date types for different purposes
CreationDate = NewType("CreationDate", datetime)
ModificationDate = NewType("ModificationDate", datetime)
ExifDate = NewType("ExifDate", datetime)
ICloudDate = NewType("ICloudDate", datetime)

# Content Type Enums
class PhotoFormat(Enum):
    """Supported photo and video formats."""
    HEIC = "heic"
    JPEG = "jpg"
    PNG = "png"
    RAW = "raw"
    MOV = "mov"  # Live photo video
    MP4 = "mp4"  # Video
    TIFF = "tiff"


class PhotoType(Enum):
    """Types of photos/media in iCloud."""
    STANDARD = "standard"
    LIVE = "live"
    RAW_PLUS_JPEG = "raw_plus_jpeg"
    BURST = "burst"
    VIDEO = "video"


class SyncMode(Enum):
    """Sync operation modes."""
    DOWNLOAD_ONLY = "download_only"
    SYNC = "sync"
    BACKUP = "backup"


# Literal Types for specific string values
SizeOption = Literal["original", "medium", "thumb"]
LogLevel = Literal["debug", "info", "warning", "error"]

# Error Types
class ErrorCode(Enum):
    """Standard error codes for the application."""
    NETWORK_ERROR = "network_error"
    AUTHENTICATION_ERROR = "auth_error"
    FILE_SYSTEM_ERROR = "filesystem_error"
    VALIDATION_ERROR = "validation_error"
    ICLOUD_API_ERROR = "icloud_api_error"
    CONFIGURATION_ERROR = "config_error"