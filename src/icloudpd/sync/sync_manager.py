"""Main orchestration for the new download architecture."""

import logging
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List

from pyicloud_ipd.services.photos import (
    PhotoAlbum,
    PhotoAsset,
    PhotoFolder,
    PhotoLibrary,
)
from pyicloud_ipd.version_size import VersionSize

from .constants import DOWNLOAD_VERSIONS
from .database import (
    AlbumRecord,
    Clock,
    FolderRecord,
    ICloudAssetRecord,
    LocalFileRecord,
    PhotoDatabase,
    SyncState,
    SyncStatus,
    SystemClock,
    UpsertResult,
)
from .download_manager import DownloadManager
from .file_manager import FileManager
from .filesystem_sync import FilesystemSync
from .photo_asset_record_mapper import PhotoAssetRecordMapper
from .progress_reporter import ProgressReporter
from .sync_strategy import PhotosToSync

logger = logging.getLogger(__name__)


# -- Pure functions (no SyncManager state needed) ------------------------------


def resolve_version_size(
    version_key: str, available_versions: Iterable[VersionSize]
) -> VersionSize | None:
    """Map a version key string back to a VersionSize enum."""
    for version_size in available_versions:
        if version_size.value == version_key:
            return version_size
    return None


def detect_deleted_ids(existing_ids: set[str], synced_ids: set[str]) -> set[str]:
    """Return IDs present in existing but absent from synced."""
    return existing_ids - synced_ids


def build_local_file_record(
    asset_id: str,
    version: VersionSize,
    file_path: Path,
    base_directory: Path,
    file_size: int,
    download_date: str,
) -> LocalFileRecord:
    """Construct a LocalFileRecord from download results (pure value construction)."""
    return LocalFileRecord(
        asset_id=asset_id,
        version_type=version.value,
        local_filename=file_path.name,
        file_path=str(file_path.relative_to(base_directory)),
        file_size=file_size,
        download_date=download_date,
        checksum=None,
    )


