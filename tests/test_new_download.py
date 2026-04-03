"""Tests for the new download architecture."""

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from icloudpd.new_download.database import (
    AlbumRecord,
    FolderRecord,
    ICloudAssetRecord,
    LocalFileRecord,
    PhotoDatabase,
    SyncStatus,
    UpsertResult,
)
from icloudpd.new_download.file_manager import FileManager
from icloudpd.new_download.photo_asset_record_mapper import PhotoAssetRecordMapper
from icloudpd.new_download.sync_manager import SyncManager
from icloudpd.new_download.sync_strategy import NoOpStrategy, RecentPhotosStrategy, SinceDateStrategy
from pyicloud_ipd.asset_version import AssetVersion
from pyicloud_ipd.item_type import AssetItemType
from pyicloud_ipd.version_size import AssetVersionSize


def _make_mock_asset(
    asset_id: str = "AYk6Y+tESR",
    filename: str = "IMG_7409.JPG",
) -> Mock:
    """Create a mock PhotoAsset with realistic structure."""
    mock = Mock()
    mock.id = asset_id
    mock.filename = filename
    mock.item_type = AssetItemType.IMAGE
    mock.item_type_extension = "JPG"
    mock.created = datetime(2024, 1, 15, 10, 30, 0)
    mock.added_date = datetime(2024, 1, 15, 10, 30, 0)
    mock.asset_date = datetime(2024, 1, 15, 10, 30, 0)
    mock.dimensions = (4032, 3024)
    mock._master_record = {"recordName": asset_id, "fields": {}}
    mock._asset_record = {"fields": {}}
    mock.versions = {
        AssetVersionSize.ORIGINAL: AssetVersion(
            size=4_500_000,
            url="https://cvws.icloud-content.com/B/original/IMG_7409.JPG",
            type="public.jpeg",
            checksum="abc123",
        ),
        AssetVersionSize.ADJUSTED: AssetVersion(
            size=2_100_000,
            url="https://cvws.icloud-content.com/B/adjusted/IMG_7409.JPG",
            type="public.jpeg",
            checksum="def456",
        ),
    }
    return mock


class TestPhotoDatabase:
    """Test database operations."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db = PhotoDatabase(Path(self.temp_dir))

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir)

    def test_upsert_and_get_icloud_metadata(self) -> None:
        record = ICloudAssetRecord(
            asset_id="test123",
            filename="test.jpg",
            asset_type="image",
            created_date="2024-01-01T00:00:00",
            added_date="2024-01-01T00:00:00",
            width=1920,
            height=1080,
            asset_versions={
                "original": {
                    "size": 1024,
                    "url": "https://example.com/test.jpg",
                    "type": "public.jpeg",
                }
            },
            master_record={"test": "data"},
            asset_record={"test": "data"},
        )

        result = self.db.upsert_icloud_metadata(record)
        assert result.operation == UpsertResult.INSERTED

        retrieved = self.db.get_icloud_metadata("test123")
        assert retrieved is not None
        assert retrieved.asset_id == "test123"
        assert retrieved.filename == "test.jpg"
        assert "original" in retrieved.asset_versions

    def test_upsert_icloud_metadata_update(self) -> None:
        record = ICloudAssetRecord(asset_id="test123", filename="test.jpg")
        self.db.upsert_icloud_metadata(record)

        record2 = ICloudAssetRecord(asset_id="test123", filename="test_updated.jpg")
        result = self.db.upsert_icloud_metadata(record2)
        assert result.operation == UpsertResult.UPDATED

        retrieved = self.db.get_icloud_metadata("test123")
        assert retrieved is not None
        assert retrieved.filename == "test_updated.jpg"

    def test_upsert_and_get_sync_status(self) -> None:
        record = ICloudAssetRecord(asset_id="test123", filename="test.jpg")
        self.db.upsert_icloud_metadata(record)

        status = SyncStatus(
            asset_id="test123",
            version_type="original",
            sync_status="metadata_processed",
        )
        self.db.upsert_sync_status(status)

        retrieved = self.db.get_sync_status("test123", "original")
        assert retrieved is not None
        assert retrieved.sync_status == "metadata_processed"

    def test_get_assets_needing_download(self) -> None:
        record = ICloudAssetRecord(asset_id="test123", filename="test.jpg")
        self.db.upsert_icloud_metadata(record)

        status = SyncStatus(
            asset_id="test123",
            version_type="original",
            sync_status="metadata_processed",
        )
        self.db.upsert_sync_status(status)

        assets = self.db.get_assets_needing_download()
        assert "test123" in assets

    def test_upsert_and_get_local_file(self) -> None:
        record = ICloudAssetRecord(asset_id="test123", filename="test.jpg")
        self.db.upsert_icloud_metadata(record)

        local_file = LocalFileRecord(
            asset_id="test123",
            version_type="original",
            local_filename="test123-original.jpg",
            file_path="_data/test123-original.jpg",
            file_size=1024,
            download_date="2024-01-01T00:00:00",
        )
        self.db.upsert_local_file(local_file)

        files = self.db.get_local_files("test123")
        assert len(files) == 1
        assert files[0].version_type == "original"
        assert files[0].file_size == 1024

    def test_get_asset_count(self) -> None:
        assert self.db.get_asset_count() == 0

        self.db.upsert_icloud_metadata(ICloudAssetRecord(asset_id="a1", filename="a.jpg"))
        self.db.upsert_icloud_metadata(ICloudAssetRecord(asset_id="a2", filename="b.jpg"))
        assert self.db.get_asset_count() == 2


class TestFileManager:
    """Test file operations."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.file_manager = FileManager(Path(self.temp_dir))

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir)

    def test_get_file_path(self) -> None:
        mock_asset = _make_mock_asset()
        path = self.file_manager.get_file_path(mock_asset, AssetVersionSize.ORIGINAL)

        assert path.parent == Path(self.temp_dir) / "_data"
        assert path.suffix == ".jpg"
        assert "original" in path.name

    def test_save_and_check_file(self) -> None:
        mock_asset = _make_mock_asset()
        content = b"test file content"
        success = self.file_manager.save_file(mock_asset, AssetVersionSize.ORIGINAL, content)

        assert success is True
        assert self.file_manager.file_exists(mock_asset, AssetVersionSize.ORIGINAL) is True

    def test_get_file_size(self) -> None:
        mock_asset = _make_mock_asset()
        content = b"test file content"
        self.file_manager.save_file(mock_asset, AssetVersionSize.ORIGINAL, content)

        size = self.file_manager.get_file_size(mock_asset, AssetVersionSize.ORIGINAL)
        assert size == len(content)

    def test_cleanup_incomplete_downloads(self) -> None:
        data_dir = Path(self.temp_dir) / "_data"
        (data_dir / "partial_download.jpg.tmp").write_bytes(b"partial")

        cleaned = self.file_manager.cleanup_incomplete_downloads()
        assert cleaned == 1
        assert not (data_dir / "partial_download.jpg.tmp").exists()


