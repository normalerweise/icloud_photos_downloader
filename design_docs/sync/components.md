# Component Reference

All components live under `src/icloudpd/sync/`.

## CLI & Runner

### CLI (`cli.py`)

argparse-based CLI entry point exposed as `icloudpd-sync`. Parses arguments into typed config dataclasses:

```python
def parse(args: Sequence[str]) -> Tuple[SyncGlobalConfig, Sequence[SyncUserConfig]]
```

Supports multi-user syntax: global options apply to all users, per-user options are introduced by `-u/--username`.

### Runner (`runner.py`)

Constructs all components and runs the sync:

```python
def run_sync(global_config: SyncGlobalConfig, user_configs: Sequence[SyncUserConfig]) -> int
```

For each user: authenticates via `authenticator()`, constructs all components, selects a strategy, calls `sync_manager.sync_photos(strategy)`.

### Configuration (`config.py`)

```python
@dataclass(kw_only=True)
class SyncUserConfig:
    username: str
    password: str | None
    directory: str
    auth_only: bool
    cookie_directory: str
    recent: int | None
    skip_created_before: datetime.datetime | datetime.timedelta | None

@dataclass(kw_only=True)
class SyncGlobalConfig:
    log_level: LogLevel
    domain: str
    password_providers: Sequence[PasswordProvider]
    mfa_provider: MFAProvider
```

---

## SyncManager (`sync_manager.py`)

The top-level orchestrator. All dependencies are injected -- `SyncManager` does not create any of its collaborators.

### Construction

```python
SyncManager(
    base_directory: Path,
    database: PhotoDatabase,
    file_manager: FileManager,
    mapper: PhotoAssetRecordMapper,
    download_manager: DownloadManager,
    filesystem_sync: FilesystemSync,
    progress_reporter: ProgressReporter,
    photo_library: PhotoLibrary | None = None,
    clock: Clock | None = None,
)
```

All dependencies are created and wired by `runner.py`. `clock` defaults to `SystemClock()` (replaceable for testing).

### Public API

```python
def sync_photos(self, photos_to_sync: PhotosToSync) -> Dict[str, Any]
```

Runs all five phases and returns statistics:

```json
{
  "total_assets": 1500,
  "downloaded_assets": 1495,
  "failed_assets": 5,
  "deleted_assets": 3,
  "disk_usage_bytes": 15000000000,
  "disk_usage_mb": 14305.11,
  "disk_usage_gb": 13.97
}
```

### Internal Methods

| Method | Phase | Purpose |
|--------|-------|---------|
| `_phase1_metadata_collection` | 1 | Iterate photos, map to records, persist metadata (bulk pre-fetch + batch insert) |
| `_determine_download_needs` | 1 | Compare available vs downloaded versions (takes pre-fetched data) |
| `_phase2_album_sync` | 2 | Sync folders, albums, and membership (DB only) |
| `_sync_folder_tree` | 2 | Recursively upsert folders |
| `_sync_albums` | 2 | Upsert albums and replace membership |
| `_phase3_reconcile_deletions` | 3 | Tombstone deleted assets, folders, albums (DB only) |
| `_detect_asset_deletions` | 3 | Find and tombstone removed assets |
| `_detect_folder_deletions` | 3 | Find and tombstone removed folders |
| `_detect_album_deletions` | 3 | Find and tombstone removed albums |
| `_phase4_download_assets` | 4 | Download all pending versions |
| `_download_single_asset` | 4 | Download all versions of one asset |
| `_resolve_pending_versions` | 4 | Map DB strings to VersionSize enums |
| `_record_download_results` | 4 | Write LocalFileRecord and SyncStatus |
| `_phase5_filesystem_sync` | 5 | Create/update symlink structure |
| `_derive_album_id` | - | Static helper: album -> stable ID string |
| `_mark_asset_failed` | - | Record failure in DB |
| `_get_sync_stats` | - | Compile statistics from DB and filesystem |

### Module-Level Pure Functions (`sync_manager.py`)

| Function | Purpose |
|----------|---------|
| `resolve_version_size(key, available)` | Map a version key string back to a `VersionSize` enum |
| `detect_deleted_ids(existing, synced)` | Set difference: IDs present in DB but absent from sync |
| `build_local_file_record(...)` | Construct a `LocalFileRecord` from download results |

