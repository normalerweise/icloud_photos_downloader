# Phase 5: Filesystem Sync

## Problem

Phases 1-4 store downloaded files in `_data/` using base64-encoded filenames (e.g., `QVlrNlkrdEVTUg-original.jpg`). This is ideal for internal management but not for human browsing. Users expect a structure similar to iCloud Photos: photos organized by date, and albums grouped in folders.

## Solution

Phase 5 creates a **symlink layer** that projects the database state onto the filesystem as a browsable directory structure. All symlinks point into `_data/` using relative paths. The source of truth remains the database -- `Library/` and `Albums/` are fully derived views.

## Directory Structure

```
<base>/
├── _data/                              # Untouched by Phase 5
├── _metadata.sqlite                    # Untouched by Phase 5
├── Library/                            # Date-organized view
│   ├── 2024/
│   │   ├── 01/
│   │   │   ├── 20240115_143022_IMG_7409.JPG -> ../../../_data/QVlrNlkr...-original.jpg
│   │   │   ├── 20240115_143022_IMG_7409.mov -> ../../../_data/QVlrNlkr...-live_photo.mov
│   │   │   └── 20240115_143022_IMG_7409-adjusted.JPG -> ../../../_data/QVlrNlkr...-adjusted.jpg
│   │   └── 02/
│   │       └── ...
│   └── Unknown/
│       └── 00/
│           └── ...                     # Assets with no date
├── Albums/                             # Album/folder hierarchy view
│   ├── Vacation/
│   │   └── 20240115_143022_IMG_7409.JPG -> ../../_data/...
│   ├── WhatsApp/
│   │   └── ...
│   └── Travel/                         # iCloud folder
│       └── Japan/                      # Album inside folder
│           └── ...
```

## Symlink Naming Convention

### Datetime Prefix

All symlink names are prefixed with `YYYYMMDD_HHMMSS_` derived from the asset's `created_date`. This ensures chronological sort order in file browsers.

```
20240115_143022_IMG_7409.JPG
^^^^^^^^ ^^^^^^ ^^^^^^^^^^^
  date    time   original filename
```

Fallback chain: `created_date` -> `added_date` -> `00000000_000000`.

### Version Suffixes

| Version | Symlink Name |
|---------|-------------|
| `original` | `20240115_143022_IMG_7409.JPG` (bare filename) |
| `adjusted` | `20240115_143022_IMG_7409-adjusted.JPG` |
| `alternative` | `20240115_143022_IMG_7409-alternative.JPG` |
| `live_photo` | `20240115_143022_IMG_7409.mov` (extension from local file) |

### Collision Disambiguation

When multiple assets produce the same symlink name in the same directory:

1. Sort colliders by `asset_id` for determinism
2. First occurrence keeps the clean name
3. Subsequent occurrences get a `_XXXXX` suffix (5-char base64 of asset_id)

Example: Two assets both named `IMG_7409.JPG` taken at the same second:
- `20240115_143022_IMG_7409.JPG` (first by asset_id sort)
- `20240115_143022_IMG_7409_QVlrN.JPG` (second, with disambiguator)

## Delta Convergence Algorithm

Phase 5 does **not** rebuild from scratch each run. Instead, it computes the desired state and converges existing symlinks toward it:

```
1. COMPUTE desired state from database
   └── dict[symlink_path -> relative_target]

2. SCAN existing symlinks on disk
   └── Walk Library/ and Albums/, record each symlink and its target

3. CONVERGE
   ├── desired - existing  = CREATE  (new symlinks)
   ├── existing - desired  = REMOVE  (stale symlinks)
   └── desired ∩ existing  = UPDATE  (if target changed) or UNCHANGED

4. PRUNE empty directories (bottom-up rmdir)
```

This approach:
- Avoids unnecessary filesystem churn on large libraries
- Is idempotent (running twice produces no changes)
- Handles renames, album membership changes, and deletions gracefully

## Library Tree Computation

1. Fetch all downloaded assets from DB (`get_all_downloaded_assets()` -- single JOIN query)
2. For each `(asset, local_files)` pair:
   - Parse `created_date` -> `(year, month)` for directory path
   - For each local file, compute symlink name
3. Group by `(year, month)` directory
4. Disambiguate collisions within each directory
5. Compute relative target paths

## Albums Tree Computation

1. Fetch all non-deleted albums and folders from DB
2. Build folder map: `{folder_id -> FolderRecord}`
3. For each user album (smart albums skipped):
   - Resolve folder path by walking parent chain (e.g., `Travel/Japan`)
   - Compute album directory: `Albums/{folder_path}/{album_name}`
   - For each asset in album, compute symlink name
4. Disambiguate collisions within each album directory

### Folder Path Resolution

Walks the parent chain from a folder to the root:

```python
folder_id="f2" (Inner) -> parent="f1" (Outer) -> parent=None
Result: Path("Outer/Inner")
```

Caps depth at 50 to prevent infinite loops from malformed data.

## Filename Sanitization

Album and folder names may contain characters unsafe for filesystems. These are replaced with `_`:

```
/ \ : * ? " < > |  ->  _
```

Leading/trailing dots and spaces are stripped.

## Smart Albums

Smart albums (Favorites, Videos, Panoramas, etc.) are **skipped** in Phase 5. They have `album_type == "smart"` and Phase 2 does not sync their membership (the iCloud API doesn't expose smart album asset lists in the same way). This is intentional and can be revisited if the API exposure changes.

## Implementation

### FilesystemSync (`sync/filesystem_sync.py`)

Single class, no network dependencies. Injected into `SyncManager` and invoked during `_phase5_filesystem_sync()`.

### Database Dependencies

| Method | Purpose |
|--------|---------|
| `get_all_downloaded_assets()` | All non-deleted assets with local files (JOIN query) |
| `get_all_non_deleted_albums()` | Full AlbumRecord objects |
| `get_all_non_deleted_folders()` | Full FolderRecord objects |
| `get_album_assets(album_id)` | Asset IDs for each album |

## Future Enhancements

- **Deleted file cleanup**: Use tombstoned assets in DB to remove corresponding files from `_data/` and symlinks
- **Smart album support**: If the API allows, sync Favorites/Videos/etc. membership
- **Hardlink option**: For systems where symlinks are problematic (e.g., Windows without Developer Mode)
- **Custom folder structure**: Allow user-configurable organization (by year only, by album only, etc.)