class TestPhotoAssetRecordMapper:
    """Test asset mapping from PhotoAsset to database records."""

    def test_map_icloud_metadata(self) -> None:
        mock_asset = _make_mock_asset()
        mapper = PhotoAssetRecordMapper()
        record = mapper.map_icloud_metadata(mock_asset)

        assert record.asset_id == "AYk6Y+tESR"
        assert record.filename == "IMG_7409.JPG"
        assert record.width == 4032
        assert record.height == 3024
        assert "original" in record.asset_versions
        assert "adjusted" in record.asset_versions

    def test_map_sync_statuses(self) -> None:
        mock_asset = _make_mock_asset()
        mapper = PhotoAssetRecordMapper()
        statuses = mapper.map_sync_statuses(mock_asset)

        assert len(statuses) == 2
        assert all(s.sync_status == "pending" for s in statuses)
        version_types = {s.version_type for s in statuses}
        assert "original" in version_types
        assert "adjusted" in version_types


class TestSyncManager:
    """Test sync manager."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.sync_manager = SyncManager(Path(self.temp_dir), Mock())

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir)
        self.sync_manager.cleanup()

    def test_get_sync_stats_empty(self) -> None:
        stats = self.sync_manager._get_sync_stats()

        assert stats["total_assets"] == 0
        assert stats["downloaded_assets"] == 0
        assert stats["failed_assets"] == 0
        assert stats["disk_usage_bytes"] == 0

    def test_resolve_version_size(self) -> None:
        mock_asset = _make_mock_asset()

        resolved = SyncManager._resolve_version_size("original", mock_asset)
        assert resolved == AssetVersionSize.ORIGINAL

        resolved = SyncManager._resolve_version_size("adjusted", mock_asset)
        assert resolved == AssetVersionSize.ADJUSTED

        resolved = SyncManager._resolve_version_size("nonexistent", mock_asset)
        assert resolved is None

    def test_phase3_removes_files_and_tombstones_db(self) -> None:
        """Assets in DB but absent from asset_map are deleted locally and tombstoned."""
        db = self.sync_manager.database
        fm = self.sync_manager.file_manager

        # Seed two assets in DB
        db.upsert_icloud_metadata(ICloudAssetRecord(asset_id="keep-1", filename="keep.jpg"))
        db.upsert_icloud_metadata(ICloudAssetRecord(asset_id="gone-1", filename="gone.jpg"))
        db.upsert_icloud_metadata(ICloudAssetRecord(asset_id="gone-2", filename="gone2.jpg"))

        # Write a fake local file for each deleted asset
        (fm.data_directory / "Z29uZS0x-original.jpg").write_bytes(b"data")
        (fm.data_directory / "Z29uZS0y-original.jpg").write_bytes(b"data")

        # asset_map only contains "keep-1" — simulates iCloud after deletion
        asset_map = {"keep-1": _make_mock_asset("keep-1", "keep.jpg")}

        self.sync_manager._phase3_mirror_deletions(asset_map)

        assert self.sync_manager._deleted_count == 2

        # Local files removed
        assert not (fm.data_directory / "Z29uZS0x-original.jpg").exists()
        assert not (fm.data_directory / "Z29uZS0y-original.jpg").exists()

        # Tombstones in DB
        assert db.get_deleted_asset_count() == 2
        gone1 = db.get_icloud_metadata("gone-1")
        assert gone1 is not None
        assert gone1.deleted
        assert gone1.deletion_detected_on is not None

        # Surviving asset untouched
        keep = db.get_icloud_metadata("keep-1")
        assert keep is not None
        assert not keep.deleted

    def test_phase3_skips_already_tombstoned(self) -> None:
        """Assets already tombstoned are not counted again on subsequent runs."""
        db = self.sync_manager.database

        db.upsert_icloud_metadata(ICloudAssetRecord(asset_id="gone-1", filename="gone.jpg"))
        detected_on = datetime.now(timezone.utc).isoformat()
        db.mark_asset_deleted("gone-1", detected_on)

        # Second run: asset still absent from iCloud
        self.sync_manager._phase3_mirror_deletions({})

        # Already tombstoned — not counted as a new deletion
        assert self.sync_manager._deleted_count == 0

    def test_phase3_noop_when_nothing_deleted(self) -> None:
        db = self.sync_manager.database
        db.upsert_icloud_metadata(ICloudAssetRecord(asset_id="keep-1", filename="keep.jpg"))

        asset_map = {"keep-1": _make_mock_asset("keep-1", "keep.jpg")}
        self.sync_manager._phase3_mirror_deletions(asset_map)

        assert self.sync_manager._deleted_count == 0
        assert db.get_deleted_asset_count() == 0


class TestDeletionDatabase:
    """Test deletion-specific database operations."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db = PhotoDatabase(Path(self.temp_dir))

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir)

    def _seed_asset(self, asset_id: str) -> None:
        self.db.upsert_icloud_metadata(ICloudAssetRecord(asset_id=asset_id, filename=f"{asset_id}.jpg"))

    def test_get_all_asset_ids_excludes_tombstoned(self) -> None:
        self._seed_asset("a1")
        self._seed_asset("a2")
        detected_on = datetime.now(timezone.utc).isoformat()
        self.db.mark_asset_deleted("a2", detected_on)

        ids = self.db.get_all_asset_ids()
        assert "a1" in ids
        assert "a2" not in ids

    def test_mark_asset_deleted_tombstones_and_removes_children(self) -> None:
        self._seed_asset("a1")
        self.db.upsert_sync_status(SyncStatus(asset_id="a1", version_type="original", sync_status="completed"))
        self.db.upsert_local_file(LocalFileRecord(
            asset_id="a1", version_type="original", local_filename="a1.jpg",
            file_path="_data/a1.jpg", file_size=100, download_date="2024-01-01",
        ))

        detected_on = datetime.now(timezone.utc).isoformat()
        self.db.mark_asset_deleted("a1", detected_on)

        record = self.db.get_icloud_metadata("a1")
        assert record is not None
        assert record.deleted
        assert record.deletion_detected_on == detected_on

        # Child records removed
        assert self.db.get_all_sync_statuses("a1") == []
        assert self.db.get_local_files("a1") == []

    def test_get_deleted_asset_count(self) -> None:
        self._seed_asset("a1")
        self._seed_asset("a2")
        self._seed_asset("a3")

        detected_on = datetime.now(timezone.utc).isoformat()
        self.db.mark_asset_deleted("a1", detected_on)
        self.db.mark_asset_deleted("a2", detected_on)

        assert self.db.get_deleted_asset_count() == 2