### DownloadResult Enum

```python
class DownloadResult(Enum):
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    SKIPPED = "skipped"
```

---

## PhotoDatabase (`database.py`)

SQLite wrapper providing typed access to six tables.

### Construction

```python
PhotoDatabase(base_directory: Path)
```

Creates `_metadata.sqlite` in the base directory. Tables and indexes are created on first use.

### Protocols and Enums

#### Clock Protocol

```python
class Clock(Protocol):
    def now(self) -> str: ...

class SystemClock:
    def now(self) -> str:
        return datetime.now().isoformat()
```

Injected into `SyncManager` for testable time generation.

#### SyncState Enum

```python
class SyncState(Enum):
    PENDING = "pending"
    METADATA_PROCESSED = "metadata_processed"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
```

#### UpsertResult Enum

```python
class UpsertResult(Enum):
    INSERTED = "inserted"
    UPDATED = "updated"
```

### Data Types

#### ICloudAssetRecord

```python
@dataclass
class ICloudAssetRecord:
    asset_id: str
    filename: str
    asset_type: str | None = None
    asset_subtype: str | None = None       # "live_photo", "burst", "hdr", "panorama"
    created_date: str | None = None        # ISO 8601
    added_date: str | None = None          # ISO 8601
    width: int | None = None
    height: int | None = None
    asset_versions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    master_record: Dict[str, Any] = field(default_factory=dict)
    asset_record: Dict[str, Any] = field(default_factory=dict)
    last_metadata_update: str | None = None
    metadata_inserted_date: str | None = None
    deleted: bool = False
    deletion_detected_on: str | None = None
```

#### LocalFileRecord

```python
@dataclass
class LocalFileRecord:
    asset_id: str
    version_type: str
    local_filename: str
    file_path: str             # relative to base directory
    file_size: int
    download_date: str         # ISO 8601
    checksum: str | None = None
```

#### SyncStatus

```python
@dataclass
class SyncStatus:
    asset_id: str
    version_type: str
    sync_status: SyncState = SyncState.PENDING
    last_sync_date: str | None = None
    retry_count: int = 0
    error_message: str | None = None
```

#### FolderRecord

```python
@dataclass
class FolderRecord:
    folder_id: str              # iCloud recordName (UUID)
    folder_name: str
    parent_folder_id: str | None = None
    position: int | None = None
    last_sync_date: str | None = None
    inserted_date: str | None = None
    deleted: bool = False
    deletion_detected_on: str | None = None
```

#### AlbumRecord

```python
@dataclass
class AlbumRecord:
    album_id: str               # 'user:<recordName>' or 'smart:<key>'
    album_name: str
    album_type: str             # 'smart' or 'user'
    folder_id: str | None = None
    obj_type: str | None = None
    list_type: str | None = None
    position: int | None = None
    last_sync_date: str | None = None
    inserted_date: str | None = None
    deleted: bool = False
    deletion_detected_on: str | None = None
```

### Key Methods

