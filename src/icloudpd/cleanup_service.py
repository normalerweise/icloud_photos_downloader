#!/usr/bin/env python
"""Incremental updates and local file cleanup service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Optional, Union

from .change_detection_service import LocalPhotoState, PhotoChanges
from .models import DirectoryStructure, Photo
from .pure_functions import calculate_data_path, calculate_library_paths, calculate_timeline_path
from .types import DataPath, LibraryPath, PhotoCount, SymlinkPath, TimelinePath


@dataclass(frozen=True)
class CleanupResult:
    """Immutable result of cleanup operations."""
    
    files_moved_to_deleted: PhotoCount
    symlinks_removed: PhotoCount
    orphaned_symlinks_cleaned: PhotoCount
    empty_directories_removed: PhotoCount
    errors: FrozenSet[str]
    
    @property
    def total_cleanup_actions(self) -> PhotoCount:
        """Get total number of cleanup actions performed."""
        return PhotoCount(
            self.files_moved_to_deleted + 
            self.symlinks_removed + 
            self.orphaned_symlinks_cleaned + 
            self.empty_directories_removed
        )
    
    @property
    def success(self) -> bool:
        """Check if cleanup was successful (no errors)."""
        return len(self.errors) == 0
    
    @property
    def summary(self) -> str:
        """Get human-readable summary of cleanup."""
        return (f"Cleanup: {self.files_moved_to_deleted} files moved, "
                f"{self.symlinks_removed} symlinks removed, "
                f"{self.orphaned_symlinks_cleaned} orphans cleaned, "
                f"{self.empty_directories_removed} dirs removed, "
                f"{len(self.errors)} errors")


@dataclass(frozen=True)
class IncrementalUpdateState:
    """Immutable state for incremental updates."""
    
    current_local_state: LocalPhotoState
    previous_local_state: Optional[LocalPhotoState]
    detected_changes: PhotoChanges
    cleanup_performed: bool
    last_update_time: Optional[str] = None
    
    @property
    def has_previous_state(self) -> bool:
        """Check if previous state is available."""
        return self.previous_local_state is not None
    
    @property
    def needs_cleanup(self) -> bool:
        """Check if cleanup is needed based on changes."""
        return (len(self.detected_changes.deleted_photos) > 0 or 
                len(self.detected_changes.modified_photos) > 0)


class LocalFileCleanupService:
    """Service for cleaning up local files and maintaining directory structure."""
    
    def __init__(self, directory_structure: DirectoryStructure, dry_run: bool = False) -> None:
        self.structure = directory_structure
        self.dry_run = dry_run
    
    def cleanup_deleted_photos(self, deleted_photos: FrozenSet[Photo]) -> CleanupResult:
        """Clean up photos that were deleted from iCloud."""
        files_moved = 0
        symlinks_removed = 0
        errors = set()
        
        for photo in deleted_photos:
            try:
                # Move photo file to deleted directory
                move_result = self._move_to_deleted_directory(photo)
                if move_result:
                    files_moved += 1
                
                # Remove symlinks
                symlink_count = self._remove_photo_symlinks(photo)
                symlinks_removed += symlink_count
                
            except Exception as e:
                errors.add(f"Failed to cleanup {photo.filename}: {e}")
        
        # Clean up orphaned symlinks and empty directories
        orphaned_cleaned = self._clean_orphaned_symlinks()
        empty_dirs_removed = self._remove_empty_directories()
        
        return CleanupResult(
            files_moved_to_deleted=PhotoCount(files_moved),
            symlinks_removed=PhotoCount(symlinks_removed),
            orphaned_symlinks_cleaned=PhotoCount(orphaned_cleaned),
            empty_directories_removed=PhotoCount(empty_dirs_removed),
            errors=frozenset(errors),
        )
    
    def _move_to_deleted_directory(self, photo: Photo) -> bool:
        """Move a photo file to the deleted directory."""
        try:
            source_path = Path(self.structure.data_dir) / photo.filename
            target_path = Path(self.structure.deleted_dir) / photo.filename
            
            if not source_path.exists():
                return False  # Already moved or doesn't exist
            
            if self.dry_run:
                print(f"[DRY RUN] Would move {source_path} to {target_path}")
                return True
            
            # Ensure deleted directory exists
            Path(self.structure.deleted_dir).mkdir(parents=True, exist_ok=True)
            
            # Move file
            source_path.rename(target_path)
            return True
            
        except Exception as e:
            print(f"Failed to move {photo.filename} to deleted directory: {e}")
            return False
    
    def _remove_photo_symlinks(self, photo: Photo) -> int:
        """Remove all symlinks for a photo."""
        removed_count = 0
        
        try:
            # Remove timeline symlink
            timeline_path = calculate_timeline_path(photo, self.structure.timeline_dir)
            if self._remove_symlink_if_exists(Path(timeline_path)):
                removed_count += 1
            
            # Remove library symlinks
            library_paths = calculate_library_paths(photo, self.structure.library_dir)
            for library_path in library_paths:
                if self._remove_symlink_if_exists(Path(library_path)):
                    removed_count += 1
                    
        except Exception as e:
            print(f"Error removing symlinks for {photo.filename}: {e}")
        
        return removed_count
    
    def _remove_symlink_if_exists(self, symlink_path: Path) -> bool:
        """Remove symlink if it exists."""
        try:
            if symlink_path.is_symlink():
                if self.dry_run:
                    print(f"[DRY RUN] Would remove symlink {symlink_path}")
                    return True
                else:
                    symlink_path.unlink()
                    return True
        except Exception as e:
            print(f"Failed to remove symlink {symlink_path}: {e}")
        
        return False
    
    def _clean_orphaned_symlinks(self) -> int:
        """Clean up symlinks that point to non-existent files."""
        orphaned_count = 0
        
        # Check timeline directory
        orphaned_count += self._clean_orphaned_in_directory(Path(self.structure.timeline_dir))
        
        # Check library directory
        orphaned_count += self._clean_orphaned_in_directory(Path(self.structure.library_dir))
        
        return orphaned_count
    
    def _clean_orphaned_in_directory(self, directory: Path) -> int:
        """Clean orphaned symlinks in a specific directory."""
        if not directory.exists():
            return 0
        
        orphaned_count = 0
        
        for item in directory.rglob('*'):
            if item.is_symlink():
                try:
                    # Check if symlink target exists
                    if not item.exists():
                        if self.dry_run:
                            print(f"[DRY RUN] Would remove orphaned symlink {item}")
                            orphaned_count += 1
                        else:
                            item.unlink()
                            orphaned_count += 1
                except Exception as e:
                    print(f"Error checking symlink {item}: {e}")
        
        return orphaned_count
    
    def _remove_empty_directories(self) -> int:
        """Remove empty directories in timeline and library hierarchies."""
        removed_count = 0
        
        # Remove empty directories in timeline
        removed_count += self._remove_empty_dirs_in_hierarchy(Path(self.structure.timeline_dir))
        
        # Remove empty directories in library
        removed_count += self._remove_empty_dirs_in_hierarchy(Path(self.structure.library_dir))
        
        return removed_count
    
    def _remove_empty_dirs_in_hierarchy(self, base_directory: Path) -> int:
        """Remove empty directories in a hierarchy (bottom-up)."""
        if not base_directory.exists():
            return 0
        
        removed_count = 0
        
        # Walk the directory tree bottom-up to remove empty directories
        for root, dirs, files in os.walk(base_directory, topdown=False):
            root_path = Path(root)
            
            # Skip the base directory itself
            if root_path == base_directory:
                continue
            
            try:
                # Check if directory is empty (no files, no non-empty subdirectories)
                if not any(root_path.iterdir()):
                    if self.dry_run:
                        print(f"[DRY RUN] Would remove empty directory {root_path}")
                        removed_count += 1
                    else:
                        root_path.rmdir()
                        removed_count += 1
            except Exception as e:
                print(f"Error removing empty directory {root_path}: {e}")
        
        return removed_count


class IncrementalUpdateService:
    """Service for managing incremental updates with state tracking."""
    
    def __init__(self, cleanup_service: LocalFileCleanupService) -> None:
        self.cleanup_service = cleanup_service
    
    def perform_incremental_update(
        self, 
        changes: PhotoChanges, 
        current_state: LocalPhotoState,
        previous_state: Optional[LocalPhotoState] = None
    ) -> IncrementalUpdateState:
        """Perform incremental update based on detected changes."""
        
        cleanup_performed = False
        
        # Perform cleanup if there are deleted or modified photos
        if len(changes.deleted_photos) > 0:
            cleanup_result = self.cleanup_service.cleanup_deleted_photos(changes.deleted_photos)
            cleanup_performed = cleanup_result.success
            
            print(f"Cleanup completed: {cleanup_result.summary}")
        
        # Handle modified photos (treat as delete + re-add)
        if len(changes.modified_photos) > 0:
            cleanup_result = self.cleanup_service.cleanup_deleted_photos(changes.modified_photos)
            cleanup_performed = cleanup_performed and cleanup_result.success
            
            print(f"Modified photos cleanup: {cleanup_result.summary}")
        
        return IncrementalUpdateState(
            current_local_state=current_state,
            previous_local_state=previous_state,
            detected_changes=changes,
            cleanup_performed=cleanup_performed,
            last_update_time=str(current_state.last_scan_time),
        )
    
    def validate_local_state_consistency(self, local_state: LocalPhotoState) -> dict[str, Union[bool, int]]:
        """Validate consistency of local state."""
        data_dir = Path(self.cleanup_service.structure.data_dir)
        timeline_dir = Path(self.cleanup_service.structure.timeline_dir)
        library_dir = Path(self.cleanup_service.structure.library_dir)
        
        # Count actual files vs. tracked photos
        actual_files = 0
        if data_dir.exists():
            actual_files = len([f for f in data_dir.iterdir() if f.is_file()])
        
        # Count symlinks
        timeline_symlinks = 0
        if timeline_dir.exists():
            timeline_symlinks = len([f for f in timeline_dir.rglob('*') if f.is_symlink()])
        
        library_symlinks = 0
        if library_dir.exists():
            library_symlinks = len([f for f in library_dir.rglob('*') if f.is_symlink()])
        
        # Check for orphaned symlinks
        orphaned_timeline = self.cleanup_service._clean_orphaned_in_directory(timeline_dir) if timeline_dir.exists() else 0
        orphaned_library = self.cleanup_service._clean_orphaned_in_directory(library_dir) if library_dir.exists() else 0
        
        return {
            'tracked_photos_match_files': actual_files == local_state.total_count,
            'actual_files_count': actual_files,
            'tracked_photos_count': local_state.total_count,
            'timeline_symlinks': timeline_symlinks,
            'library_symlinks': library_symlinks,
            'orphaned_timeline': orphaned_timeline,
            'orphaned_library': orphaned_library,
            'has_orphaned_symlinks': (orphaned_timeline + orphaned_library) > 0,
        }


class StateAwareCleanupOrchestrator:
    """High-level orchestrator for state-aware cleanup operations."""
    
    def __init__(self, directory_structure: DirectoryStructure, dry_run: bool = False) -> None:
        self.cleanup_service = LocalFileCleanupService(directory_structure, dry_run)
        self.update_service = IncrementalUpdateService(self.cleanup_service)
        self.dry_run = dry_run
    
    def perform_smart_cleanup(
        self, 
        changes: PhotoChanges, 
        current_state: LocalPhotoState,
        previous_state: Optional[LocalPhotoState] = None
    ) -> tuple[IncrementalUpdateState, dict[str, Union[bool, int]]]:
        """Perform comprehensive cleanup with state validation."""
        
        print(f"Starting smart cleanup for {changes.summary}")
        
        # Perform incremental update
        update_state = self.update_service.perform_incremental_update(
            changes, current_state, previous_state
        )
        
        # Validate consistency
        consistency_report = self.update_service.validate_local_state_consistency(current_state)
        
        # Additional cleanup if inconsistencies found
        if consistency_report.get('has_orphaned_symlinks', False):
            print("Found orphaned symlinks, performing additional cleanup...")
            additional_cleanup = self.cleanup_service._clean_orphaned_symlinks()
            print(f"Cleaned {additional_cleanup} orphaned symlinks")
        
        print(f"Smart cleanup completed. Update state: cleanup_performed={update_state.cleanup_performed}")
        
        return update_state, consistency_report


# Factory functions
def create_cleanup_service(directory_structure: DirectoryStructure, dry_run: bool = False) -> LocalFileCleanupService:
    """Factory function to create a cleanup service."""
    return LocalFileCleanupService(directory_structure, dry_run)


def create_incremental_service(directory_structure: DirectoryStructure, dry_run: bool = False) -> IncrementalUpdateService:
    """Factory function to create an incremental update service."""
    cleanup_service = LocalFileCleanupService(directory_structure, dry_run)
    return IncrementalUpdateService(cleanup_service)


def create_cleanup_orchestrator(directory_structure: DirectoryStructure, dry_run: bool = False) -> StateAwareCleanupOrchestrator:
    """Factory function to create a cleanup orchestrator."""
    return StateAwareCleanupOrchestrator(directory_structure, dry_run)