class TestDeleteAssetFiles:
    """Test FileManager.delete_asset_files."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.fm = FileManager(Path(self.temp_dir))

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir)

    def test_deletes_all_versions_for_asset(self) -> None:
        import base64
        asset_id = "test-asset-1"
        encoded = base64.urlsafe_b64encode(asset_id.encode()).decode().rstrip("=")

        (self.fm.data_directory / f"{encoded}-original.jpg").write_bytes(b"data")
        (self.fm.data_directory / f"{encoded}-medium.jpg").write_bytes(b"data")

        deleted = self.fm.delete_asset_files(asset_id)

        assert deleted == 2
        assert not (self.fm.data_directory / f"{encoded}-original.jpg").exists()
        assert not (self.fm.data_directory / f"{encoded}-medium.jpg").exists()

    def test_does_not_delete_other_assets(self) -> None:
        import base64
        asset_a = "asset-a"
        asset_b = "asset-b"
        enc_a = base64.urlsafe_b64encode(asset_a.encode()).decode().rstrip("=")
        enc_b = base64.urlsafe_b64encode(asset_b.encode()).decode().rstrip("=")

        (self.fm.data_directory / f"{enc_a}-original.jpg").write_bytes(b"data")
        (self.fm.data_directory / f"{enc_b}-original.jpg").write_bytes(b"data")

        self.fm.delete_asset_files(asset_a)

        assert not (self.fm.data_directory / f"{enc_a}-original.jpg").exists()
        assert (self.fm.data_directory / f"{enc_b}-original.jpg").exists()

    def test_returns_zero_when_no_files(self) -> None:
        deleted = self.fm.delete_asset_files("nonexistent-asset")
        assert deleted == 0


class TestCoversFullLibrary:
    """Test covers_full_library property on each strategy."""

    def test_noop_strategy_covers_full_library(self) -> None:
        assert NoOpStrategy.covers_full_library is True

    def test_recent_strategy_does_not_cover_full_library(self) -> None:
        assert RecentPhotosStrategy.covers_full_library is False

    def test_since_date_strategy_does_not_cover_full_library(self) -> None:
        assert SinceDateStrategy.covers_full_library is False


class TestFolderDatabase:
    """Test folder table operations."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db = PhotoDatabase(Path(self.temp_dir))

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir)

    def test_upsert_and_get_folder(self) -> None:
        folder = FolderRecord(folder_id="folder-1", folder_name="Travel")
        result = self.db.upsert_folder(folder)
        assert result.operation == UpsertResult.INSERTED
        assert result.record.folder_name == "Travel"
        assert result.record.folder_id == "folder-1"

    def test_upsert_folder_update(self) -> None:
        self.db.upsert_folder(FolderRecord(folder_id="f1", folder_name="Old"))
        result = self.db.upsert_folder(FolderRecord(folder_id="f1", folder_name="New"))
        assert result.operation == UpsertResult.UPDATED
        assert result.record.folder_name == "New"

    def test_nested_folders(self) -> None:
        self.db.upsert_folder(FolderRecord(folder_id="parent", folder_name="Parent"))
        self.db.upsert_folder(
            FolderRecord(folder_id="child", folder_name="Child", parent_folder_id="parent")
        )
        ids = self.db.get_all_folder_ids()
        assert set(ids) == {"parent", "child"}

    def test_mark_folder_deleted(self) -> None:
        self.db.upsert_folder(FolderRecord(folder_id="f1", folder_name="Gone"))
        self.db.mark_folder_deleted("f1", "2024-01-01T00:00:00Z")
        assert self.db.get_all_folder_ids() == []

    def test_upsert_resurrects_deleted_folder(self) -> None:
        self.db.upsert_folder(FolderRecord(folder_id="f1", folder_name="F"))
        self.db.mark_folder_deleted("f1", "2024-01-01T00:00:00Z")
        assert self.db.get_all_folder_ids() == []
        self.db.upsert_folder(FolderRecord(folder_id="f1", folder_name="F"))
        assert self.db.get_all_folder_ids() == ["f1"]


