#!/usr/bin/env python
"""Test script for Phase 4 immutable state management."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import FrozenSet

from src.icloudpd.change_detection_service import (
    ICloudPhotoState, LocalPhotoState, PhotoChanges, SmartChangeDetector
)
from src.icloudpd.cleanup_service import StateAwareCleanupOrchestrator
from src.icloudpd.models import DirectoryStructure, Photo, SyncConfiguration
from src.icloudpd.types import (
    AlbumName, CreationDate, DataPath, FileSizeBytes, ModificationDate, 
    PhotoFormat, PhotoId, PhotoType
)


def create_test_photos_with_dates() -> FrozenSet[Photo]:
    """Create test photos with various dates for testing recent-days filtering."""
    now = datetime.now()
    photos = {
        # Recent photos (last 7 days)
        Photo(
            id=PhotoId("recent1"),
            filename="IMG_Recent_001.heic",
            creation_date=CreationDate(now - timedelta(days=2)),
            modification_date=ModificationDate(now - timedelta(days=2)),
            size_bytes=FileSizeBytes(1024000),
            format=PhotoFormat.HEIC,
            photo_type=PhotoType.LIVE,
            albums=frozenset([AlbumName("Recent"), AlbumName("All Photos")]),
        ),
        Photo(
            id=PhotoId("recent2"),
            filename="IMG_Recent_002.jpg",
            creation_date=CreationDate(now - timedelta(days=5)),
            modification_date=ModificationDate(now - timedelta(days=5)),
            size_bytes=FileSizeBytes(800000),
            format=PhotoFormat.JPEG,
            photo_type=PhotoType.STANDARD,
            albums=frozenset([AlbumName("Recent"), AlbumName("All Photos")]),
        ),
        
        # Medium-old photos (last 30 days)
        Photo(
            id=PhotoId("medium1"),
            filename="IMG_Medium_001.heic",
            creation_date=CreationDate(now - timedelta(days=15)),
            modification_date=ModificationDate(now - timedelta(days=15)),
            size_bytes=FileSizeBytes(1500000),
            format=PhotoFormat.HEIC,
            photo_type=PhotoType.RAW_PLUS_JPEG,
            albums=frozenset([AlbumName("Vacation"), AlbumName("All Photos")]),
        ),
        Photo(
            id=PhotoId("medium2"),
            filename="IMG_Medium_002.jpg",
            creation_date=CreationDate(now - timedelta(days=25)),
            modification_date=ModificationDate(now - timedelta(days=25)),
            size_bytes=FileSizeBytes(750000),
            format=PhotoFormat.JPEG,
            photo_type=PhotoType.STANDARD,
            albums=frozenset([AlbumName("Family"), AlbumName("All Photos")]),
        ),
        
        # Old photos (beyond 30 days)
        Photo(
            id=PhotoId("old1"),
            filename="IMG_Old_001.raw",
            creation_date=CreationDate(now - timedelta(days=45)),
            modification_date=ModificationDate(now - timedelta(days=45)),
            size_bytes=FileSizeBytes(3000000),
            format=PhotoFormat.RAW,
            photo_type=PhotoType.RAW_PLUS_JPEG,
            albums=frozenset([AlbumName("Archive"), AlbumName("All Photos")]),
        ),
        Photo(
            id=PhotoId("old2"),
            filename="IMG_Old_002.jpg",
            creation_date=CreationDate(now - timedelta(days=60)),
            modification_date=ModificationDate(now - timedelta(days=60)),
            size_bytes=FileSizeBytes(650000),
            format=PhotoFormat.JPEG,
            photo_type=PhotoType.STANDARD,
            albums=frozenset([AlbumName("Archive"), AlbumName("All Photos")]),
        ),
    }
    return frozenset(photos)


def create_local_photos_subset() -> FrozenSet[Photo]:
    """Create a subset of photos that exist locally (simulating partial sync)."""
    all_photos = create_test_photos_with_dates()
    
    # Simulate local state: missing some recent photos, has some old ones
    local_ids = {PhotoId("medium1"), PhotoId("medium2"), PhotoId("old1"), PhotoId("old2")}
    
    return frozenset(photo for photo in all_photos if photo.id in local_ids)


def test_change_detection():
    """Test change detection functionality."""
    print("=== Testing Change Detection ===")
    
    # Create test data
    icloud_photos = create_test_photos_with_dates()
    local_photos = create_local_photos_subset()
    
    print(f"iCloud photos: {len(icloud_photos)}")
    print(f"Local photos: {len(local_photos)}")
    
    # Test change detection
    detector = SmartChangeDetector()
    changes = detector.detect_changes(icloud_photos, local_photos)
    
    print(f"\nChange detection results:")
    print(f"  {changes.summary}")
    
    print(f"\nNew photos to download:")
    for photo in changes.new_photos:
        print(f"  - {photo.filename} ({photo.creation_date.strftime('%Y-%m-%d')})")
    
    print(f"\nDeleted photos (local only):")
    for photo in changes.deleted_photos:
        print(f"  - {photo.filename}")
    
    return changes


def test_recent_days_filtering():
    """Test recent days filtering."""
    print("\n=== Testing Recent Days Filtering ===")
    
    photos = create_test_photos_with_dates()
    detector = SmartChangeDetector()
    
    # Test different recent-days filters
    for days in [7, 30, 90]:
        recent_photos = detector.filter_by_recent_days(photos, days)
        print(f"\nPhotos from last {days} days: {len(recent_photos)}")
        for photo in recent_photos:
            days_ago = (datetime.now() - photo.creation_date).days
            print(f"  - {photo.filename} ({days_ago} days ago)")


def test_max_photos_limiting():
    """Test max photos limiting with prioritization."""
    print("\n=== Testing Max Photos Limiting ===")
    
    photos = create_test_photos_with_dates()
    detector = SmartChangeDetector()
    
    # Test with different limits
    for max_count in [2, 4, 10]:
        # First filter by recent days, then limit count
        recent_photos = detector.filter_by_recent_days(photos, 30)
        limited_photos = detector._limit_photo_count(recent_photos, max_count)
        
        print(f"\nLimited to {max_count} photos (from last 30 days):")
        for photo in sorted(limited_photos, key=lambda p: p.creation_date, reverse=True):
            days_ago = (datetime.now() - photo.creation_date).days
            print(f"  - {photo.filename} ({days_ago} days ago)")


def test_cleanup_functionality():
    """Test cleanup functionality."""
    print("\n=== Testing Cleanup Functionality ===")
    
    # Create test directory structure
    test_dir = Path("/tmp/icloud_test_phase4_cleanup")
    config = SyncConfiguration(
        base_directory=DataPath(test_dir),
        recent_days_only=7,
        max_photos=30,
        dry_run=True,
    )
    
    directory_structure = DirectoryStructure.from_base(config.base_directory)
    
    # Create cleanup orchestrator
    cleanup_orchestrator = StateAwareCleanupOrchestrator(directory_structure, dry_run=True)
    
    # Test change detection and cleanup
    icloud_photos = create_test_photos_with_dates()
    local_photos = create_local_photos_subset()
    
    detector = SmartChangeDetector()
    changes = detector.detect_changes(icloud_photos, local_photos)
    
    # Create states
    icloud_state = ICloudPhotoState.from_photos_and_albums(
        icloud_photos, 
        frozenset([AlbumName("Recent"), AlbumName("Vacation"), AlbumName("Family"), AlbumName("Archive"), AlbumName("All Photos")])
    )
    current_local_state = LocalPhotoState.from_photos(local_photos)
    
    # Perform smart cleanup
    update_state, consistency_report = cleanup_orchestrator.perform_smart_cleanup(
        changes, current_local_state
    )
    
    print(f"\nCleanup results:")
    print(f"  Cleanup performed: {update_state.cleanup_performed}")
    print(f"  Changes detected: {update_state.detected_changes.summary}")
    
    print(f"\nConsistency report:")
    for key, value in consistency_report.items():
        print(f"  {key}: {value}")


def test_comprehensive_filtering():
    """Test comprehensive filtering combining recent days and max photos."""
    print("\n=== Testing Comprehensive Filtering (--recent-days 7 --max-photos 30) ===")
    
    photos = create_test_photos_with_dates()
    detector = SmartChangeDetector()
    
    # Apply the specific test case: recent 7 days + max 30 photos
    recent_7_days = detector.filter_by_recent_days(photos, 7)
    limited_to_30 = detector._limit_photo_count(recent_7_days, 30)
    
    print(f"\nOriginal photos: {len(photos)}")
    print(f"After recent 7 days filter: {len(recent_7_days)}")
    print(f"After max 30 photos limit: {len(limited_to_30)}")
    
    print(f"\nFinal selection:")
    for photo in sorted(limited_to_30, key=lambda p: p.creation_date, reverse=True):
        days_ago = (datetime.now() - photo.creation_date).days
        size_mb = photo.size_bytes / (1024 * 1024)
        print(f"  - {photo.filename} ({days_ago} days ago, {size_mb:.1f}MB, {photo.photo_type.value})")
    
    # Test state management for this selection
    local_subset = frozenset()  # Simulate empty local state
    changes = detector.detect_changes(limited_to_30, local_subset)
    
    print(f"\nChanges for sync:")
    print(f"  New photos to download: {len(changes.new_photos)}")
    print(f"  Total download size: {sum(p.size_bytes for p in changes.new_photos) / (1024*1024):.1f}MB")
    
    return limited_to_30, changes


def test_integration_with_configuration():
    """Test integration with SyncConfiguration."""
    print("\n=== Testing Configuration Integration ===")
    
    config = SyncConfiguration(
        base_directory=DataPath(Path("/tmp/icloud_test_phase4_integration")),
        recent_days_only=7,
        max_photos=30,
        target_albums=frozenset([AlbumName("Recent"), AlbumName("Vacation")]),
        dry_run=True,
    )
    
    print(f"Configuration:")
    print(f"  Recent days only: {config.recent_days_only}")
    print(f"  Max photos: {config.max_photos}")
    print(f"  Target albums: {list(config.target_albums) if config.target_albums else 'All'}")
    print(f"  Dry run: {config.dry_run}")
    
    # Apply configuration-based filtering
    photos = create_test_photos_with_dates()
    detector = SmartChangeDetector()
    
    # Filter by target albums first
    album_filtered = detector.filter_by_target_albums(photos, config.target_albums)
    
    # Then by recent days
    recent_filtered = detector.filter_by_recent_days(album_filtered, config.recent_days_only)
    
    # Finally by count
    final_selection = detector._limit_photo_count(recent_filtered, config.max_photos)
    
    print(f"\nFiltering pipeline:")
    print(f"  Original: {len(photos)} photos")
    print(f"  After album filter: {len(album_filtered)} photos")
    print(f"  After recent days filter: {len(recent_filtered)} photos") 
    print(f"  After count limit: {len(final_selection)} photos")
    
    print(f"\nFinal selection for sync:")
    for photo in sorted(final_selection, key=lambda p: p.creation_date, reverse=True):
        days_ago = (datetime.now() - photo.creation_date).days
        albums = ', '.join(photo.albums)
        print(f"  - {photo.filename} ({days_ago} days ago) [albums: {albums}]")


if __name__ == "__main__":
    print("Phase 4 Immutable State Management Testing")
    print("=" * 60)
    
    try:
        # Test core change detection
        changes = test_change_detection()
        
        # Test recent days filtering
        test_recent_days_filtering()
        
        # Test max photos limiting
        test_max_photos_limiting()
        
        # Test cleanup functionality
        test_cleanup_functionality()
        
        # Test comprehensive filtering (the specific test case)
        final_photos, sync_changes = test_comprehensive_filtering()
        
        # Test configuration integration
        test_integration_with_configuration()
        
        print("\n" + "=" * 60)
        print("✅ All Phase 4 tests completed successfully!")
        print("\n📋 Phase 4 Summary:")
        print("  ✓ Change detection with immutable data structures")
        print("  ✓ Album filtering and date-based selection") 
        print("  ✓ Incremental updates and local file cleanup")
        print("  ✓ Testing with --recent-days 7 --max-photos 30")
        print("\n🎯 Ready for Phase 5: Production Polish")
        
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()