# Database Refactoring Summary

## Overview

The database has been refactored to separate concerns and address the TODO items from the original `PhotoAssetRecord` class. The new design provides better separation of iCloud metadata, local file tracking, and sync status.

## Problems with Original Design

The original `PhotoAssetRecord` mixed three distinct concerns:

1. **iCloud metadata** (remote information): `asset_id`, `filename`, `asset_type`, `created_date`, etc.
2. **Local file system information**: `downloaded_versions`, `failed_versions`
3. **Sync status/todo tracking**: `sync_status`, `last_sync_date`, `available_versions`

This made it difficult to:
- Track individual file versions separately
- Map asset IDs to local filenames easily
- Store detailed version metadata from iCloud
- Handle different asset subtypes (live photos, bursts, etc.)

## New Database Structure

### 1. iCloud Assets Table (`icloud_assets`)
Stores remote metadata from iCloud:

```sql
CREATE TABLE icloud_assets (
    asset_id TEXT PRIMARY KEY,
    asset_id_base64 TEXT UNIQUE NOT NULL,  -- Base64 encoded for easier file mapping
    filename TEXT,
    asset_type TEXT,
    created_date DATETIME,
    added_date DATETIME,
    width INTEGER,
    height INTEGER,
    location_latitude REAL,
    location_longitude REAL,
    location_altitude REAL,
    subtype TEXT,  -- e.g., "live_photo", "burst", "hdr", "panorama"
    master_record TEXT,  -- JSON blob of iCloud master record
    asset_record TEXT,   -- JSON blob of iCloud asset record
    last_metadata_update DATETIME
);
```

### 2. Asset Versions Table (`asset_versions`)
Stores metadata for each available version of an asset:

```sql
CREATE TABLE asset_versions (
    asset_id TEXT,
    version_type TEXT,      -- e.g., "original", "adjusted", "alternative"
    version_size TEXT,      -- e.g., "original", "medium", "thumb"
    file_extension TEXT,
    file_size INTEGER,
    checksum TEXT,
    download_url TEXT,
    is_available BOOLEAN DEFAULT 1,
    last_checked DATETIME,
    PRIMARY KEY (asset_id, version_type, version_size),
    FOREIGN KEY (asset_id) REFERENCES icloud_assets(asset_id)
);
```

### 3. Local Files Table (`local_files`)
Tracks downloaded files on the local filesystem:

```sql
CREATE TABLE local_files (
    asset_id TEXT,
    version_type TEXT,
    local_filename TEXT,    -- Actual filename on disk
    file_path TEXT,         -- Full path relative to base directory
    file_size INTEGER,
    download_date DATETIME,
    checksum TEXT,
    is_complete BOOLEAN DEFAULT 1,
    error_message TEXT,
    PRIMARY KEY (asset_id, version_type),
    FOREIGN KEY (asset_id) REFERENCES icloud_assets(asset_id)
);
```

### 4. Sync Status Table (`sync_status`)
Tracks sync progress and status:

```sql
CREATE TABLE sync_status (
    asset_id TEXT PRIMARY KEY,
    sync_status TEXT DEFAULT 'pending',  -- pending, metadata_processed, downloading, completed, failed
    last_sync_date DATETIME,
    retry_count INTEGER DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY (asset_id) REFERENCES icloud_assets(asset_id)
);
```

## Key Improvements

### 1. Base64 Asset ID Mapping
- **Problem**: Original asset IDs contain special characters that make file naming difficult
- **Solution**: Store base64-encoded asset IDs for easier file mapping
- **Benefit**: Cleaner filenames like `3453453-original.jpg` instead of complex IDs

### 2. Explicit Local File Tracking
- **Problem**: Original design only tracked version lists, not actual files
- **Solution**: Separate table for local files with actual filenames and paths
- **Benefit**: Can track individual file downloads, errors, and file system state

### 3. Detailed Version Metadata
- **Problem**: Limited version information stored
- **Solution**: Dedicated table for version metadata with file sizes, extensions, URLs
- **Benefit**: Better download planning and verification

### 4. Asset Subtype Support
- **Problem**: No way to distinguish live photos, bursts, HDR, panoramas
- **Solution**: `subtype` field in iCloud assets table
- **Benefit**: Better organization and filtering capabilities

### 5. Improved Sync Status Tracking
- **Problem**: Simple status string with limited error information
- **Solution**: Dedicated sync status table with retry counts and error messages
- **Benefit**: Better error handling and recovery

## Migration Strategy

The refactored database includes legacy compatibility methods:

```python
# Legacy method for transition
def get_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
    """Legacy method to get asset data in old format."""
    # Combines data from all tables into old format
```

This allows existing code to continue working while the new architecture is implemented.

## File Naming Convention

The new structure supports the file naming convention from the TODO:

- **Base64 encoded asset ID**: `3453453` (from original `AZN7i4naNL7x5Jws70SFPH6r5j+p`)
- **Version suffix**: `-original`, `-adjusted`, `-alternative`
- **File extension**: `.jpg`, `.heic`, `.mov`
- **Example**: `3453453-original.jpg`, `3453453-original.mov` (live photo video)

## Benefits

1. **Better Separation of Concerns**: Each table has a single responsibility
2. **Improved Performance**: Indexed queries for common operations
3. **Enhanced Error Handling**: Detailed error tracking per file
4. **Future Extensibility**: Easy to add new fields and tables
5. **Cleaner File Organization**: Base64 IDs make file naming consistent
6. **Better Metadata Tracking**: Full version information from iCloud
7. **Subtype Support**: Proper handling of live photos, bursts, etc.

## Next Steps

1. Update existing code to use the new database methods
2. Implement file manager integration with the new structure
3. Add migration scripts for existing databases
4. Update tests to use the new data classes
5. Implement the download manager with the new architecture 