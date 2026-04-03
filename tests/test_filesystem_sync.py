"""Tests for Phase 5: Filesystem sync (symlink layer)."""

import os
import shutil
import tempfile
from pathlib import Path

from icloudpd.new_download.database import (
    AlbumRecord,
    FolderRecord,
    ICloudAssetRecord,
    LocalFileRecord,
    PhotoDatabase,
)
from icloudpd.new_download.filesystem_sync import FilesystemSync, _sanitize_filename, _short_id


class TestFilesystemSync:
    """Test the FilesystemSync delta convergence."""

    def setup_method(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.base = Path(self.tmp_dir)
        self.db = PhotoDatabase(self.base)
        self.fs_sync = FilesystemSync(self.base, self.db)

        # Create _data directory with some fake files
        data_dir = self.base / "_data"
        data_dir.mkdir(exist_ok=True)

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp_dir)

    def _seed_asset(
        self,
        asset_id: str,
        filename: str,
        created_date: str = "2024-01-15T14:30:22",
        added_date: str | None = None,
        versions: list[str] | None = None,
    ) -> None:
        """Seed an asset in DB with metadata and local file records."""
        self.db.upsert_icloud_metadata(
            ICloudAssetRecord(
                asset_id=asset_id,
                filename=filename,
                created_date=created_date,
                added_date=added_date,
            )
        )
        for version in versions or ["original"]:
            ext = os.path.splitext(filename)[1].lower()
            if version == "live_photo":
                ext = ".mov"
            data_filename = f"{asset_id}-{version}{ext}"
            data_path = f"_data/{data_filename}"
            (self.base / data_path).write_bytes(b"fake data")
            self.db.upsert_local_file(
                LocalFileRecord(
                    asset_id=asset_id,
                    version_type=version,
                    local_filename=data_filename,
                    file_path=data_path,
                    file_size=9,
                    download_date="2024-01-16T00:00:00",
                )
            )

    # -- Library tree tests -----------------------------------------------------

    def test_library_basic(self) -> None:
        """Assets produce Library/YYYY/MM/ symlinks with datetime prefix."""
        self._seed_asset("a1", "IMG_001.JPG", created_date="2024-01-15T14:30:22")
        self._seed_asset("a2", "IMG_002.JPG", created_date="2024-03-20T09:15:00")

        stats = self.fs_sync.sync_filesystem()

        assert stats["created"] == 2
        jan = self.base / "Library" / "2024" / "01" / "20240115_143022_IMG_001.JPG"
        mar = self.base / "Library" / "2024" / "03" / "20240320_091500_IMG_002.JPG"
        assert jan.is_symlink()
        assert mar.is_symlink()
        assert jan.resolve().exists()
        assert mar.resolve().exists()

    def test_library_fallback_to_added_date(self) -> None:
        """Missing created_date falls back to added_date."""
        self._seed_asset("a1", "IMG.JPG", created_date=None, added_date="2023-06-01T12:00:00")

        self.fs_sync.sync_filesystem()

        link = self.base / "Library" / "2023" / "06" / "20230601_120000_IMG.JPG"
        assert link.is_symlink()

    def test_library_fallback_to_unknown(self) -> None:
        """Missing both dates falls back to Unknown/00/."""
        self._seed_asset("a1", "IMG.JPG", created_date=None, added_date=None)

        self.fs_sync.sync_filesystem()

        link = self.base / "Library" / "Unknown" / "00" / "00000000_000000_IMG.JPG"
        assert link.is_symlink()

    def test_library_collision_disambiguation(self) -> None:
        """Two assets with same filename in same month get disambiguated."""
        self._seed_asset("a1", "IMG.JPG", created_date="2024-01-15T10:00:00")
        self._seed_asset("a2", "IMG.JPG", created_date="2024-01-15T10:00:00")

        self.fs_sync.sync_filesystem()

        dir_path = self.base / "Library" / "2024" / "01"
        links = sorted(p.name for p in dir_path.iterdir() if p.is_symlink())
        assert len(links) == 2
        # First gets clean name, second gets suffix
        assert links[0] == "20240115_100000_IMG.JPG"
        suffix = _short_id("a2")
        assert links[1] == f"20240115_100000_IMG_{suffix}.JPG"

    def test_library_multi_version(self) -> None:
        """Multiple versions produce multiple symlinks."""
        self._seed_asset("a1", "IMG.JPG", created_date="2024-01-15T10:00:00", versions=["original", "adjusted"])

        self.fs_sync.sync_filesystem()

        dir_path = self.base / "Library" / "2024" / "01"
        links = sorted(p.name for p in dir_path.iterdir() if p.is_symlink())
        assert "20240115_100000_IMG.JPG" in links
        assert "20240115_100000_IMG-adjusted.JPG" in links

    def test_library_live_photo(self) -> None:
        """Live photo version gets .mov extension."""
        self._seed_asset("a1", "IMG.JPG", created_date="2024-01-15T10:00:00", versions=["original", "live_photo"])

        self.fs_sync.sync_filesystem()

        dir_path = self.base / "Library" / "2024" / "01"
        links = sorted(p.name for p in dir_path.iterdir() if p.is_symlink())
        assert "20240115_100000_IMG.JPG" in links
        assert "20240115_100000_IMG.mov" in links

    # -- Albums tree tests ------------------------------------------------------

    def test_albums_basic(self) -> None:
        """User album produces Albums/AlbumName/ with symlinks."""
        self._seed_asset("a1", "IMG.JPG")

        self.db.upsert_album(
            AlbumRecord(album_id="user:abc", album_name="Vacation", album_type="user")
        )
        self.db.replace_album_assets("user:abc", ["a1"])

        self.fs_sync.sync_filesystem()

        link = self.base / "Albums" / "Vacation" / "20240115_143022_IMG.JPG"
        assert link.is_symlink()
        assert link.resolve().exists()

    def test_albums_nested_in_folder(self) -> None:
        """Album inside a folder produces Albums/Folder/AlbumName/."""
        self._seed_asset("a1", "IMG.JPG")

        self.db.upsert_folder(
            FolderRecord(folder_id="f1", folder_name="Travel")
        )
        self.db.upsert_album(
            AlbumRecord(
                album_id="user:abc", album_name="Japan", album_type="user", folder_id="f1"
            )
        )
        self.db.replace_album_assets("user:abc", ["a1"])

        self.fs_sync.sync_filesystem()

        link = self.base / "Albums" / "Travel" / "Japan" / "20240115_143022_IMG.JPG"
        assert link.is_symlink()

    def test_albums_deeply_nested(self) -> None:
        """Folder inside folder produces correct path."""
        self._seed_asset("a1", "IMG.JPG")

        self.db.upsert_folder(
            FolderRecord(folder_id="f1", folder_name="Outer")
        )
        self.db.upsert_folder(
            FolderRecord(folder_id="f2", folder_name="Inner", parent_folder_id="f1")
        )
        self.db.upsert_album(
            AlbumRecord(
                album_id="user:abc", album_name="Photos", album_type="user", folder_id="f2"
            )
        )
        self.db.replace_album_assets("user:abc", ["a1"])

        self.fs_sync.sync_filesystem()

        link = self.base / "Albums" / "Outer" / "Inner" / "Photos" / "20240115_143022_IMG.JPG"
        assert link.is_symlink()

    def test_smart_albums_skipped(self) -> None:
        """Smart albums produce no Albums/ subdirectory."""
        self._seed_asset("a1", "IMG.JPG")

        self.db.upsert_album(
            AlbumRecord(album_id="smart:Favorites", album_name="Favorites", album_type="smart")
        )

        self.fs_sync.sync_filesystem()

        assert not (self.base / "Albums" / "Favorites").exists()

    # -- Delta convergence tests ------------------------------------------------

    def test_delta_creates_missing(self) -> None:
        """Second run after adding an asset creates only the new symlink."""
        self._seed_asset("a1", "IMG_001.JPG", created_date="2024-01-15T10:00:00")
        self.fs_sync.sync_filesystem()

        self._seed_asset("a2", "IMG_002.JPG", created_date="2024-01-16T10:00:00")
        stats = self.fs_sync.sync_filesystem()

        assert stats["created"] == 1
        assert stats["unchanged"] == 1
        assert stats["removed"] == 0

    def test_delta_removes_stale(self) -> None:
        """Tombstoned asset's symlinks are removed on next sync."""
        self._seed_asset("a1", "IMG.JPG")
        self.fs_sync.sync_filesystem()

        # Tombstone the asset
        self.db.mark_asset_deleted("a1", "2024-02-01T00:00:00")
        stats = self.fs_sync.sync_filesystem()

        assert stats["removed"] == 1
        assert stats["created"] == 0
        assert not list((self.base / "Library").rglob("*IMG*"))

    def test_delta_idempotent(self) -> None:
        """Running sync twice produces same result."""
        self._seed_asset("a1", "IMG.JPG")
        self.fs_sync.sync_filesystem()
        stats = self.fs_sync.sync_filesystem()

        assert stats["created"] == 0
        assert stats["removed"] == 0
        assert stats["updated"] == 0
        assert stats["unchanged"] == 1

    def test_prune_empty_dirs(self) -> None:
        """Empty directories are cleaned up after stale symlink removal."""
        self._seed_asset("a1", "IMG.JPG", created_date="2024-06-15T10:00:00")
        self.fs_sync.sync_filesystem()

        assert (self.base / "Library" / "2024" / "06").exists()

        self.db.mark_asset_deleted("a1", "2024-07-01T00:00:00")
        self.fs_sync.sync_filesystem()

        # Empty year/month dirs should be pruned
        assert not (self.base / "Library" / "2024" / "06").exists()
        assert not (self.base / "Library" / "2024").exists()

    # -- Relative symlinks test -------------------------------------------------

    def test_symlinks_are_relative(self) -> None:
        """All created symlinks use relative paths."""
        self._seed_asset("a1", "IMG.JPG")
        self.fs_sync.sync_filesystem()

        for link in (self.base / "Library").rglob("*"):
            if link.is_symlink():
                target = os.readlink(link)
                assert not os.path.isabs(target), f"Symlink {link} has absolute target {target}"

    # -- Filename sanitization test ---------------------------------------------

    def test_album_name_sanitized(self) -> None:
        """Album names with unsafe characters are sanitized."""
        self._seed_asset("a1", "IMG.JPG")

        self.db.upsert_album(
            AlbumRecord(
                album_id="user:abc", album_name='My/Bad:Album*Name', album_type="user"
            )
        )
        self.db.replace_album_assets("user:abc", ["a1"])

        self.fs_sync.sync_filesystem()

        sanitized_dir = self.base / "Albums" / "My_Bad_Album_Name"
        assert sanitized_dir.exists()


class TestSanitizeFilename:
    def test_replaces_unsafe(self) -> None:
        assert _sanitize_filename('a/b\\c:d*e?"f<g>h|i') == "a_b_c_d_e__f_g_h_i"

    def test_strips_dots_and_spaces(self) -> None:
        assert _sanitize_filename("...test...") == "test"
        assert _sanitize_filename("  test  ") == "test"

    def test_empty_becomes_underscore(self) -> None:
        assert _sanitize_filename("...") == "_"
