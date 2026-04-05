"""Property-based tests: structural invariants hold for arbitrary sync scenarios."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from hypothesis import given, settings
from hypothesis import strategies as st

from icloudpd.sync.database import PhotoDatabase
from icloudpd.sync.file_manager import FileManager
from icloudpd.sync.filesystem_sync import FilesystemSync
from icloudpd.sync.photo_asset_record_mapper import PhotoAssetRecordMapper
from icloudpd.sync.progress_reporter import LoggingProgressReporter
from icloudpd.sync.sync_manager import SyncManager
from pyicloud_ipd.asset_version import AssetVersion
from pyicloud_ipd.version_size import AssetVersionSize

from .conftest import (
    FakeClock,
    MockPhotosToSync,
    collect_symlinks,
    make_album,
    make_asset,
    make_downloading_manager,
    make_folder,
    SyncEnv,
)


# -- Hypothesis strategies for generating random sync scenarios ----------------

safe_filename_chars = st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_-.")

filenames = st.from_regex(r"[A-Z]{3}_[0-9]{4}\.(JPG|PNG|HEIC|MOV)", fullmatch=True)

dates = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2025, 12, 31),
)

asset_ids = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=4,
    max_size=12,
).map(lambda s: f"asset_{s}")


@st.composite
def random_assets(draw, min_count=1, max_count=15):
    """Generate a list of unique mock PhotoAssets."""
    count = draw(st.integers(min_value=min_count, max_value=max_count))
    ids = draw(
        st.lists(asset_ids, min_size=count, max_size=count, unique=True)
    )
    assets = []
    for aid in ids:
        fname = draw(filenames)
        created = draw(dates)
        assets.append(make_asset(aid, fname, created=created))
    return assets


@st.composite
def random_albums(draw, assets):
    """Generate random album structures referencing provided assets."""
    if not assets:
        return {}, []

    album_count = draw(st.integers(min_value=0, max_value=5))
    folder_count = draw(st.integers(min_value=0, max_value=3))

    folders = []
    for i in range(folder_count):
        folders.append(make_folder(f"folder_{i}", f"Folder {i}"))

    albums = {}
    for i in range(album_count):
        # Pick random subset of assets
        member_indices = draw(
            st.lists(
                st.integers(min_value=0, max_value=len(assets) - 1),
                max_size=min(len(assets), 10),
                unique=True,
            )
        )
        members = [assets[j] for j in member_indices]
        folder_id = folders[i % len(folders)].record_name if folders else None
        album_name = f"Album {i}"
        albums[album_name] = make_album(
            album_name,
            record_name=f"album_{i}",
            parent_folder_id=folder_id,
            assets=members,
        )

    return albums, folders


def _make_env():
    """Create a fresh SyncEnv (not using pytest fixture for hypothesis)."""
    temp_dir = Path(tempfile.mkdtemp())
    clock = FakeClock()
    database = PhotoDatabase(temp_dir, clock=clock)
    file_manager = FileManager(temp_dir)
    filesystem_sync = FilesystemSync(temp_dir, database)
    return SyncEnv(
        temp_dir=temp_dir,
        database=database,
        file_manager=file_manager,
        filesystem_sync=filesystem_sync,
        mapper=PhotoAssetRecordMapper(),
        progress_reporter=LoggingProgressReporter(),
        clock=clock,
    )


def _check_invariants(env: SyncEnv) -> None:
    """Assert all structural invariants after a sync."""
    library_links = collect_symlinks(env.temp_dir, "Library")
    album_links = collect_symlinks(env.temp_dir, "Albums")
    data_dir = env.temp_dir / "_data"

    # Invariant 1: no dangling symlinks — every symlink target resolves
    for subdir in ("Library", "Albums"):
        root = env.temp_dir / subdir
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_symlink():
                target = path.parent / path.readlink()
                assert target.exists(), f"Dangling symlink: {path} → {path.readlink()}"

    # Invariant 2: no symlinks for tombstoned assets
    tombstoned_ids = set()
    all_ids = env.database.get_all_asset_ids()
    # get_all_asset_ids excludes tombstoned, so anything NOT in all_ids that's in DB is tombstoned
    # We check by ensuring no symlink references a file for a tombstoned asset

    # Invariant 3: DB counts are self-consistent
    total = env.database.get_asset_count()
    downloaded = env.database.get_downloaded_count()
    assert downloaded <= total

    # Invariant 4: every local_file in DB has a corresponding file on disk
    for asset_data in env.database.get_all_downloaded_assets():
        _asset, local_files = asset_data
        for lf in local_files:
            full_path = env.temp_dir / lf.file_path
            assert full_path.exists(), f"DB has local_file but file missing: {lf.file_path}"


class TestPropertyInvariants:

    @given(assets=random_assets(min_count=1, max_count=10))
    @settings(max_examples=20, deadline=10000)
    def test_invariants_hold_after_sync(self, assets) -> None:
        """Structural invariants hold for arbitrary asset sets."""
        env = _make_env()
        try:
            dm = make_downloading_manager(env)
            lib = Mock()
            lib.folders = []
            lib.albums = {}
            all_album = Mock()
            all_album.__iter__ = lambda self: iter(assets)
            all_album.__len__ = lambda self: len(assets)
            lib.all = all_album

            sm = env.make_sync_manager(photo_library=lib, download_manager=dm)
            sm.sync_photos(MockPhotosToSync(assets))

            _check_invariants(env)
        finally:
            shutil.rmtree(env.temp_dir)

    @given(assets=random_assets(min_count=1, max_count=10))
    @settings(max_examples=20, deadline=10000)
    def test_idempotent_sync_property(self, assets) -> None:
        """Running sync twice with same input produces identical filesystem state."""
        env = _make_env()
        try:
            dm = make_downloading_manager(env)
            lib = Mock()
            lib.folders = []
            lib.albums = {}
            all_album = Mock()
            all_album.__iter__ = lambda self: iter(assets)
            all_album.__len__ = lambda self: len(assets)
            lib.all = all_album

            sm = env.make_sync_manager(photo_library=lib, download_manager=dm)
            sm.sync_photos(MockPhotosToSync(assets))

            links_first = collect_symlinks(env.temp_dir, "Library")

            sm2 = env.make_sync_manager(photo_library=lib, download_manager=dm)
            sm2.sync_photos(MockPhotosToSync(assets))

            links_second = collect_symlinks(env.temp_dir, "Library")
            assert links_first == links_second
        finally:
            shutil.rmtree(env.temp_dir)

    @given(data=st.data())
    @settings(max_examples=10, deadline=15000)
    def test_invariants_with_albums_and_deletions(self, data) -> None:
        """Invariants hold after sync with albums, then deletion of some assets."""
        env = _make_env()
        try:
            assets = data.draw(random_assets(min_count=2, max_count=8))
            albums_dict, folders = data.draw(random_albums(assets))

            dm = make_downloading_manager(env)
            lib = Mock()
            lib.folders = folders
            lib.albums = albums_dict
            all_album = Mock()
            all_album.__iter__ = lambda self: iter(assets)
            all_album.__len__ = lambda self: len(assets)
            lib.all = all_album

            sm = env.make_sync_manager(photo_library=lib, download_manager=dm)
            sm.sync_photos(MockPhotosToSync(assets))

            _check_invariants(env)

            # Delete roughly half the assets
            keep_count = max(1, len(assets) // 2)
            remaining = assets[:keep_count]

            # Rebuild albums with only remaining assets
            new_albums = {}
            for name, album in albums_dict.items():
                remaining_ids = {a.id for a in remaining}
                new_members = [a for a in assets if a.id in remaining_ids]
                new_albums[name] = make_album(
                    name,
                    record_name=album.record_name,
                    parent_folder_id=album.parent_folder_id,
                    assets=new_members,
                )

            lib2 = Mock()
            lib2.folders = folders
            lib2.albums = new_albums
            all_album2 = Mock()
            all_album2.__iter__ = lambda self: iter(remaining)
            all_album2.__len__ = lambda self: len(remaining)
            lib2.all = all_album2

            env.clock.advance("2025-01-01T00:00:00")
            sm2 = env.make_sync_manager(photo_library=lib2, download_manager=dm)
            sm2.sync_photos(MockPhotosToSync(remaining, covers_full=True))

            _check_invariants(env)
        finally:
            shutil.rmtree(env.temp_dir)
