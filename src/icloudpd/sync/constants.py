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
DOWNLOAD_VERSIONS: tuple[AssetVersionSize | LivePhotoVersionSize, ...] = (
    AssetVersionSize.ORIGINAL,
    LivePhotoVersionSize.ORIGINAL,
    AssetVersionSize.ADJUSTED,
    AssetVersionSize.ALTERNATIVE,
)

# Supported file extensions for live photos
LIVE_PHOTO_EXTENSIONS: tuple[str, ...] = (".mov", ".mp4")

# Filesystem sync directories
LIBRARY_DIRECTORY = "Library"
ALBUMS_DIRECTORY = "Albums"
