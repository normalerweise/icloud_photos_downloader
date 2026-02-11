# Data Flow and Lifecycle

## End-to-End Flow

### 1. CLI Entry (`base.py`)

```
User runs:  icloudpd -u user@example.com -d /photos --recent 100
```

The Click CLI parses arguments and:
1. Authenticates with iCloud via `authenticator()` -> `PyiCloudService`
2. Creates `SyncManager(Path("/photos"))`
3. Selects strategy: `RecentPhotosStrategy(icloud.photos, 100)`
4. Calls `sync_manager.sync_photos(strategy)`

### 2. Phase 1: Metadata Collection

```
for each PhotoAsset in PhotosToSync:
    |
    v
PhotoAssetRecordMapper.map_icloud_metadata(asset)
    |  Extracts: id, filename, type, dates, dimensions, versions, subtypes
    |  Stores raw master_record and asset_record for future use
    v
PhotoDatabase.upsert_icloud_metadata(record)
    |  INSERT or UPDATE in icloud_assets table
    |  Returns ICloudAssetUpsertResult (INSERTED or UPDATED)
    v
SyncManager._determine_download_needs(asset, record)
    |  For each version in DOWNLOAD_VERSIONS:
    |    - If version not available in iCloud -> skip
    |    - If version already completed in sync_status -> skip
    |    - If version has local file -> mark "completed"
    |    - Otherwise -> mark "metadata_processed"
    v
PhotoDatabase.upsert_sync_status(status)
    |  Record status for each version
    v
asset_map[asset.id] = asset   (kept in memory for Phase 2)
```

### 3. Phase 2: Download

```
PhotoDatabase.get_assets_needing_download()
    |  Query: sync_status = 'metadata_processed' AND no local_file
    v
for each asset_id:
    |
    v
SyncManager._resolve_pending_versions(asset_id, icloud_asset)
    |  Maps version strings ("original") back to VersionSize enums
    |  by checking icloud_asset.versions keys
    v
DownloadManager.download_asset_versions(metadata, icloud_asset, versions)
    |
    |  ThreadPoolExecutor (5 workers)
    |  for each version:
    |    |
    |    v
    |  _download_single_version(version, icloud_asset)
    |    |  Retry loop (3 attempts, exponential backoff):
    |    |    1. Get URL from icloud_asset.versions[version].url
    |    |    2. Compute file path via FileManager.get_file_path()
    |    |    3. Call icloud_asset.download(url)  [authenticated]
    |    |    4. Stream response.raw to file via FileManager
    |    |       - Write to .tmp file
    |    |       - Atomic rename to final path
    |    |    5. Return True/False
    |    v
    |  Collect results: (downloaded_values, failed_values)
    v
SyncManager._record_download_results(asset_id, ...)
    |  For each downloaded version:
    |    - Create LocalFileRecord (filename, path, size, date)
    |    - Upsert to local_files table
    |    - Set sync_status = "completed"
    |  For each failed version:
    |    - Set sync_status = "failed" with error message
    v
ProgressReporter.phase_progress(current, total)
```

### 4. Completion

```
SyncManager._get_sync_stats()
    |  Query: asset count, download count from database
    |  Calculate: disk usage from FileManager
    v
ProgressReporter.sync_complete(stats)
    |  Print summary to terminal
    v
Return stats dict to main()
    |  Print JSON, sys.exit(0)
```

## Data Transformations

### PhotoAsset -> ICloudAssetRecord

```
PhotoAsset (iCloud API)              ICloudAssetRecord (database)
-------------------------------      --------------------------------
.id                            ->    asset_id
.filename                      ->    filename
.item_type.value               ->    asset_type ("image"/"movie")
_determine_subtype()           ->    asset_subtype
.created.isoformat()           ->    created_date
.added_date.isoformat()        ->    added_date
.dimensions[0]                 ->    width
.dimensions[1]                 ->    height
.versions {VersionSize: AV}    ->    asset_versions {str: dict}
._master_record                ->    master_record (JSON)
._asset_record                 ->    asset_record (JSON)
```

### Version Key Mapping

The database stores version keys as strings. When needed for download, they are resolved back to enums:

```
Database string     VersionSize enum              Used for
"original"     <->  AssetVersionSize.ORIGINAL      photos
"adjusted"     <->  AssetVersionSize.ADJUSTED      edited photos
"alternative"  <->  AssetVersionSize.ALTERNATIVE   RAW files
"originalVideo"<->  LivePhotoVersionSize.ORIGINAL  live photo videos
```

Resolution is done by iterating `icloud_asset.versions` and matching `.value`.

### File Path Derivation

```
Input:  asset.id = "AYk6Y+tESR", version = AssetVersionSize.ORIGINAL
        asset.versions[ORIGINAL].file_extension = "JPG"

Step 1: base64_encode("AYk6Y+tESR") -> "QVlrNlkrdEVTUg"
Step 2: version.value -> "original"
Step 3: extension.lower() -> "jpg"

Output: _data/QVlrNlkrdEVTUg-original.jpg
```

## Error Handling

### Phase 1 Errors

If metadata processing fails for an asset:
- Error logged
- SyncStatus set to `"failed"` with error message
- Processing continues with next asset

### Phase 2 Errors

Three levels of error handling:

1. **Per-attempt** (in retry loop): Log warning, exponential backoff, retry
2. **Per-version** (after all retries exhausted): Mark version as `"failed"` in sync_status
3. **Per-asset** (unexpected exception): Mark asset as `"failed"`, continue to next

### Incomplete Downloads

On startup, `FileManager.cleanup_incomplete_downloads()` removes all `.tmp` files from `_data/`. These are artifacts from interrupted atomic writes.

## Resumability Scenarios

### Scenario: Interrupted during Phase 1

```
State: icloud_assets has partial data, some sync_status records exist
Recovery: Re-run. Upserts are idempotent. Already-processed assets get UPDATED.
          Already-completed versions stay "completed". New versions get "metadata_processed".
```

### Scenario: Interrupted during Phase 2

```
State: Some files downloaded, some sync_status = "completed", some = "metadata_processed"
Recovery: Re-run. Phase 1 re-processes metadata (fast, idempotent).
          Phase 2 queries only "metadata_processed" versions -- skips completed ones.
          Partial .tmp files cleaned up automatically.
```

### Scenario: Network failure for specific assets

```
State: Failed versions have sync_status = "failed" with error_message
Recovery: Currently, re-running resets Phase 1 which re-evaluates needs.
          Failed versions without local files will be set to "metadata_processed" again.
          Phase 2 will retry them.
```