| Method | Returns | Purpose |
|--------|---------|---------|
| `upsert_icloud_metadata(record)` | `ICloudAssetUpsertResult` | Insert/update asset metadata |
| `upsert_local_file(record)` | None | Record downloaded file |
| `upsert_sync_status(status)` | None | Update version sync state |
| `get_icloud_metadata(asset_id)` | `ICloudAssetRecord \| None` | Fetch asset metadata |
| `get_local_files(asset_id)` | `list[LocalFileRecord]` | All downloaded versions |
| `get_sync_status(asset_id, ver)` | `SyncStatus \| None` | Single version state |
| `get_all_sync_statuses(asset_id)` | `list[SyncStatus]` | All version states |
| `get_assets_needing_download()` | `list[str]` | Asset IDs with metadata_processed status |
| `get_sync_statuses_for_assets(ids)` | `dict[str, list[SyncStatus]]` | Bulk-fetch statuses for multiple assets |
| `get_local_files_for_assets(ids)` | `dict[str, list[LocalFileRecord]]` | Bulk-fetch local files for multiple assets |
| `batch_upsert_sync_statuses(statuses)` | None | Batch-insert/update sync statuses |
| `get_asset_count()` | `int` | Total non-deleted assets |
| `get_downloaded_count()` | `int` | Assets with at least one local file |
| `mark_asset_deleted(id, date)` | None | Tombstone asset + cascade album_assets |
| `upsert_folder(record)` | `FolderUpsertResult` | Insert/update folder |
| `get_all_folder_ids()` | `list[str]` | Non-deleted folder IDs |
| `mark_folder_deleted(id, date)` | None | Tombstone folder + cascade children |
| `upsert_album(record)` | `AlbumUpsertResult` | Insert/update album |
| `get_all_album_ids()` | `list[str]` | Non-deleted album IDs |
| `mark_album_deleted(id, date)` | None | Tombstone album + remove membership |
| `replace_album_assets(id, ids)` | `int` | Full-replace album membership |
| `get_album_assets(album_id)` | `list[str]` | Asset IDs in album |
| `get_asset_albums(asset_id)` | `list[str]` | Albums containing asset |
| `get_all_non_deleted_albums()` | `list[AlbumRecord]` | Full album objects |
| `get_all_non_deleted_folders()` | `list[FolderRecord]` | Full folder objects |
| `get_all_downloaded_assets()` | `list[tuple]` | Assets + local files (JOIN) |

---

## FilesystemSync (`filesystem_sync.py`)

Phase 5: projects database state onto the filesystem as a browsable symlink structure. Purely local -- no network access.

### Construction

```python
FilesystemSync(base_directory: Path, database: PhotoDatabase)
```

### Public API

```python
def sync_filesystem(self) -> dict[str, Any]
```

Returns stats: `{"created": N, "removed": N, "updated": N, "unchanged": N}`

### Delta Convergence Algorithm

1. **Compute desired state** from DB: `dict[Path, Path]` of symlink -> relative target
2. **Scan existing symlinks** on disk in `Library/` and `Albums/`
3. **Converge**: create missing, remove stale, update wrong targets
4. **Prune** empty directories bottom-up

### Symlink Naming

```
YYYYMMDD_HHMMSS_OriginalFilename.ext        (original version)
YYYYMMDD_HHMMSS_OriginalFilename-adjusted.ext (non-original versions)
YYYYMMDD_HHMMSS_OriginalFilename.mov          (live photo, ext from local file)
```

Collision disambiguation: sorted by asset_id, first gets clean name, subsequent get `_XXXXX` suffix (5-char base64 of asset_id).

---

## DownloadManager (`download_manager.py`)

Handles parallel downloads with retry logic. Does not manage authentication -- it receives `PhotoAsset` objects that carry an authenticated session.

### Construction

```python
DownloadManager(file_manager: FileManager, session: PyiCloudSession, mapper: PhotoAssetRecordMapper)
```

The `mapper` is used to create lightweight `AssetFileRef` objects from `PhotoAsset` for filesystem operations.

### Public API

```python
def download_asset_versions(
    self,
    asset: ICloudAssetRecord,
    icloud_asset: PhotoAsset,
    versions_to_download: List[VersionSize],
) -> Tuple[List[str], List[str]]
```

Returns `(downloaded_version_values, failed_version_values)` -- lists of version string values (e.g., `["original", "adjusted"]`).

### Download Strategy

- Uses `ThreadPoolExecutor` with `MAX_CONCURRENT_DOWNLOADS` (5) workers
- Each version is submitted as an independent task
- Results collected via `as_completed()`

### Retry Logic

Per-version retry with exponential backoff:

```
Attempt 1: immediate
Attempt 2: wait RETRY_DELAY * 2^0 = 2s
Attempt 3: wait RETRY_DELAY * 2^1 = 4s
```

### Authentication

Downloads use `icloud_asset.download(url)` which calls `self._service.session.get(url, stream=True)` internally. The `PyiCloudSession` carries all necessary authentication cookies.

The response is streamed to disk via `FileManager.save_file_from_stream(file_path, response.raw, overwrite=True)`.

---

## FileManager (`file_manager.py`)

Manages file operations in the `_data/` subdirectory.

### AssetFileRef

