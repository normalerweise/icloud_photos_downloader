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
  |  metadata  <---maps---  PhotoAssetRecordMapper
  |  collection                         |
  |     |                               v
  |     v                          PhotoAsset (iCloud API)
  |  PhotoDatabase
  |     |
  |  Phase 2
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
  v     v
ProgressReporter
```

## Two-Phase Sync

The sync process is split into two sequential phases:

### Phase 1: Metadata Collection

Iterates through iCloud photos (filtered by the selected strategy), maps each `PhotoAsset` to an `ICloudAssetRecord`, and persists it to SQLite. For each asset, determines which versions need downloading by comparing available versions against what's already on disk and in the database.

This phase is lightweight -- no file I/O beyond the database. It builds a complete picture of what needs to happen before any downloads begin.

### Phase 2: Asset Download

Queries the database for assets with `metadata_processed` status. For each, resolves the version strings back to `VersionSize` enums, then hands them to `DownloadManager` for parallel download. Results (success or failure) are recorded in the database.

This separation means:
- Phase 1 can complete even if the network is slow
- Phase 2 can be re-run without re-scanning iCloud
- Progress is tracked at version granularity

## Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| `SyncManager` | Orchestrates the two-phase sync, wires components together |
| `PhotoDatabase` | SQLite persistence for metadata, local files, and sync status |
| `DownloadManager` | Parallel downloads with retry and exponential backoff |
| `FileManager` | Atomic file writes, path generation, cleanup |
| `PhotoAssetRecordMapper` | Maps iCloud `PhotoAsset` objects to database records |
| `PhotosToSync` (strategies) | Filters which photos to sync (recent, since date, all) |
| `ProgressReporter` | Displays progress bars and statistics |

## Directory Layout

```
<download_directory>/
  _metadata.sqlite          # SQLite database
  _data/
    <base64_id>-original.jpg
    <base64_id>-originalVideo.mov
    <base64_id>-adjusted.jpg
    ...
```

Files are named using URL-safe base64-encoded asset IDs to guarantee uniqueness and filesystem safety. The version type (original, adjusted, alternative, originalVideo) is appended as a suffix.

## What Gets Downloaded

The constant `DOWNLOAD_VERSIONS` controls which versions are fetched:

| Version | Enum | Description |
|---------|------|-------------|
| `original` | `AssetVersionSize.ORIGINAL` | Full-quality original photo |
| `originalVideo` | `LivePhotoVersionSize.ORIGINAL` | Live photo video companion |
| `adjusted` | `AssetVersionSize.ADJUSTED` | Edited version (if available) |
| `alternative` | `AssetVersionSize.ALTERNATIVE` | Alternative version (RAW if present) |

Medium and thumbnail versions are deliberately skipped.

## Authentication Flow

The new architecture does not manage authentication itself. It relies on the existing `authenticator()` in `base.py` which produces an authenticated `PyiCloudService` instance. The `PhotoAsset.download(url)` method uses the authenticated session internally, so `DownloadManager` never needs to handle cookies or tokens directly.

## Key Design Decisions

1. **Database over filesystem scanning** -- Instead of scanning the filesystem to determine what's been downloaded (fragile, slow for large libraries), we track state explicitly in SQLite.

2. **Version-level granularity** -- Each version of each asset has its own sync status. A photo can have its original downloaded but its live photo video still pending.

3. **Base64 filenames** -- Asset IDs from iCloud contain characters like `+`, `/`, `=` that are problematic on some filesystems. Base64 encoding (URL-safe, no padding) solves this while keeping filenames deterministic.

4. **Authenticated download via PhotoAsset** -- Rather than extracting cookies and making raw HTTP requests, we pass the `PhotoAsset` object through to the download layer. Its `.download(url)` method uses the already-authenticated `PyiCloudSession`.

5. **Strategy pattern for filtering** -- The `PhotosToSync` abstraction decouples "which photos" from "how to download." Adding a new filter (e.g., by album, by media type) means adding a new strategy class.
