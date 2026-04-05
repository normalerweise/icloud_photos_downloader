# Architecture Overview

## Problem Statement

The legacy icloudpd download flow was monolithic -- authentication, iteration, download, file naming, EXIF handling, and state tracking were interleaved in a single loop inside `base.py`. This made it difficult to:

- Resume interrupted downloads
- Track what has been downloaded across sessions
- Handle failures gracefully
- Test individual components in isolation
- Reason about the download lifecycle

## Design Goals

1. **Resumability** -- Persist download state in SQLite so interrupted syncs can continue where they left off.
2. **Modularity** -- Separate concerns into focused components (database, download, file management, orchestration).
3. **Reliability** -- Retry failed downloads with exponential backoff; track errors per-version.
4. **Simplicity** -- Remove legacy CLI options that create combinatorial complexity (`--size`, `--skip-videos`, `--folder-structure`, `--file-match-policy`, etc.). The algorithm decides what to download.

## High-Level Architecture

```
CLI (base.py)
  |
  v
Authentication (pyicloud_ipd)
  |
  v
SyncManager  ----orchestrates--->  PhotosToSync (strategy)
  |                                     |
  |  Phase 1                            v
  |  asset metadata  <---maps---  PhotoAssetRecordMapper
  |  collection                         |
  |     |                               v
  |     v                          PhotoAsset (iCloud API)
  |  PhotoDatabase
  |     |
  |  Phase 2                       PhotoLibrary
  |  album & folder  <---reads---  .albums, .folders
  |  sync                               |
  |     |                               v
  |     v                          iCloud CloudKit API
  |  PhotoDatabase
  |     |
  |  Phase 3
  |  reconcile         (DB tombstone only -- no file I/O)
  |  deletions
  |     |
  |     v
  |  PhotoDatabase
  |     |
  |  Phase 4
  |  download
  |     |
  |     v
  |  DownloadManager ---parallel--->  PhotoAsset.download(url)
  |     |                                  |
  |     v                                  v
  |  FileManager  <---stream---  authenticated Response
  |     |
  |     v
  |  _data/  (files on disk)
  |     |
  |     v
  |  PhotoDatabase (record results)
  |     |
  |  Phase 5
  |  filesystem       (symlinks from Library/ and Albums/ into _data/)
  |  sync
  |     |
  |     v
  |  FilesystemSync
  |     |
  v     v
ProgressReporter
```

## Five-Phase Sync

The sync process is split into five sequential phases. Phases 1-3 are database-only (no file I/O beyond SQLite). Phase 4 downloads files. Phase 5 creates the browsable filesystem structure.

### Phase 1: Asset Metadata Collection

Iterates through iCloud photos (filtered by the selected strategy), maps each `PhotoAsset` to an `ICloudAssetRecord`, and persists it to SQLite. For each asset, determines which versions need downloading by comparing available versions against what's already on disk and in the database.

This phase is lightweight -- no file I/O beyond the database. It builds a complete picture of what needs to happen before any downloads begin.

### Phase 2: Album & Folder Sync

Reads the iCloud folder/album hierarchy via `PhotoLibrary.folders` and `PhotoLibrary.albums`. Recursively upserts folders and albums into the database, and replaces album-asset membership. Returns synced IDs for deletion detection in Phase 3.