Lightweight reference type that decouples `FileManager` from the iCloud domain:

```python
@dataclass(frozen=True)
class AssetFileRef:
    asset_id: str
    filename: str
```

Created via `PhotoAssetRecordMapper.to_file_ref(asset)`. Used by `FileManager` instead of raw `PhotoAsset` objects.

### Construction

```python
FileManager(base_directory: Path)
```

Creates `<base_directory>/_data/` if it doesn't exist.

### Filename Generation

```python
def get_file_path(self, asset_ref: AssetFileRef, version: VersionSize) -> Path
```

Formula: `_data/{base64_asset_id}-{version_value}.{extension}`

- Asset ID is encoded as URL-safe base64 with padding stripped
- Version value comes from the enum (e.g., `"original"`, `"live_photo"`)
- Extension comes from the `AssetVersion.file_extension` field, lowercased

Example: `_data/QVlrNlkrdEVTUg-original.jpg`

### Atomic Writes

All file saves use a two-step process:
1. Write to `<path>.tmp` temporary file
2. Atomic rename to final path

If the process is interrupted, only `.tmp` files remain, which are cleaned up on the next run by `cleanup_incomplete_downloads()`.

### Key Methods

| Method | Purpose |
|--------|---------|
| `get_file_path(asset_ref, version)` | Compute deterministic file path |
| `file_exists(asset_ref, version)` | Check if already downloaded |
| `save_file(asset_ref, version, content)` | Write bytes atomically |
| `save_file_from_stream(path, stream, overwrite)` | Stream write atomically (used by DownloadManager) |
| `delete_file(asset_ref, version)` | Remove file from disk |
| `delete_asset_files(asset_id)` | Remove all files for an asset |
| `get_file_size(asset_ref, version)` | File size in bytes |
| `cleanup_incomplete_downloads()` | Remove all `.tmp` files |
| `get_disk_usage()` | Total bytes in `_data/` |

---

## PhotosToSync Strategies (`sync_strategy.py`)

Abstract base class implementing `__iter__`, `__len__`, and `covers_full_library` (abstract property) for the strategy pattern.

### RecentPhotosStrategy (`covers_full_library = False`)

```python
RecentPhotosStrategy(photos_service: PhotosService, count: int)
```

- Iterates: Most recent `count` photos via `islice(photos_service.all(descending=True), count)`
- Length: Returns `count` (exact)

### SinceDateStrategy (`covers_full_library = False`)

```python
SinceDateStrategy(photos_service: PhotosService, since: datetime)
```

- Iterates: All photos with `created >= since`, newest first
- Length: Upper-bound estimate from `len(photos_service.all)` (fast API call, does not iterate)

### NoOpStrategy (`covers_full_library = True`)

```python
NoOpStrategy(photos_service: PhotosService)
```

- Iterates: All photos, newest first
- Length: Delegates to `PhotoAlbum.__len__()` (API call)

---

## PhotoAssetRecordMapper (`photo_asset_record_mapper.py`)

Stateless mapper that converts `PhotoAsset` (iCloud API objects) into database records and lightweight references.

### Key Methods

```python
@staticmethod
def to_file_ref(asset: PhotoAsset) -> AssetFileRef
```

Creates a lightweight `AssetFileRef` for filesystem operations without coupling to `PhotoAsset`.

```python
@staticmethod
def map_icloud_metadata(asset: PhotoAsset, now: str) -> ICloudAssetRecord
```

The `now` parameter (from `Clock.now()`) is used for `metadata_inserted_date`, keeping the mapper pure.

Extracts from `PhotoAsset`:
- `asset.id` -> `asset_id`
- `asset.filename` -> `filename`
- `asset.item_type.value` -> `asset_type`
- `asset.created.isoformat()` -> `created_date`
- `asset.added_date.isoformat()` -> `added_date`
- `asset.dimensions` -> `width`, `height`
- `asset.versions` -> `asset_versions` (flattened to string-keyed dict)
- `asset._master_record` -> `master_record`
- `asset._asset_record` -> `asset_record`

### Subtype Detection

```python
@staticmethod
def _determine_subtype(asset: PhotoAsset) -> str | None
```

