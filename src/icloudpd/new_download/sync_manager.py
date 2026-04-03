"""Main orchestration for the new download architecture."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from pyicloud_ipd.services.photos import (
    PhotoAlbum,
    PhotoAsset,
    PhotoFolder,
    PhotoLibrary,
)
from pyicloud_ipd.session import PyiCloudSession
from pyicloud_ipd.version_size import VersionSize

from .constants import DOWNLOAD_VERSIONS
from .database import (
    AlbumRecord,
    FolderRecord,
    ICloudAssetRecord,
    LocalFileRecord,
    PhotoDatabase,
    SyncStatus,
    UpsertResult,
)
from .download_manager import DownloadManager
from .file_manager import FileManager
from .photo_asset_record_mapper import PhotoAssetRecordMapper
from .progress_reporter import ProgressReporter, TerminalProgressReporter
from .sync_strategy import PhotosToSync

logger = logging.getLogger(__name__)


class SyncManager:
    """Main orchestration class for downloading iCloud photos."""

    def __init__(
        self,
        base_directory: Path,
        session: PyiCloudSession,
        photo_library: PhotoLibrary | None = None,
        progress_reporter: ProgressReporter | None = None,
    ):
        self.base_directory = Path(base_directory)
        self.photo_library = photo_library
        self.progress_reporter = progress_reporter or TerminalProgressReporter()

        self.database = PhotoDatabase(self.base_directory)
        self.file_manager = FileManager(self.base_directory)
        self.mapper = PhotoAssetRecordMapper()
        self.download_manager = DownloadManager(self.file_manager, session)
        self._deleted_count: int = 0

    def sync_photos(self, photos_to_sync: PhotosToSync) -> Dict[str, Any]:
        """Main sync method: metadata collection then download."""
        logger.info("Starting photo sync with phased approach...")

        cleaned_count = self.file_manager.cleanup_incomplete_downloads()
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} incomplete downloads")

        phase1_stats, asset_map = self._phase1_metadata_collection(photos_to_sync)

        if self.photo_library is not None:
            self._phase4_album_sync(self.photo_library)

        if photos_to_sync.covers_full_library:
            self._phase3_mirror_deletions(asset_map)
        else:
            logger.debug("Skipping deletion detection: strategy does not cover full library.")

        self._phase2_download_assets(asset_map)

        final_stats = self._get_sync_stats()
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

        for i, asset in enumerate(photos_to_sync):
            try:
                asset_map[asset.id] = asset
                icloud_metadata = self.mapper.map_icloud_metadata(asset)

                upsert_result = self.database.upsert_icloud_metadata(icloud_metadata)
                if upsert_result.operation == UpsertResult.INSERTED:
                    new_assets += 1
                else:
                    updated_assets += 1

                sync_statuses = self._determine_download_needs(asset, icloud_metadata)
                for status in sync_statuses:
                    self.database.upsert_sync_status(status)

                processed_count += 1
            except Exception as e:
                logger.error(f"Failed to process metadata for asset {asset.id}: {e}")
                failed_assets += 1
                self._mark_asset_failed(asset.id, "original", str(e))

            self.progress_reporter.phase_progress(i + 1, total_photos)

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
        self, asset: PhotoAsset, icloud_metadata: ICloudAssetRecord
    ) -> List[SyncStatus]:
        """Determine which versions need downloading for an asset."""
        sync_statuses: List[SyncStatus] = []

        available_versions = set(icloud_metadata.asset_versions.keys())
        existing_statuses = self.database.get_all_sync_statuses(asset.id)
        local_files = self.database.get_local_files(asset.id)
        local_version_types = {f.version_type for f in local_files}

        desired_versions = [vs.value for vs in DOWNLOAD_VERSIONS if vs.value in available_versions]

        for version_type in desired_versions:
            existing = next((s for s in existing_statuses if s.version_type == version_type), None)
            if existing and existing.sync_status == "completed":
                continue

            if version_type in local_version_types:
                sync_statuses.append(
                    SyncStatus(
                        asset_id=asset.id,
                        version_type=version_type,
                        sync_status="completed",
                    )
                )
            else:
                sync_statuses.append(
                    SyncStatus(
                        asset_id=asset.id,
                        version_type=version_type,
                        sync_status="metadata_processed",
                    )
                )

        return sync_statuses

    # -- Phase 2: Download assets -----------------------------------------------

    def _phase2_download_assets(self, asset_map: Dict[str, PhotoAsset]) -> Dict[str, Any]:
        """Download assets that have metadata_processed status."""
        logger.info("Phase 2: Starting asset downloads...")

        assets_to_download = self.database.get_assets_needing_download()
        total_assets = len(assets_to_download)

        if total_assets == 0:
            logger.info("No assets need downloading")
            return {"downloaded": 0, "failed": 0, "skipped": 0}

        self.progress_reporter.phase_start("Phase 2: Downloading assets", total_assets)

        downloaded_count = 0
        failed_count = 0
        skipped_count = 0

        for i, asset_id in enumerate(assets_to_download):
            try:
                result = self._download_single_asset(asset_id, asset_map)
                if result == "downloaded":
                    downloaded_count += 1
                elif result == "failed":
                    failed_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                logger.error(f"Failed to download asset {asset_id}: {e}")
                failed_count += 1
                self._mark_asset_failed(asset_id, "original", str(e))

            self.progress_reporter.phase_progress(i + 1, total_assets)

        phase2_stats = {
            "downloaded": downloaded_count,
            "failed": failed_count,
            "skipped": skipped_count,
        }
        self.progress_reporter.phase_complete("Phase 2: Downloading assets", phase2_stats)
        logger.info(
            f"Phase 2 completed: {downloaded_count} downloaded, "
            f"{failed_count} failed, {skipped_count} skipped"
        )
        return phase2_stats

    def _download_single_asset(self, asset_id: str, asset_map: Dict[str, PhotoAsset]) -> str:
        """Download all pending versions of a single asset.

        Returns:
            "downloaded", "failed", or "skipped"
        """
        icloud_asset = asset_map.get(asset_id)
        if not icloud_asset:
            logger.warning(f"Asset {asset_id} not found in asset map")
            self._mark_asset_failed(asset_id, "original", "Asset not found in asset map")
            return "failed"

        metadata = self.database.get_icloud_metadata(asset_id)
        if not metadata:
            logger.warning(f"Asset {asset_id} not found in database")
            return "failed"

        # Resolve which VersionSize enums to download
        versions_to_download = self._resolve_pending_versions(asset_id, icloud_asset)
        if not versions_to_download:
            return "skipped"

        # Use DownloadManager for authenticated, parallel download
        downloaded_values, failed_values = self.download_manager.download_asset_versions(
            metadata, icloud_asset, versions_to_download
        )

        # Record results
        self._record_download_results(asset_id, icloud_asset, downloaded_values, failed_values)

        if failed_values:
            return "failed"
        return "downloaded"

    def _resolve_pending_versions(
        self, asset_id: str, icloud_asset: PhotoAsset
    ) -> List[VersionSize]:
        """Get VersionSize enums for versions that need downloading."""
        pending_statuses = [
            s
            for s in self.database.get_all_sync_statuses(asset_id)
            if s.sync_status == "metadata_processed"
        ]
        versions: List[VersionSize] = []
        for status in pending_statuses:
            version_size = self._resolve_version_size(status.version_type, icloud_asset)
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
        downloaded_values: List[str],
        failed_values: List[str],
    ) -> None:
        """Record download results in the database."""
        for version_value in downloaded_values:
            version_size = self._resolve_version_size(version_value, icloud_asset)
            if version_size is None:
                continue

            file_path = self.file_manager.get_file_path(icloud_asset, version_size)
            file_size = self.file_manager.get_file_size(icloud_asset, version_size) or 0

            self.database.upsert_local_file(
                LocalFileRecord(
                    asset_id=asset_id,
                    version_type=version_value,
                    local_filename=file_path.name,
                    file_path=str(file_path.relative_to(self.base_directory)),
                    file_size=file_size,
                    download_date=datetime.now().isoformat(),
                    checksum=None,
                )
            )
            self.database.upsert_sync_status(
                SyncStatus(
                    asset_id=asset_id,
                    version_type=version_value,
                    sync_status="completed",
                )
            )

        for version_value in failed_values:
            self.database.upsert_sync_status(
                SyncStatus(
                    asset_id=asset_id,
                    version_type=version_value,
                    sync_status="failed",
                    error_message="Download failed",
                )
            )

    # -- Phase 3: Mirror deletions ----------------------------------------------

    def _phase3_mirror_deletions(self, asset_map: Dict[str, PhotoAsset]) -> None:
        """Delete local files for assets no longer present in iCloud."""
        logger.info("Phase 3: Starting deletion detection...")

        deleted_ids = set(self.database.get_all_asset_ids()) - set(asset_map.keys())
        detected_on = datetime.now(timezone.utc).isoformat()

        for asset_id in deleted_ids:
            self.file_manager.delete_asset_files(asset_id)
            self.database.mark_asset_deleted(asset_id, detected_on)
            logger.debug(f"Mirrored deletion of asset {asset_id}")

        self._deleted_count = len(deleted_ids)
        logger.info(f"Phase 3 completed: {self._deleted_count} assets removed (deleted from iCloud)")

    # -- Phase 4: Album and folder sync ------------------------------------------

    def _phase4_album_sync(self, photo_library: PhotoLibrary) -> dict[str, Any]:
        """Sync folders, albums, and album membership from iCloud."""
        logger.info("Phase 4: Starting album and folder sync...")

        folder_tree = photo_library.folders
        albums_dict = photo_library.albums

        synced_folder_ids = self._sync_folder_tree(folder_tree)
        synced_album_ids, total_memberships = self._sync_albums(albums_dict)
        deleted_folders = self._detect_folder_deletions(synced_folder_ids)
        deleted_albums = self._detect_album_deletions(synced_album_ids)

        stats = {
            "folders": len(synced_folder_ids),
            "albums": len(synced_album_ids),
            "memberships": total_memberships,
            "deleted_folders": deleted_folders,
            "deleted_albums": deleted_albums,
        }
        logger.info(
            f"Phase 4 completed: {len(synced_folder_ids)} folders, "
            f"{len(synced_album_ids)} albums, {total_memberships} memberships"
        )
        return stats

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
        self.progress_reporter.phase_start("Phase 4: Album sync", total_albums)

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

        self.progress_reporter.phase_complete("Phase 4: Album sync", {})
        return synced_ids, total_memberships

    def _detect_folder_deletions(self, synced_ids: set[str]) -> int:
        """Tombstone folders no longer present in iCloud."""
        existing_ids = set(self.database.get_all_folder_ids())
        deleted_ids = existing_ids - synced_ids
        detected_on = datetime.now(timezone.utc).isoformat()
        for folder_id in deleted_ids:
            self.database.mark_folder_deleted(folder_id, detected_on)
        return len(deleted_ids)

    def _detect_album_deletions(self, synced_ids: set[str]) -> int:
        """Tombstone albums no longer present in iCloud."""
        existing_ids = set(self.database.get_all_album_ids())
        deleted_ids = existing_ids - synced_ids
        detected_on = datetime.now(timezone.utc).isoformat()
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

    # -- Helpers ----------------------------------------------------------------

    @staticmethod
    def _resolve_version_size(version_key: str, icloud_asset: PhotoAsset) -> VersionSize | None:
        """Map a version key string back to a VersionSize enum."""
        for version_size in icloud_asset.versions:
            if version_size.value == version_key:
                return version_size
        return None

    def _mark_asset_failed(self, asset_id: str, version_type: str, error: str) -> None:
        """Mark an asset version as failed in the database."""
        try:
            self.database.upsert_sync_status(
                SyncStatus(
                    asset_id=asset_id,
                    version_type=version_type,
                    sync_status="failed",
                    error_message=error,
                )
            )
        except Exception as db_error:
            logger.error(f"Failed to mark asset {asset_id} as failed: {db_error}")

    def _get_sync_stats(self) -> Dict[str, Any]:
        """Get synchronization statistics."""
        total_assets = self.database.get_asset_count()
        downloaded_assets = self.database.get_downloaded_count()
        disk_usage = self.file_manager.get_disk_usage()

        return {
            "total_assets": total_assets,
            "downloaded_assets": downloaded_assets,
            "failed_assets": total_assets - downloaded_assets,
            "deleted_assets": self._deleted_count,
            "disk_usage_bytes": disk_usage,
            "disk_usage_mb": disk_usage / (1024 * 1024),
            "disk_usage_gb": disk_usage / (1024 * 1024 * 1024),
        }

    def cleanup(self) -> None:
        """Clean up resources."""
        self.download_manager.cleanup()