Only runs when `photo_library` is available. Smart albums have metadata recorded but no membership synced (they don't expose asset lists via the API).

### Phase 3: Reconcile Deletions

Compares database state against what was seen in Phases 1-2. Assets, folders, and albums present in the database but absent from iCloud are tombstoned (soft-deleted with `deleted = TRUE` and `deletion_detected_on` timestamp).

This phase is strictly DB-only -- no files are deleted from disk. A future Phase 5 enhancement will handle file cleanup based on tombstone state.

Only runs when the sync strategy covers the full library (`covers_full_library`), since partial syncs can't distinguish "deleted" from "not included in filter."

### Phase 4: Asset Download

Queries the database for assets with `metadata_processed` status. For each, resolves the version strings back to `VersionSize` enums, then hands them to `DownloadManager` for parallel download. Results (success or failure) are recorded in the database.

This separation means:
- Phases 1-3 can complete even if the network is slow
- Phase 4 can be re-run without re-scanning iCloud
- Progress is tracked at version granularity

### Phase 5: Filesystem Sync

Projects database state onto the filesystem as a browsable symlink structure:

```
<download_directory>/
  Library/
    2024/
      01/
        20240115_143022_IMG_7409.JPG -> ../../_data/QVlrNlkr...-original.jpg
  Albums/
    Vacation/
      20240115_143022_IMG_7409.JPG -> ../../_data/QVlrNlkr...-original.jpg
    Travel/
      Japan/
        ...
```

Uses delta convergence: compares desired symlinks (computed from DB) against existing symlinks on disk, and only creates/removes/updates what changed. See `design_docs/filesystem_sync.md` for details.

## Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| `SyncManager` | Orchestrates the five-phase sync, wires components together |
| `PhotoDatabase` | SQLite persistence for metadata, local files, sync status, albums, folders |
| `DownloadManager` | Parallel downloads with retry and exponential backoff |
| `FileManager` | Atomic file writes to `_data/`, path generation, cleanup |
| `FilesystemSync` | Phase 5: create/update/remove symlinks in `Library/` and `Albums/` |
| `PhotoAssetRecordMapper` | Maps iCloud `PhotoAsset` objects to database records |
| `PhotosToSync` (strategies) | Filters which photos to sync (recent, since date, all) |
| `ProgressReporter` | Displays progress bars and statistics |

## Directory Layout

```
<download_directory>/
  _metadata.sqlite          # SQLite database
  _data/                    # Flat storage (base64-encoded filenames)
    <base64_id>-original.jpg
    <base64_id>-live_photo.mov
    <base64_id>-adjusted.jpg
    ...
  Library/                  # Symlinks organized by date
    2024/
      01/
        20240115_143022_IMG_7409.JPG -> ../../_data/...
      02/
        ...
  Albums/                   # Symlinks mirroring iCloud album/folder hierarchy
    AlbumName/
      20240115_143022_IMG_7409.JPG -> ../../_data/...
    FolderName/
      NestedAlbum/
        ...
```

Files in `_data/` are named using URL-safe base64-encoded asset IDs to guarantee uniqueness and filesystem safety. The version type (original, adjusted, alternative, live_photo) is appended as a suffix.

`Library/` and `Albums/` contain only symlinks pointing into `_data/`. They are fully derived from the database and can be regenerated at any time.

## What Gets Downloaded

The constant `DOWNLOAD_VERSIONS` controls which versions are fetched:

| Version | Enum | Description |
|---------|------|-------------|
| `original` | `AssetVersionSize.ORIGINAL` | Full-quality original photo |
| `live_photo` | `LivePhotoVersionSize.ORIGINAL` | Live photo video companion |
| `adjusted` | `AssetVersionSize.ADJUSTED` | Edited version (if available) |
| `alternative` | `AssetVersionSize.ALTERNATIVE` | Alternative version (RAW if present) |

Medium and thumbnail versions are deliberately skipped.

## Authentication Flow

The new architecture does not manage authentication itself. It relies on the existing `authenticator()` in `base.py` which produces an authenticated `PyiCloudService` instance. The `PhotoAsset.download(url)` method uses the authenticated session internally, so `DownloadManager` never needs to handle cookies or tokens directly.

## Key Design Decisions

1. **Database over filesystem scanning** -- Instead of scanning the filesystem to determine what's been downloaded (fragile, slow for large libraries), we track state explicitly in SQLite.

2. **Version-level granularity** -- Each version of each asset has its own sync status. A photo can have its original downloaded but its live photo video still pending.

3. **Base64 filenames in `_data/`** -- Asset IDs from iCloud contain characters like `+`, `/`, `=` that are problematic on some filesystems. Base64 encoding (URL-safe, no padding) solves this while keeping filenames deterministic.

4. **Symlink layer for browsability** -- `_data/` is not meant for human consumption. `Library/` and `Albums/` provide a browsable view via relative symlinks, fully derived from database state.

5. **Authenticated download via PhotoAsset** -- Rather than extracting cookies and making raw HTTP requests, we pass the `PhotoAsset` object through to the download layer. Its `.download(url)` method uses the already-authenticated `PyiCloudSession`.

6. **Strategy pattern for filtering** -- The `PhotosToSync` abstraction decouples "which photos" from "how to download." Adding a new filter (e.g., by album, by media type) means adding a new strategy class.

7. **DB-only phases 1-3** -- Metadata, album sync, and deletion detection are pure database operations. No files are created or deleted until Phase 4 (download) and Phase 5 (symlinks). This makes the first three phases fast, safe, and idempotent.

8. **Tombstone deletions** -- Deleted assets/folders/albums are soft-deleted (marked with `deleted = TRUE`) rather than removed from the database. This preserves history and enables future features like undo or deletion reports.
