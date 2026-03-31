"""Tests for the new download architecture."""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from icloudpd.new_download.database import (
    ICloudAssetRecord,
    LocalFileRecord,
    PhotoDatabase,
    SyncStatus,
    UpsertResult,
)
from icloudpd.new_download.file_manager import FileManager
from icloudpd.new_download.photo_asset_record_mapper import PhotoAssetRecordMapper
from icloudpd.new_download.sync_manager import SyncManager
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
        self.sync_manager = SyncManager(Path(self.temp_dir))

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