class TestAlbumDatabase:
    """Test album and album_assets table operations."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db = PhotoDatabase(Path(self.temp_dir))

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir)

    def test_upsert_and_get_album(self) -> None:
        album = AlbumRecord(
            album_id="user:abc", album_name="Vacation", album_type="user"
        )
        result = self.db.upsert_album(album)
        assert result.operation == UpsertResult.INSERTED
        assert result.record.album_name == "Vacation"

    def test_upsert_album_update(self) -> None:
        self.db.upsert_album(
            AlbumRecord(album_id="user:abc", album_name="Old", album_type="user")
        )
        result = self.db.upsert_album(
            AlbumRecord(album_id="user:abc", album_name="New", album_type="user")
        )
        assert result.operation == UpsertResult.UPDATED
        assert result.record.album_name == "New"

    def test_smart_album(self) -> None:
        album = AlbumRecord(
            album_id="smart:Favorites", album_name="Favorites", album_type="smart"
        )
        result = self.db.upsert_album(album)
        assert result.operation == UpsertResult.INSERTED

    def test_album_with_folder(self) -> None:
        self.db.upsert_folder(FolderRecord(folder_id="f1", folder_name="Folder"))
        album = AlbumRecord(
            album_id="user:a1", album_name="Inside", album_type="user", folder_id="f1"
        )
        result = self.db.upsert_album(album)
        assert result.record.folder_id == "f1"

    def test_replace_album_assets(self) -> None:
        self.db.upsert_album(
            AlbumRecord(album_id="a1", album_name="Test", album_type="user")
        )
        # Insert assets so FK is satisfied
        for aid in ["asset1", "asset2", "asset3"]:
            self.db.upsert_icloud_metadata(
                ICloudAssetRecord(asset_id=aid, filename=f"{aid}.jpg")
            )
        count = self.db.replace_album_assets("a1", ["asset1", "asset2", "asset3"])
        assert count == 3
        assert self.db.get_album_assets("a1") == ["asset1", "asset2", "asset3"]

    def test_replace_album_assets_replaces_previous(self) -> None:
        self.db.upsert_album(
            AlbumRecord(album_id="a1", album_name="Test", album_type="user")
        )
        for aid in ["asset1", "asset2", "asset3"]:
            self.db.upsert_icloud_metadata(
                ICloudAssetRecord(asset_id=aid, filename=f"{aid}.jpg")
            )
        self.db.replace_album_assets("a1", ["asset1", "asset2"])
        self.db.replace_album_assets("a1", ["asset2", "asset3"])
        assert self.db.get_album_assets("a1") == ["asset2", "asset3"]

    def test_get_asset_albums(self) -> None:
        for album_id in ["a1", "a2"]:
            self.db.upsert_album(
                AlbumRecord(album_id=album_id, album_name=album_id, album_type="user")
            )
        self.db.upsert_icloud_metadata(
            ICloudAssetRecord(asset_id="shared", filename="shared.jpg")
        )
        self.db.replace_album_assets("a1", ["shared"])
        self.db.replace_album_assets("a2", ["shared"])
        assert set(self.db.get_asset_albums("shared")) == {"a1", "a2"}

    def test_mark_album_deleted_cleans_membership(self) -> None:
        self.db.upsert_album(
            AlbumRecord(album_id="a1", album_name="Gone", album_type="user")
        )
        self.db.upsert_icloud_metadata(
            ICloudAssetRecord(asset_id="x", filename="x.jpg")
        )
        self.db.replace_album_assets("a1", ["x"])
        self.db.mark_album_deleted("a1", "2024-01-01T00:00:00Z")
        assert self.db.get_all_album_ids() == []
        assert self.db.get_album_assets("a1") == []

    def test_mark_asset_deleted_cascades_to_album_assets(self) -> None:
        self.db.upsert_album(
            AlbumRecord(album_id="a1", album_name="Test", album_type="user")
        )
        self.db.upsert_icloud_metadata(
            ICloudAssetRecord(asset_id="x", filename="x.jpg")
        )
        self.db.replace_album_assets("a1", ["x"])
        self.db.mark_asset_deleted("x", "2024-01-01T00:00:00Z")
        assert self.db.get_album_assets("a1") == []

    def test_album_count(self) -> None:
        self.db.upsert_album(
            AlbumRecord(album_id="a1", album_name="One", album_type="user")
        )
        self.db.upsert_album(
            AlbumRecord(album_id="a2", album_name="Two", album_type="smart")
        )
        assert self.db.get_album_count() == 2
        self.db.mark_album_deleted("a1", "2024-01-01T00:00:00Z")
        assert self.db.get_album_count() == 1


class TestDeriveAlbumId:
    """Test album ID derivation logic."""

    def test_user_album(self) -> None:
        album = Mock()
        album.record_name = "abc-123"
        assert SyncManager._derive_album_id("MyAlbum", album) == "user:abc-123"

    def test_smart_album(self) -> None:
        album = Mock()
        album.record_name = None
        assert SyncManager._derive_album_id("Favorites", album) == "smart:Favorites"

    def test_smart_album_with_spaces(self) -> None:
        album = Mock()
        album.record_name = None
        assert SyncManager._derive_album_id("Recently Deleted", album) == "smart:RecentlyDeleted"