| Check | Result |
|-------|--------|
| "VidCompl" in version keys | `"live_photo"` |
| `burstId` in asset_record fields | `"burst"` |
| `assetHDRType` in asset_record fields | `"hdr"` |
| `assetSubtype` == 3 | `"panorama"` |
| None of the above | `None` |

---

## ProgressReporter (`progress_reporter.py`)

Abstract base class with two implementations.

### Interface

```python
class ProgressReporter(ABC):
    def phase_start(self, phase_name: str, total_items: int) -> None
    def phase_progress(self, current: int, total: int, **kwargs) -> None
    def phase_complete(self, phase_name: str, stats: Dict[str, Any]) -> None
    def sync_complete(self, final_stats: Dict[str, Any]) -> None
```

### TerminalProgressReporter

Renders a 40-character progress bar with speed and ETA:

```
Phase 1: Change detection
████████████████████░░░░░░░░░░░░░░░░░░░░  50.0% (500/1000) (speed: 45.3 assets/min; ETA: 11 min)
```

Uses `\r` for in-place line updates. Adds newline only on phase completion.

### LoggingProgressReporter

Logs progress via `logging.info()` every 10 items. Suitable for non-interactive environments (cron, Docker).

---

## Constants (`constants.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_CONCURRENT_DOWNLOADS` | 5 | Thread pool size |
| `DOWNLOAD_TIMEOUT` | 30s | Per-request timeout |
| `RETRY_ATTEMPTS` | 3 | Max retries per version |
| `RETRY_DELAY` | 2s | Base delay (exponential backoff) |
| `DATABASE_FILENAME` | `_metadata.sqlite` | Database file name |
| `DATA_DIRECTORY` | `_data` | Download subdirectory |
| `LIBRARY_DIRECTORY` | `Library` | Symlink tree for date-organized browsing |
| `ALBUMS_DIRECTORY` | `Albums` | Symlink tree for album/folder browsing |
| `DOWNLOAD_VERSIONS` | See below | Which versions to fetch |
| `LIVE_PHOTO_EXTENSIONS` | `(".mov", ".mp4")` | Supported live photo file extensions |

### DOWNLOAD_VERSIONS

```python
(
    AssetVersionSize.ORIGINAL,        # "original"
    LivePhotoVersionSize.ORIGINAL,    # "live_photo"
    AssetVersionSize.ADJUSTED,        # "adjusted"
    AssetVersionSize.ALTERNATIVE,     # "alternative"
)
```

## External Types (pyicloud_ipd)

### VersionSize

```python
VersionSize = Union[AssetVersionSize, LivePhotoVersionSize]
```

`AssetVersionSize`: ORIGINAL, ADJUSTED, ALTERNATIVE, MEDIUM, THUMB
`LivePhotoVersionSize`: ORIGINAL ("originalVideo"), MEDIUM ("mediumVideo"), THUMB ("smallVideo")

### AssetVersion

```python
class AssetVersion:
    filename: str
    size: int
    url: str
    type: str              # MIME type, e.g., "public.jpeg"
    file_extension: str    # e.g., "JPG"
```

### PhotoAsset

Key properties used by the new architecture:
- `id: str` -- unique asset identifier
- `filename: str` -- cleaned filename
- `created: datetime` -- creation timestamp (UTC)
- `added_date: datetime` -- library addition timestamp
- `dimensions: Tuple[int, int]` -- (width, height)
- `item_type: AssetItemType | None` -- IMAGE or MOVIE
- `versions: Dict[VersionSize, AssetVersion]` -- available versions
- `download(url: str) -> Response` -- authenticated streaming download

### PhotoFolder

```python
@dataclass
class PhotoFolder:
    record_name: str
    name: str
    parent_id: str | None = None
    children_folders: list[PhotoFolder]
    children_albums: list[PhotoAlbum]
```

### PhotoAlbum

Key properties:
- `name: str` -- album display name
- `record_name: str | None` -- iCloud UUID (None for smart albums)
- `parent_folder_id: str | None` -- parent folder's recordName
- `obj_type: str` -- iCloud object type
- `list_type: str` -- iCloud list type
- Iterable: yields `PhotoAsset` objects for contained photos
