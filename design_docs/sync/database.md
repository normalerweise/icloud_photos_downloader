# Database Design

## Overview

The sync state is persisted in a SQLite database (`_metadata.sqlite`) stored in the download root directory. The schema uses six tables to separate concerns: remote metadata, local file tracking, sync progress, folder hierarchy, albums, and album-asset membership.

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
    metadata_inserted_date  DATETIME,
    deleted                 BOOLEAN DEFAULT FALSE,
    deletion_detected_on    DATETIME
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
    version_type    TEXT,           -- "original" | "adjusted" | "alternative" | "live_photo"
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

### `folders` -- Folder Hierarchy

Stores the iCloud Photos folder tree. Self-referencing via `parent_folder_id`.

```sql
CREATE TABLE folders (
    folder_id           TEXT PRIMARY KEY,    -- iCloud recordName (UUID)
    folder_name         TEXT NOT NULL,
    parent_folder_id    TEXT,                -- NULL for top-level, FK for nested
    position            INTEGER,
    last_sync_date      DATETIME,
    inserted_date       DATETIME,
    deleted             BOOLEAN DEFAULT FALSE,
    deletion_detected_on DATETIME,
    FOREIGN KEY (parent_folder_id) REFERENCES folders(folder_id)
);
```

### `albums` -- Albums

Stores iCloud albums (both user-created and smart albums).

```sql
CREATE TABLE albums (
    album_id            TEXT PRIMARY KEY,    -- 'user:<recordName>' or 'smart:<key>'
    album_name          TEXT NOT NULL,
    album_type          TEXT NOT NULL,       -- 'smart' or 'user'
    folder_id           TEXT,                -- parent folder, NULL for top-level/smart
    obj_type            TEXT,
    list_type           TEXT,
    position            INTEGER,
    last_sync_date      DATETIME,
    inserted_date       DATETIME,
    deleted             BOOLEAN DEFAULT FALSE,
    deletion_detected_on DATETIME,
    FOREIGN KEY (folder_id) REFERENCES folders(folder_id)
);
```

### `album_assets` -- Album Membership

Junction table linking albums to their assets. Full-replaced on each sync.

```sql
CREATE TABLE album_assets (
    album_id    TEXT NOT NULL,
    asset_id    TEXT NOT NULL,
    position    INTEGER,
    PRIMARY KEY (album_id, asset_id),
    FOREIGN KEY (album_id) REFERENCES albums(album_id),
    FOREIGN KEY (asset_id) REFERENCES icloud_assets(asset_id)
);
```

### Indexes

```sql
CREATE INDEX idx_sync_status ON sync_status(sync_status);
CREATE INDEX idx_local_files_asset_id ON local_files(asset_id);
CREATE INDEX idx_albums_folder_id ON albums(folder_id);
CREATE INDEX idx_album_assets_asset_id ON album_assets(asset_id);
CREATE INDEX idx_album_assets_album_id ON album_assets(album_id);
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
| `downloading` | Download in progress | Phase 4 (before download) |
| `completed` | File downloaded and recorded | Phase 4 (after success) |
| `failed` | Download or metadata processing failed | Either phase |

The `pending` state exists in the dataclass default but is not used in the current flow. Phase 1 sets versions directly to `metadata_processed` (ready for download) or `completed` (already on disk).

## Deletion Tracking

Assets, folders, and albums use a tombstone pattern for deletion tracking:

- `deleted` (BOOLEAN) -- set to TRUE when the entity is no longer seen in iCloud
- `deletion_detected_on` (DATETIME) -- timestamp when deletion was detected

Tombstoned records are excluded from active queries (e.g., `get_all_asset_ids()` returns only non-deleted assets). Deletion is cascaded:
- `mark_asset_deleted` also removes rows from `album_assets`
- `mark_album_deleted` also removes rows from `album_assets`
- `mark_folder_deleted` cascades to child folders and their albums

## Upsert Strategy

### icloud_assets

Uses `INSERT ... ON CONFLICT DO UPDATE` with `RETURNING` to atomically insert or update metadata. The `metadata_inserted_date` field is compared to distinguish inserts from updates:
- If the returned `metadata_inserted_date` matches the one we set, it was an INSERT.
- Otherwise, it was an UPDATE (the existing `metadata_inserted_date` was preserved).

### local_files and sync_status

Use `INSERT OR REPLACE` for idempotent upserts. The composite primary key `(asset_id, version_type)` ensures one record per version.

### folders and albums

Use `INSERT ... ON CONFLICT DO UPDATE` with `RETURNING`, same pattern as `icloud_assets`.

### album_assets

Full-replace strategy: `DELETE FROM album_assets WHERE album_id = ?` followed by batch `INSERT`. This ensures membership is always current with iCloud.

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

### All downloaded assets (for Phase 5 filesystem sync)

```sql
SELECT a.*, lf.*
FROM icloud_assets a
JOIN local_files lf ON a.asset_id = lf.asset_id
WHERE (a.deleted = FALSE OR a.deleted IS NULL)
ORDER BY a.asset_id, lf.version_type
```

Efficient single-query fetch of all data needed to build the symlink layer.

## Resumability

Because all state is persisted:
- If the process is interrupted during Phase 1, restarting will re-upsert metadata (idempotent) and skip already-completed versions.
- If interrupted during Phase 4, restarting will re-run Phase 1 (fast, just updates), then Phase 4 picks up only versions still in `metadata_processed` state.
- Failed downloads retain their `failed` status and error messages for diagnosis.
- Phase 5 (filesystem sync) uses delta convergence and is idempotent.
