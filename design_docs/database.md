# Database Design

## Overview

The sync state is persisted in a SQLite database (`_metadata.sqlite`) stored in the download root directory. The schema uses three tables to separate concerns: remote metadata, local file tracking, and sync progress.

## Schema

### `icloud_assets` -- Remote Metadata

Stores metadata fetched from the iCloud API. One row per asset.

```sql
CREATE TABLE icloud_assets (
    asset_id                TEXT PRIMARY KEY,
    filename                TEXT,
    asset_type              TEXT,           -- "image" | "movie"
    created_date            DATETIME,
    added_date              DATETIME,
    width                   INTEGER,
    height                  INTEGER,
    asset_subtype           TEXT,           -- "live_photo" | "burst" | "hdr" | "panorama" | NULL
    asset_versions          TEXT,           -- JSON: version metadata dict
    master_record           TEXT,           -- JSON: raw iCloud master record
    asset_record            TEXT,           -- JSON: raw iCloud asset record
    last_metadata_update    DATETIME,
    metadata_inserted_date  DATETIME
);
```

The `asset_versions` field stores a JSON dictionary keyed by version type string (e.g., `"original"`, `"adjusted"`, `"originalVideo"`). Each value contains:

```json
{
  "original": {
    "filename": "IMG_7409.JPG",
    "size": 4500000,
    "url": "https://cvws.icloud-content.com/...",
    "type": "public.jpeg",
    "file_extension": "JPG"
  }
}
```

The `master_record` and `asset_record` fields store the raw iCloud API responses as JSON blobs for debugging and future use.

### `local_files` -- Downloaded Files

Tracks files that have been successfully written to disk. One row per asset-version pair.

```sql
CREATE TABLE local_files (
    asset_id        TEXT,
    version_type    TEXT,           -- "original" | "adjusted" | "alternative" | "originalVideo"
    local_filename  TEXT,           -- actual filename on disk
    file_path       TEXT,           -- path relative to base directory
    file_size       INTEGER,
    download_date   DATETIME,
    checksum        TEXT,           -- reserved for future integrity checks
    PRIMARY KEY (asset_id, version_type),
    FOREIGN KEY (asset_id) REFERENCES icloud_assets(asset_id)
);
```

### `sync_status` -- Sync Progress

Tracks the download lifecycle for each asset-version. One row per asset-version pair.

```sql
CREATE TABLE sync_status (
    asset_id        TEXT,
    version_type    TEXT,
    sync_status     TEXT DEFAULT 'pending',  -- see state machine below
    last_sync_date  DATETIME,
    retry_count     INTEGER DEFAULT 0,
    error_message   TEXT,
    PRIMARY KEY (asset_id, version_type),
    FOREIGN KEY (asset_id) REFERENCES icloud_assets(asset_id)
);
```

### Indexes

```sql
CREATE INDEX idx_sync_status ON sync_status(sync_status);
CREATE INDEX idx_local_files_asset_id ON local_files(asset_id);
```

## State Machine

Each asset-version progresses through these states:

```
metadata_processed  -->  downloading  -->  completed
        |                     |
        v                     v
      failed               failed
```

| State | Meaning | Set By |
|-------|---------|--------|
| `metadata_processed` | Metadata collected, ready for download | Phase 1 |
| `downloading` | Download in progress | Phase 2 (before download) |
| `completed` | File downloaded and recorded | Phase 2 (after success) |
| `failed` | Download or metadata processing failed | Either phase |

The `pending` state exists in the dataclass default but is not used in the current flow. Phase 1 sets versions directly to `metadata_processed` (ready for download) or `completed` (already on disk).

## Upsert Strategy

### icloud_assets

Uses `INSERT ... ON CONFLICT DO UPDATE` with `RETURNING` to atomically insert or update metadata. The `metadata_inserted_date` field is compared to distinguish inserts from updates:
- If the returned `metadata_inserted_date` matches the one we set, it was an INSERT.
- Otherwise, it was an UPDATE (the existing `metadata_inserted_date` was preserved).

### local_files and sync_status

Use `INSERT OR REPLACE` for idempotent upserts. The composite primary key `(asset_id, version_type)` ensures one record per version.

## Querying for Work

### Assets needing download

```sql
SELECT DISTINCT ss.asset_id
FROM sync_status ss
LEFT JOIN local_files lf
  ON ss.asset_id = lf.asset_id AND ss.version_type = lf.version_type
WHERE lf.asset_id IS NULL
  AND ss.sync_status = 'metadata_processed'
```

This finds versions that have been processed in Phase 1 but don't have a corresponding local file yet.

## Resumability

Because all state is persisted:
- If the process is interrupted during Phase 1, restarting will re-upsert metadata (idempotent) and skip already-completed versions.
- If interrupted during Phase 2, restarting will re-run Phase 1 (fast, just updates), then Phase 2 picks up only versions still in `metadata_processed` state.
- Failed downloads retain their `failed` status and error messages for diagnosis.
