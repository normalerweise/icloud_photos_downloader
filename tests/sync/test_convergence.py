"""Integration tests: multi-sync convergence across album/folder restructuring and asset lifecycle."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pyicloud_ipd.asset_version import AssetVersion
from pyicloud_ipd.version_size import AssetVersionSize

from .conftest import (
    MockPhotosToSync,
    SyncEnv,
    collect_symlinks,
    make_album,
    make_asset,
    make_downloading_manager,
    make_folder,
    make_photo_library,
    seed_downloaded_asset,
)


# -- Album / Folder Restructuring ---------------------------------------------


class TestAlbumRestructuring:

    def test_album_renamed(self, sync_env: SyncEnv) -> None:
        """After album rename, old symlinks gone, new album dir with same assets."""
        asset = make_asset("a1", "IMG_001.JPG")
        album = make_album("Vacation", record_name="album1", assets=[asset])
        lib = make_photo_library(albums={"Vacation": album}, all_assets=[asset])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        album_links = collect_symlinks(sync_env.temp_dir, "Albums")
        assert any("Vacation" in k for k in album_links)

        # Rename album
        album_v2 = make_album("2024 Vacation", record_name="album1", assets=[asset])
        lib_v2 = make_photo_library(albums={"2024 Vacation": album_v2}, all_assets=[asset])

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        album_links_v2 = collect_symlinks(sync_env.temp_dir, "Albums")
        assert not any("Vacation" in k and "2024" not in k for k in album_links_v2)
        assert any("2024 Vacation" in k for k in album_links_v2)

    def test_album_moved_to_different_folder(self, sync_env: SyncEnv) -> None:
        """Album moves from FolderA to FolderB: old path gone, new path created."""
        asset = make_asset("a1", "IMG_001.JPG")
        folder_a = make_folder("fA", "Travel")
        album = make_album("Beach", record_name="album1", parent_folder_id="fA", assets=[asset])
        lib = make_photo_library(
            folders=[folder_a],
            albums={"Beach": album},
            all_assets=[asset],
        )

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        album_links = collect_symlinks(sync_env.temp_dir, "Albums")
        assert any("Travel" in k for k in album_links)

        # Move album to new folder
        folder_b = make_folder("fB", "Trips")
        album_v2 = make_album("Beach", record_name="album1", parent_folder_id="fB", assets=[asset])
        lib_v2 = make_photo_library(
            folders=[folder_b],
            albums={"Beach": album_v2},
            all_assets=[asset],
        )

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        album_links_v2 = collect_symlinks(sync_env.temp_dir, "Albums")
        assert not any("Travel" in k for k in album_links_v2)
        assert any("Trips" in k for k in album_links_v2)

    def test_folder_renamed(self, sync_env: SyncEnv) -> None:
        """Folder rename updates all child album paths."""
        asset = make_asset("a1", "IMG_001.JPG")
        folder = make_folder("f1", "Travel")
        album = make_album("Beach", record_name="album1", parent_folder_id="f1", assets=[asset])
        lib = make_photo_library(folders=[folder], albums={"Beach": album}, all_assets=[asset])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        # Rename folder
        folder_v2 = make_folder("f1", "Trips")
        lib_v2 = make_photo_library(folders=[folder_v2], albums={"Beach": album}, all_assets=[asset])

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        album_links = collect_symlinks(sync_env.temp_dir, "Albums")
        assert any("Trips" in k for k in album_links)
        assert not any("Travel" in k for k in album_links)

    def test_album_deleted_assets_remain(self, sync_env: SyncEnv) -> None:
        """Album deletion removes album symlinks, but Library/ symlinks stay."""
        asset = make_asset("a1", "IMG_001.JPG")
        album = make_album("Vacation", record_name="album1", assets=[asset])
        lib = make_photo_library(albums={"Vacation": album}, all_assets=[asset])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        assert collect_symlinks(sync_env.temp_dir, "Albums")
        assert collect_symlinks(sync_env.temp_dir, "Library")

        # Delete album (asset still in iCloud)
        lib_v2 = make_photo_library(albums={}, all_assets=[asset])

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        assert not collect_symlinks(sync_env.temp_dir, "Albums")
        assert collect_symlinks(sync_env.temp_dir, "Library")

    def test_folder_deleted_with_child_albums(self, sync_env: SyncEnv) -> None:
        """Deleting folder tombstones folder and its albums."""
        asset = make_asset("a1", "IMG_001.JPG")
        folder = make_folder("f1", "Travel")
        album = make_album("Beach", record_name="album1", parent_folder_id="f1", assets=[asset])
        lib = make_photo_library(folders=[folder], albums={"Beach": album}, all_assets=[asset])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        # Delete folder and album
        lib_v2 = make_photo_library(folders=[], albums={}, all_assets=[asset])

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        assert not collect_symlinks(sync_env.temp_dir, "Albums")
        assert collect_symlinks(sync_env.temp_dir, "Library")

        # DB: folder and album tombstoned
        assert sync_env.database.get_all_folder_ids() == []
        assert sync_env.database.get_all_album_ids() == []

    def test_empty_album_created_then_deleted(self, sync_env: SyncEnv) -> None:
        """Empty album → no leftover directories after deletion."""
        asset = make_asset("a1", "IMG_001.JPG")
        album = make_album("Empty", record_name="album1", assets=[])
        lib = make_photo_library(albums={"Empty": album}, all_assets=[asset])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        # Delete empty album
        lib_v2 = make_photo_library(albums={}, all_assets=[asset])

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        albums_dir = sync_env.temp_dir / "Albums"
        if albums_dir.exists():
            assert not any(albums_dir.rglob("*"))

    def test_asset_moved_between_albums(self, sync_env: SyncEnv) -> None:
        """Asset moves from Album A to Album B: symlinks reflect new membership."""
        asset = make_asset("a1", "IMG_001.JPG")
        album_a = make_album("Album A", record_name="albumA", assets=[asset])
        album_b = make_album("Album B", record_name="albumB", assets=[])
        lib = make_photo_library(
            albums={"Album A": album_a, "Album B": album_b},
            all_assets=[asset],
        )

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        links = collect_symlinks(sync_env.temp_dir, "Albums")
        assert any("Album A" in k for k in links)
        assert not any("Album B" in k for k in links)

        # Move asset to Album B
        album_a_v2 = make_album("Album A", record_name="albumA", assets=[])
        album_b_v2 = make_album("Album B", record_name="albumB", assets=[asset])
        lib_v2 = make_photo_library(
            albums={"Album A": album_a_v2, "Album B": album_b_v2},
            all_assets=[asset],
        )

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        links_v2 = collect_symlinks(sync_env.temp_dir, "Albums")
        assert not any("Album A" in k for k in links_v2)
        assert any("Album B" in k for k in links_v2)


# -- Asset Lifecycle -----------------------------------------------------------


class TestAssetLifecycle:

    def test_asset_deleted_from_icloud(self, sync_env: SyncEnv) -> None:
        """Deleted asset: tombstoned in DB, symlinks removed."""
        a1 = make_asset("a1", "IMG_001.JPG")
        a2 = make_asset("a2", "IMG_002.JPG", created=datetime(2024, 2, 10, 8, 0, 0))
        lib = make_photo_library(all_assets=[a1, a2])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([a1, a2]))

        library_links = collect_symlinks(sync_env.temp_dir, "Library")
        assert len(library_links) == 2

        # Delete a1 from iCloud
        lib_v2 = make_photo_library(all_assets=[a2])
        sync_env.clock.advance("2024-03-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([a2]))

        library_links_v2 = collect_symlinks(sync_env.temp_dir, "Library")
        assert len(library_links_v2) == 1
        assert not any("IMG_001" in k for k in library_links_v2)

        # DB: asset tombstoned
        meta = sync_env.database.get_icloud_metadata("a1")
        assert meta is not None
        assert meta.deleted

    def test_asset_deleted_then_readded(self, sync_env: SyncEnv) -> None:
        """Asset deleted then re-added: resurrected in DB, symlinks recreated."""
        asset = make_asset("a1", "IMG_001.JPG")
        lib = make_photo_library(all_assets=[asset])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        assert collect_symlinks(sync_env.temp_dir, "Library")

        # Delete
        lib_v2 = make_photo_library(all_assets=[])
        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([]))

        assert not collect_symlinks(sync_env.temp_dir, "Library")

        # Re-add
        lib_v3 = make_photo_library(all_assets=[asset])
        sync_env.clock.advance("2024-03-01T00:00:00")
        sm3 = sync_env.make_sync_manager(photo_library=lib_v3, download_manager=dm)
        sm3.sync_photos(MockPhotosToSync([asset]))

        assert collect_symlinks(sync_env.temp_dir, "Library")
        meta = sync_env.database.get_icloud_metadata("a1")
        assert meta is not None
        assert not meta.deleted

    def test_asset_filename_changed(self, sync_env: SyncEnv) -> None:
        """Asset filename change: DB updated, symlinks reflect new name."""
        asset_v1 = make_asset("a1", "IMG_001.JPG")
        lib = make_photo_library(all_assets=[asset_v1])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset_v1]))

        links_v1 = collect_symlinks(sync_env.temp_dir, "Library")
        assert any("IMG_001" in k for k in links_v1)

        # Filename changed on iCloud (e.g., user edited title)
        asset_v2 = make_asset("a1", "Sunset_Beach.JPG")
        lib_v2 = make_photo_library(all_assets=[asset_v2])

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset_v2]))

        links_v2 = collect_symlinks(sync_env.temp_dir, "Library")
        assert any("Sunset_Beach" in k for k in links_v2)
        assert not any("IMG_001" in k for k in links_v2)

    def test_asset_date_changed(self, sync_env: SyncEnv) -> None:
        """Asset date change: asset moves to different year/month in Library/."""
        asset_v1 = make_asset("a1", "IMG_001.JPG", created=datetime(2024, 1, 15, 10, 0, 0))
        lib = make_photo_library(all_assets=[asset_v1])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset_v1]))

        links_v1 = collect_symlinks(sync_env.temp_dir, "Library")
        assert any("2024/01" in k for k in links_v1)

        # Date corrected on iCloud
        asset_v2 = make_asset("a1", "IMG_001.JPG", created=datetime(2023, 7, 4, 12, 0, 0))
        lib_v2 = make_photo_library(all_assets=[asset_v2])

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset_v2]))

        links_v2 = collect_symlinks(sync_env.temp_dir, "Library")
        assert any("2023/07" in k for k in links_v2)
        assert not any("2024/01" in k for k in links_v2)


# -- Convergence Properties ----------------------------------------------------


class TestConvergence:

    def test_idempotent_sync(self, sync_env: SyncEnv) -> None:
        """Two identical syncs: second reports 0 filesystem changes."""
        asset = make_asset("a1", "IMG_001.JPG")
        album = make_album("Vacation", record_name="album1", assets=[asset])
        lib = make_photo_library(albums={"Vacation": album}, all_assets=[asset])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        links_after_first = collect_symlinks(sync_env.temp_dir, "Library")
        album_links_first = collect_symlinks(sync_env.temp_dir, "Albums")

        # Second sync with same state
        sm2 = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        links_after_second = collect_symlinks(sync_env.temp_dir, "Library")
        album_links_second = collect_symlinks(sync_env.temp_dir, "Albums")

        assert links_after_first == links_after_second
        assert album_links_first == album_links_second

    def test_three_syncs_converge(self, sync_env: SyncEnv) -> None:
        """Initial sync → mutate → second sync → third sync (no changes)."""
        a1 = make_asset("a1", "IMG_001.JPG")
        a2 = make_asset("a2", "IMG_002.JPG", created=datetime(2024, 3, 1, 9, 0, 0))
        album = make_album("Trip", record_name="album1", assets=[a1, a2])
        lib = make_photo_library(albums={"Trip": album}, all_assets=[a1, a2])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([a1, a2]))

        # Mutate: delete a1, rename album
        album_v2 = make_album("Holiday", record_name="album1", assets=[a2])
        lib_v2 = make_photo_library(albums={"Holiday": album_v2}, all_assets=[a2])

        sync_env.clock.advance("2024-04-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([a2]))

        links_after_second = collect_symlinks(sync_env.temp_dir, "Library")
        album_links_second = collect_symlinks(sync_env.temp_dir, "Albums")

        # Third sync — should be no-op
        sync_env.clock.advance("2024-05-01T00:00:00")
        sm3 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm3.sync_photos(MockPhotosToSync([a2]))

        assert collect_symlinks(sync_env.temp_dir, "Library") == links_after_second
        assert collect_symlinks(sync_env.temp_dir, "Albums") == album_links_second

    def test_partial_then_full_sync(self, sync_env: SyncEnv) -> None:
        """Partial strategy: no deletions. Full strategy: deletions detected."""
        a1 = make_asset("a1", "IMG_001.JPG")
        a2 = make_asset("a2", "IMG_002.JPG", created=datetime(2024, 3, 1, 9, 0, 0))

        dm = make_downloading_manager(sync_env)

        # Full sync with both assets
        lib = make_photo_library(all_assets=[a1, a2])
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([a1, a2], covers_full=True))

        assert len(collect_symlinks(sync_env.temp_dir, "Library")) == 2

        # Partial sync with only a2 (a1 removed from iCloud, but partial strategy)
        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([a2], covers_full=False))

        # a1 should still be visible (no deletion detection in partial mode)
        assert len(collect_symlinks(sync_env.temp_dir, "Library")) == 2

        # Full sync with only a2 — now deletions detected
        lib_v2 = make_photo_library(all_assets=[a2])
        sync_env.clock.advance("2024-03-01T00:00:00")
        sm3 = sync_env.make_sync_manager(photo_library=lib_v2, download_manager=dm)
        sm3.sync_photos(MockPhotosToSync([a2], covers_full=True))

        links = collect_symlinks(sync_env.temp_dir, "Library")
        assert len(links) == 1
        assert not any("IMG_001" in k for k in links)
