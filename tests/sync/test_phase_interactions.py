"""Tests for cross-phase consistency."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock, patch

from icloudpd.sync.database import SyncState

from .conftest import (
    MockPhotosToSync,
    SyncEnv,
    collect_symlinks,
    make_album,
    make_asset,
    make_downloading_manager,
    make_photo_library,
    seed_downloaded_asset,
)


class TestPhaseInteractions:

    def test_phase1_failure_does_not_corrupt_existing_data(self, sync_env: SyncEnv) -> None:
        """If Phase 1 fails on one asset, previously synced assets remain intact."""
        a1 = make_asset("a1", "IMG_001.JPG")
        lib = make_photo_library(all_assets=[a1])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([a1]))

        meta = sync_env.database.get_icloud_metadata("a1")
        assert meta is not None

        # Second sync: a2 causes mapper exception, a1 still there
        a2_bad = make_asset("a2", "IMG_002.JPG")
        a2_bad.versions = None  # Will cause exception in mapper

        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([a1, a2_bad]))

        # a1 is still intact
        meta_after = sync_env.database.get_icloud_metadata("a1")
        assert meta_after is not None
        assert not meta_after.deleted

    def test_phase3_skipped_for_partial_strategy(self, sync_env: SyncEnv) -> None:
        """RecentPhotosStrategy (covers_full_library=False) skips deletion detection."""
        a1 = make_asset("a1", "IMG_001.JPG")
        a2 = make_asset("a2", "IMG_002.JPG", created=datetime(2024, 3, 1, 9, 0, 0))

        dm = make_downloading_manager(sync_env)

        # Full sync with both
        lib = make_photo_library(all_assets=[a1, a2])
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([a1, a2], covers_full=True))

        assert sync_env.database.get_asset_count() == 2

        # Partial sync with only a2 — should NOT delete a1
        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([a2], covers_full=False))

        assert sync_env.database.get_asset_count() == 2  # a1 still counted

    def test_phase3_with_empty_asset_map_deletes_all(self, sync_env: SyncEnv) -> None:
        """Full sync with empty asset map tombstones ALL existing assets."""
        a1 = make_asset("a1", "IMG_001.JPG")

        dm = make_downloading_manager(sync_env)
        lib = make_photo_library(all_assets=[a1])
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([a1], covers_full=True))

        assert sync_env.database.get_asset_count() == 1

        # Full sync with empty — all deleted
        lib_empty = make_photo_library(all_assets=[])
        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib_empty, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([], covers_full=True))

        assert sync_env.database.get_deleted_asset_count() == 1

    def test_phase4_skips_already_completed(self, sync_env: SyncEnv) -> None:
        """Re-sync does not re-download already completed assets."""
        asset = make_asset("a1", "IMG_001.JPG")
        lib = make_photo_library(all_assets=[asset])

        dm = make_downloading_manager(sync_env)
        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        first_call_count = dm.download_asset_versions.call_count

        # Second sync — should not trigger additional downloads
        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib, download_manager=dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        assert dm.download_asset_versions.call_count == first_call_count

    def test_phase4_retries_failed_on_next_sync(self, sync_env: SyncEnv) -> None:
        """FAILED version re-evaluated as METADATA_PROCESSED on next sync."""
        asset = make_asset("a1", "IMG_001.JPG")
        lib = make_photo_library(all_assets=[asset])

        # First sync: download fails
        failing_dm = Mock()
        failing_dm.download_asset_versions.return_value = ([], [list(asset.versions.keys())[0]])
        failing_dm.cleanup.return_value = None

        sm = sync_env.make_sync_manager(photo_library=lib, download_manager=failing_dm)
        sm.sync_photos(MockPhotosToSync([asset]))

        statuses = sync_env.database.get_all_sync_statuses("a1")
        failed_statuses = [s for s in statuses if s.sync_status == SyncState.FAILED]
        assert len(failed_statuses) > 0

        # Second sync: download succeeds
        working_dm = make_downloading_manager(sync_env)
        sync_env.clock.advance("2024-02-01T00:00:00")
        sm2 = sync_env.make_sync_manager(photo_library=lib, download_manager=working_dm)
        sm2.sync_photos(MockPhotosToSync([asset]))

        statuses_after = sync_env.database.get_all_sync_statuses("a1")
        completed = [s for s in statuses_after if s.sync_status == SyncState.COMPLETED]
        assert len(completed) > 0
        assert collect_symlinks(sync_env.temp_dir, "Library")
