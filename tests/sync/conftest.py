"""Shared fixtures and factories for sync architecture tests."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator
from unittest.mock import Mock

import pytest

from icloudpd.sync.database import (
    AlbumRecord,
    Clock,
    FolderRecord,
    ICloudAssetRecord,
    LocalFileRecord,
    PhotoDatabase,
    SyncState,
    SyncStatus,
)
from icloudpd.sync.file_manager import AssetFileRef, FileManager
from icloudpd.sync.filesystem_sync import FilesystemSync
from icloudpd.sync.photo_asset_record_mapper import PhotoAssetRecordMapper
from icloudpd.sync.progress_reporter import LoggingProgressReporter
from icloudpd.sync.sync_manager import SyncManager
from icloudpd.sync.sync_strategy import PhotosToSync
from pyicloud_ipd.asset_version import AssetVersion
from pyicloud_ipd.item_type import AssetItemType
from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize


class FakeClock:
    """Deterministic clock for tests."""

    def __init__(self, start: str = "2024-01-01T00:00:00"):
        self._now = start

    def now(self) -> str:
        return self._now

    def advance(self, new_time: str) -> None:
        self._now = new_time


@dataclass
class SyncEnv:
    """All components wired together for integration tests."""

    temp_dir: Path
    database: PhotoDatabase
    file_manager: FileManager
    filesystem_sync: FilesystemSync
    mapper: PhotoAssetRecordMapper
    progress_reporter: LoggingProgressReporter
    clock: FakeClock

    def make_sync_manager(
        self,
        photo_library: Mock | None = None,
        download_manager: Mock | None = None,
    ) -> SyncManager:
        dm = download_manager or _make_noop_download_manager()
        return SyncManager(
            base_directory=self.temp_dir,
            database=self.database,
            file_manager=self.file_manager,
            mapper=self.mapper,
            download_manager=dm,
            filesystem_sync=self.filesystem_sync,
            progress_reporter=self.progress_reporter,
            photo_library=photo_library,
            clock=self.clock,
        )


@pytest.fixture
def sync_env() -> Iterator[SyncEnv]:
    temp_dir = Path(tempfile.mkdtemp())
    clock = FakeClock()
    database = PhotoDatabase(temp_dir, clock=clock)
    file_manager = FileManager(temp_dir)
    filesystem_sync = FilesystemSync(temp_dir, database)
    mapper = PhotoAssetRecordMapper()
    progress_reporter = LoggingProgressReporter()

    env = SyncEnv(
        temp_dir=temp_dir,
        database=database,
        file_manager=file_manager,
        filesystem_sync=filesystem_sync,
        mapper=mapper,
        progress_reporter=progress_reporter,
        clock=clock,
    )
    yield env
    shutil.rmtree(temp_dir)


# -- Factories ----------------------------------------------------------------


def make_asset(
    asset_id: str = "asset1",
    filename: str = "IMG_0001.JPG",
    created: datetime | None = None,
    added: datetime | None = None,
    versions: dict | None = None,
    item_type: AssetItemType = AssetItemType.IMAGE,
) -> Mock:
    """Create a mock PhotoAsset with realistic structure."""
    if created is None:
        created = datetime(2024, 1, 15, 14, 30, 22)
    if added is None:
        added = created

    mock = Mock()
    mock.id = asset_id
    mock.filename = filename
    mock.item_type = item_type
    mock.item_type_extension = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
    mock.created = created
    mock.added_date = added
    mock.asset_date = created
    mock.dimensions = (4032, 3024)
    mock._master_record = {"recordName": asset_id, "fields": {}}
    mock._asset_record = {"fields": {}}

    if versions is None:
        versions = {
            AssetVersionSize.ORIGINAL: AssetVersion(
                size=4_500_000,
                url=f"https://icloud.com/{asset_id}/original",
                type="public.jpeg",
                checksum="abc123",
            ),
        }
    mock.versions = versions
    return mock


def make_folder(
    record_name: str,
    name: str,
    parent_id: str | None = None,
    children: list | None = None,
) -> Mock:
    """Create a mock PhotoFolder."""
    mock = Mock()
    mock.record_name = record_name
    mock.name = name
    mock.parent_id = parent_id
    mock.children_folders = children or []
    return mock


def make_album(
    name: str,
    record_name: str | None = None,
    parent_folder_id: str | None = None,
    assets: list[Mock] | None = None,
    obj_type: str = "regular",
    list_type: str = "default",
) -> Mock:
    """Create a mock PhotoAlbum."""
    mock = Mock()
    mock.name = name
    mock.record_name = record_name
    mock.parent_folder_id = parent_folder_id
    mock.obj_type = obj_type
    mock.list_type = list_type

    asset_list = assets or []
    mock.__iter__ = lambda self: iter(asset_list)
    mock.__len__ = lambda self: len(asset_list)
    return mock


def make_photo_library(
    folders: list[Mock] | None = None,
    albums: dict[str, Mock] | None = None,
    all_assets: list[Mock] | None = None,
) -> Mock:
    """Create a mock PhotoLibrary."""
    lib = Mock()
    lib.folders = folders or []
    lib.albums = albums or {}

    all_album = Mock()
    asset_list = all_assets or []
    all_album.__iter__ = lambda self: iter(asset_list)
    all_album.__len__ = lambda self: len(asset_list)
    lib.all = all_album
    return lib


class MockPhotosToSync(PhotosToSync):
    """Test PhotosToSync that wraps a list of mock assets."""

    def __init__(self, assets: list[Mock], covers_full: bool = True):
        self._assets = assets
        self._covers_full = covers_full

    def __iter__(self) -> Iterator:
        return iter(self._assets)

    def __len__(self) -> int:
        return len(self._assets)

    @property
    def covers_full_library(self) -> bool:
        return self._covers_full


def seed_downloaded_asset(env: SyncEnv, asset: Mock) -> None:
    """Insert an asset into DB as fully downloaded and write a placeholder file to _data/."""
    record = env.mapper.map_icloud_metadata(asset, env.clock.now())
    env.database.upsert_icloud_metadata(record)

    for version_size in asset.versions:
        asset_ref = AssetFileRef(asset_id=asset.id, filename=asset.filename)
        file_path = env.file_manager.get_file_path(asset_ref, version_size)
        file_path.write_bytes(b"fake-content")

        local_file = LocalFileRecord(
            asset_id=asset.id,
            version_type=version_size.value,
            local_filename=file_path.name,
            file_path=str(file_path.relative_to(env.temp_dir)),
            file_size=len(b"fake-content"),
            download_date=env.clock.now(),
        )
        env.database.upsert_local_file(local_file)
        env.database.upsert_sync_status(
            SyncStatus(
                asset_id=asset.id,
                version_type=version_size.value,
                sync_status=SyncState.COMPLETED,
            )
        )


def _make_noop_download_manager() -> Mock:
    """DownloadManager that returns empty results (no actual downloads)."""
    dm = Mock()
    dm.download_asset_versions.return_value = ([], [])
    dm.cleanup.return_value = None
    return dm


def make_downloading_manager(env: SyncEnv) -> Mock:
    """DownloadManager that writes real placeholder files to _data/ on download."""

    def download_asset_versions(asset_record, icloud_asset, versions_to_download):
        downloaded = []
        for version in versions_to_download:
            asset_ref = AssetFileRef(asset_id=icloud_asset.id, filename=icloud_asset.filename)
            file_path = env.file_manager.get_file_path(asset_ref, version)
            file_path.write_bytes(b"downloaded-content")
            downloaded.append(version)
        return downloaded, []

    dm = Mock()
    dm.download_asset_versions.side_effect = download_asset_versions
    dm.cleanup.return_value = None
    return dm


def collect_symlinks(base_dir: Path, subdir: str) -> dict[str, str]:
    """Collect all symlinks under base_dir/subdir as {relative_path: target}."""
    root = base_dir / subdir
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for path in root.rglob("*"):
        if path.is_symlink():
            rel = str(path.relative_to(root))
            result[rel] = str(path.readlink())
    return result