class DownloadResult(Enum):
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SyncManager:
    """Main orchestration class for downloading iCloud photos."""

    def __init__(
        self,
        base_directory: Path,
        database: PhotoDatabase,
        file_manager: FileManager,
        mapper: PhotoAssetRecordMapper,
        download_manager: DownloadManager,
        filesystem_sync: FilesystemSync,
        progress_reporter: ProgressReporter,
        photo_library: PhotoLibrary | None = None,
        clock: Clock | None = None,
    ):
        self.base_directory = Path(base_directory)
        self.database = database
        self.file_manager = file_manager
        self.mapper = mapper
        self.download_manager = download_manager
        self.filesystem_sync = filesystem_sync
        self.progress_reporter = progress_reporter
        self.photo_library = photo_library
        self.clock = clock or SystemClock()

    def sync_photos(self, photos_to_sync: PhotosToSync) -> Dict[str, Any]:
        """Main sync method: metadata collection then download."""
        logger.info("Starting photo sync with phased approach...")

        cleaned_count = self.file_manager.cleanup_incomplete_downloads()
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} incomplete downloads")

        phase1_stats, asset_map = self._phase1_metadata_collection(photos_to_sync)

        synced_folder_ids: set[str] = set()
        synced_album_ids: set[str] = set()
        if self.photo_library is not None:
            synced_folder_ids, synced_album_ids = self._phase2_album_sync(self.photo_library)

        deleted_count = 0
        if photos_to_sync.covers_full_library:
            deleted_count = self._phase3_reconcile_deletions(asset_map, synced_folder_ids, synced_album_ids)
        else:
            logger.debug("Skipping deletion detection: strategy does not cover full library.")

        self._phase4_download_assets(asset_map)

        self._phase5_filesystem_sync()

        final_stats = self._get_sync_stats(deleted_count)
        self.progress_reporter.sync_complete(final_stats)
        return final_stats

    # -- Phase 1: Metadata collection -------------------------------------------

    def _phase1_metadata_collection(
        self, photos_to_sync: PhotosToSync
    ) -> tuple[Dict[str, Any], Dict[str, PhotoAsset]]:
        """Collect metadata from iCloud and determine what needs downloading."""
        logger.info("Phase 1: Starting metadata collection and change detection...")

        total_photos = len(photos_to_sync)
        self.progress_reporter.phase_start("Phase 1: Change detection", total_photos)

        processed_count = 0
        new_assets = 0
        updated_assets = 0
        failed_assets = 0
        asset_map: Dict[str, PhotoAsset] = {}

        # Collect all assets first to enable bulk queries
        assets_list: List[PhotoAsset] = []
        for asset in photos_to_sync:
            assets_list.append(asset)
            asset_map[asset.id] = asset

        # Pre-fetch existing statuses and local files for all assets
        all_asset_ids = [a.id for a in assets_list]
        existing_statuses_map = self.database.get_sync_statuses_for_assets(all_asset_ids)
        local_files_map = self.database.get_local_files_for_assets(all_asset_ids)

        all_sync_statuses: List[SyncStatus] = []

        for i, asset in enumerate(assets_list):
            try:
                icloud_metadata = self.mapper.map_icloud_metadata(asset, self.clock.now())

                upsert_result = self.database.upsert_icloud_metadata(icloud_metadata)
                if upsert_result.operation == UpsertResult.INSERTED:
                    new_assets += 1
                else:
                    updated_assets += 1

                sync_statuses = self._determine_download_needs(
                    asset,
                    icloud_metadata,
                    existing_statuses_map.get(asset.id, []),
                    local_files_map.get(asset.id, []),
                )
                all_sync_statuses.extend(sync_statuses)

                processed_count += 1
            except Exception as e:
                logger.error(f"Failed to process metadata for asset {asset.id}: {e}")
                failed_assets += 1
                self._mark_asset_failed(asset.id, "original", str(e))

            self.progress_reporter.phase_progress(i + 1, total_photos)

        # Batch-insert all sync statuses
        self.database.batch_upsert_sync_statuses(all_sync_statuses)

        phase1_stats = {
            "processed": processed_count,
            "new_assets": new_assets,
            "updated_assets": updated_assets,
            "failed_assets": failed_assets,
        }
        self.progress_reporter.phase_complete("Phase 1: Change detection", phase1_stats)
        logger.info(f"Phase 1 completed: {processed_count} assets processed")
        return phase1_stats, asset_map

    def _determine_download_needs(
        self,
        asset: PhotoAsset,
        icloud_metadata: ICloudAssetRecord,
        existing_statuses: List[SyncStatus],
        local_files: List[LocalFileRecord],
    ) -> List[SyncStatus]:
        """Determine which versions need downloading for an asset (pure)."""
        sync_statuses: List[SyncStatus] = []

        available_versions = set(icloud_metadata.asset_versions.keys())
        local_version_types = {f.version_type for f in local_files}

        desired_versions = [vs.value for vs in DOWNLOAD_VERSIONS if vs.value in available_versions]

        for version_type in desired_versions:
            existing = next((s for s in existing_statuses if s.version_type == version_type), None)
            if existing and existing.sync_status == SyncState.COMPLETED:
                continue

            if version_type in local_version_types:
                sync_statuses.append(
                    SyncStatus(
                        asset_id=asset.id,
                        version_type=version_type,
                        sync_status=SyncState.COMPLETED,
                    )
                )
            else:
                sync_statuses.append(
                    SyncStatus(
                        asset_id=asset.id,
                        version_type=version_type,
                        sync_status=SyncState.METADATA_PROCESSED,
                    )
                )

        return sync_statuses

    # -- Phase 4: Download assets -----------------------------------------------

    def _phase4_download_assets(self, asset_map: Dict[str, PhotoAsset]) -> Dict[str, Any]:
        """Download assets that have metadata_processed status."""
        logger.info("Phase 4: Starting asset downloads...")

        assets_to_download = self.database.get_assets_needing_download()
        total_assets = len(assets_to_download)

        if total_assets == 0:
            logger.info("No assets need downloading")
            return {"downloaded": 0, "failed": 0, "skipped": 0}

        self.progress_reporter.phase_start("Phase 4: Downloading assets", total_assets)

        downloaded_count = 0
        failed_count = 0
        skipped_count = 0

        for i, asset_id in enumerate(assets_to_download):
            try:
                result = self._download_single_asset(asset_id, asset_map)
                if result == DownloadResult.DOWNLOADED:
                    downloaded_count += 1
                elif result == DownloadResult.FAILED:
                    failed_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                logger.error(f"Failed to download asset {asset_id}: {e}")
                failed_count += 1
                self._mark_asset_failed(asset_id, "original", str(e))

            self.progress_reporter.phase_progress(i + 1, total_assets)

        phase4_stats = {
            "downloaded": downloaded_count,
            "failed": failed_count,
            "skipped": skipped_count,
        }
        self.progress_reporter.phase_complete("Phase 4: Downloading assets", phase4_stats)
        logger.info(
            f"Phase 4 completed: {downloaded_count} downloaded, "
            f"{failed_count} failed, {skipped_count} skipped"
        )
        return phase4_stats

    def _download_single_asset(
        self, asset_id: str, asset_map: Dict[str, PhotoAsset]
    ) -> DownloadResult:
        """Download all pending versions of a single asset."""
        icloud_asset = asset_map.get(asset_id)
        if not icloud_asset:
            logger.warning(f"Asset {asset_id} not found in asset map")
            self._mark_asset_failed(asset_id, "original", "Asset not found in asset map")
            return DownloadResult.FAILED

        metadata = self.database.get_icloud_metadata(asset_id)
        if not metadata:
            logger.warning(f"Asset {asset_id} not found in database")
            return DownloadResult.FAILED

        versions_to_download = self._resolve_pending_versions(asset_id, icloud_asset)
        if not versions_to_download:
            return DownloadResult.SKIPPED

        downloaded_versions, failed_versions = self.download_manager.download_asset_versions(
            metadata, icloud_asset, versions_to_download
        )

        self._record_download_results(asset_id, icloud_asset, downloaded_versions, failed_versions)

        if failed_versions:
            return DownloadResult.FAILED
        return DownloadResult.DOWNLOADED

    def _resolve_pending_versions(
        self, asset_id: str, icloud_asset: PhotoAsset
    ) -> List[VersionSize]:
        """Get VersionSize enums for versions that need downloading."""
        pending_statuses = [
            s
            for s in self.database.get_all_sync_statuses(asset_id)
            if s.sync_status == SyncState.METADATA_PROCESSED
        ]
        versions: List[VersionSize] = []
        for status in pending_statuses:
            version_size = resolve_version_size(status.version_type, icloud_asset.versions)
            if version_size is not None:
                versions.append(version_size)
            else:
                logger.warning(
                    f"Could not resolve version '{status.version_type}' for asset {asset_id}"
                )
        return versions

    def _record_download_results(
        self,
        asset_id: str,
        icloud_asset: PhotoAsset,
        downloaded_versions: List[VersionSize],
        failed_versions: List[VersionSize],
    ) -> None:
        """Record download results in the database."""
        asset_ref = self.mapper.to_file_ref(icloud_asset)
        for version in downloaded_versions:
            file_path = self.file_manager.get_file_path(asset_ref, version)
            file_size = self.file_manager.get_file_size(asset_ref, version) or 0

            record = build_local_file_record(
                asset_id, version, file_path, self.base_directory, file_size, self.clock.now()
            )
            self.database.upsert_local_file(record)
            self.database.upsert_sync_status(
                SyncStatus(
                    asset_id=asset_id,
                    version_type=version.value,
                    sync_status=SyncState.COMPLETED,
                )
            )

        for version in failed_versions:
            self.database.upsert_sync_status(
                SyncStatus(
                    asset_id=asset_id,
                    version_type=version.value,
                    sync_status=SyncState.FAILED,
                    error_message="Download failed",
                )
            )

    # -- Phase 3: Reconcile deletions (DB only) ----------------------------------

    def _phase3_reconcile_deletions(
        self,
        asset_map: Dict[str, PhotoAsset],
        synced_folder_ids: set[str],
        synced_album_ids: set[str],
    ) -> int:
        """Detect deletions across assets, folders, and albums (DB tombstone only).

        Returns:
            Number of assets marked as deleted.
        """
        logger.info("Phase 3: Starting deletion reconciliation...")

        detected_on = self.clock.now()

        deleted_assets = self._detect_asset_deletions(asset_map, detected_on)
        deleted_folders = self._detect_folder_deletions(synced_folder_ids, detected_on)
        deleted_albums = self._detect_album_deletions(synced_album_ids, detected_on)

        logger.info(
            f"Phase 3 completed: {deleted_assets} assets, "
            f"{deleted_folders} folders, {deleted_albums} albums marked deleted"
        )
        return deleted_assets

    def _detect_asset_deletions(
        self, asset_map: Dict[str, PhotoAsset], detected_on: str
    ) -> int:
        """Tombstone assets no longer present in iCloud."""
        deleted_ids = detect_deleted_ids(set(self.database.get_all_asset_ids()), set(asset_map.keys()))
        for asset_id in deleted_ids:
            self.database.mark_asset_deleted(asset_id, detected_on)
            logger.debug(f"Marked asset {asset_id} as deleted")
        return len(deleted_ids)

    # -- Phase 2: Album and folder sync ------------------------------------------

    def _phase2_album_sync(
        self, photo_library: PhotoLibrary
    ) -> tuple[set[str], set[str]]:
        """Sync folders, albums, and album membership from iCloud (DB only).

        Returns:
            Tuple of (synced_folder_ids, synced_album_ids) for deletion detection in Phase 3.
        """
        logger.info("Phase 2: Starting album and folder sync...")

        folder_tree = photo_library.folders
        albums_dict = photo_library.albums

        self.progress_reporter.phase_start("Phase 2: Album sync", len(albums_dict))

        synced_folder_ids = self._sync_folder_tree(folder_tree)
        synced_album_ids, total_memberships = self._sync_albums(albums_dict)

        self.progress_reporter.phase_complete("Phase 2: Album sync", {
            "folders": len(synced_folder_ids),
            "albums": len(synced_album_ids),
            "memberships": total_memberships,
        })

        logger.info(
            f"Phase 2 completed: {len(synced_folder_ids)} folders, "
            f"{len(synced_album_ids)} albums, {total_memberships} memberships"
        )
        return synced_folder_ids, synced_album_ids

    def _sync_folder_tree(self, folders: list[PhotoFolder]) -> set[str]:
        """Recursively upsert folders and return all synced folder IDs."""
        synced_ids: set[str] = set()
        for folder in folders:
            self.database.upsert_folder(
                FolderRecord(
                    folder_id=folder.record_name,
                    folder_name=folder.name,
                    parent_folder_id=folder.parent_id,
                )
            )
            synced_ids.add(folder.record_name)
            synced_ids.update(self._sync_folder_tree(folder.children_folders))
        return synced_ids

    def _sync_albums(
        self, albums_dict: dict[str, PhotoAlbum]
    ) -> tuple[set[str], int]:
        """Upsert albums and sync their asset membership."""
        synced_ids: set[str] = set()
        total_memberships = 0
        total_albums = len(albums_dict)

        for i, (name, album) in enumerate(albums_dict.items()):
            album_id = self._derive_album_id(name, album)
            album_type = "user" if album.record_name is not None else "smart"

            self.database.upsert_album(
                AlbumRecord(
                    album_id=album_id,
                    album_name=name,
                    album_type=album_type,
                    folder_id=album.parent_folder_id,
                    obj_type=album.obj_type,
                    list_type=album.list_type,
                )
            )
            synced_ids.add(album_id)

            if album_type == "user":
                asset_ids = [asset.id for asset in album]
                count = self.database.replace_album_assets(album_id, asset_ids)
                total_memberships += count

            self.progress_reporter.phase_progress(i + 1, total_albums)

        return synced_ids, total_memberships

    def _detect_folder_deletions(self, synced_ids: set[str], detected_on: str) -> int:
        """Tombstone folders no longer present in iCloud."""
        deleted_ids = detect_deleted_ids(set(self.database.get_all_folder_ids()), synced_ids)
        for folder_id in deleted_ids:
            self.database.mark_folder_deleted(folder_id, detected_on)
        return len(deleted_ids)

    def _detect_album_deletions(self, synced_ids: set[str], detected_on: str) -> int:
        """Tombstone albums no longer present in iCloud."""
        deleted_ids = detect_deleted_ids(set(self.database.get_all_album_ids()), synced_ids)
        for album_id in deleted_ids:
            self.database.mark_album_deleted(album_id, detected_on)
        return len(deleted_ids)

    @staticmethod
    def _derive_album_id(name: str, album: PhotoAlbum) -> str:
        """Derive a stable unique ID for a PhotoAlbum."""
        record_name = album.record_name
        if record_name is not None:
            return f"user:{record_name}"
        smart_key = name.replace(" ", "")
        return f"smart:{smart_key}"

    # -- Phase 5: Filesystem sync ------------------------------------------------

    def _phase5_filesystem_sync(self) -> dict[str, Any]:
        """Phase 5: Create browsable symlink structure from database state."""
        logger.info("Phase 5: Syncing filesystem structure...")
        self.progress_reporter.phase_start("Phase 5: Filesystem sync", 0)

        stats = self.filesystem_sync.sync_filesystem()

        self.progress_reporter.phase_complete("Phase 5: Filesystem sync", stats)
        logger.info(
            f"Phase 5 completed: {stats.get('created', 0)} created, "
            f"{stats.get('removed', 0)} removed, {stats.get('updated', 0)} updated"
        )
        return stats

    # -- Helpers ----------------------------------------------------------------

    def _mark_asset_failed(self, asset_id: str, version_type: str, error: str) -> None:
        """Mark an asset version as failed in the database."""
        try:
            self.database.upsert_sync_status(
                SyncStatus(
                    asset_id=asset_id,
                    version_type=version_type,
                    sync_status=SyncState.FAILED,
                    error_message=error,
                )
            )
        except Exception as db_error:
            logger.error(f"Failed to mark asset {asset_id} as failed: {db_error}")

    def _get_sync_stats(self, deleted_count: int) -> Dict[str, Any]:
        """Get synchronization statistics."""
        total_assets = self.database.get_asset_count()
        downloaded_assets = self.database.get_downloaded_count()
        disk_usage = self.file_manager.get_disk_usage()

        return {
            "total_assets": total_assets,
            "downloaded_assets": downloaded_assets,
            "failed_assets": total_assets - downloaded_assets,
            "deleted_assets": deleted_count,
            "disk_usage_bytes": disk_usage,
            "disk_usage_mb": disk_usage / (1024 * 1024),
            "disk_usage_gb": disk_usage / (1024 * 1024 * 1024),
        }

    def cleanup(self) -> None:
        """Clean up resources."""
        self.download_manager.cleanup()
        self.database.close()
