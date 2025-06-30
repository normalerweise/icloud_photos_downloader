"""Constants for the new download architecture."""

# Download configuration
from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize


MAX_CONCURRENT_DOWNLOADS = 5
DOWNLOAD_TIMEOUT = 30  # seconds
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2  # seconds

# Database configuration
DATABASE_FILENAME = "_metadata.sqlite"

# File naming
DATA_DIRECTORY = "_data"

# Version types to download
DOWNLOAD_VERSIONS = [
    AssetVersionSize.ORIGINAL.value,
    LivePhotoVersionSize.ORIGINAL.value,
    AssetVersionSize.ADJUSTED.value,
    AssetVersionSize.ALTERNATIVE.value,
]

# Supported file extensions for live photos
LIVE_PHOTO_EXTENSIONS = [".mov", ".mp4"]
