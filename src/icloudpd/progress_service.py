#!/usr/bin/env python
"""Progress reporting and user feedback service."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .error_handling_service import ProductionLogger
from .models import Photo
from .types import FileSizeBytes, PhotoCount


@dataclass(frozen=True)
class ProgressSnapshot:
    """Immutable snapshot of progress at a point in time."""
    
    total_photos: PhotoCount
    completed_photos: PhotoCount
    failed_photos: PhotoCount
    bytes_downloaded: FileSizeBytes
    bytes_total: FileSizeBytes
    elapsed_time: timedelta
    current_operation: str
    current_photo: Optional[Photo] = None
    
    @property
    def completion_percentage(self) -> float:
        """Calculate completion percentage."""
        if self.total_photos == 0:
            return 100.0
        return (self.completed_photos / self.total_photos) * 100.0
    
    @property
    def download_percentage(self) -> float:
        """Calculate download percentage by bytes."""
        if self.bytes_total == 0:
            return 100.0
        return (self.bytes_downloaded / self.bytes_total) * 100.0
    
    @property
    def photos_per_second(self) -> float:
        """Calculate photos processed per second."""
        if self.elapsed_time.total_seconds() == 0:
            return 0.0
        return self.completed_photos / self.elapsed_time.total_seconds()
    
    @property
    def bytes_per_second(self) -> float:
        """Calculate bytes downloaded per second."""
        if self.elapsed_time.total_seconds() == 0:
            return 0.0
        return self.bytes_downloaded / self.elapsed_time.total_seconds()
    
    @property
    def estimated_time_remaining(self) -> Optional[timedelta]:
        """Estimate time remaining based on current progress."""
        if self.completion_percentage >= 100.0:
            return timedelta(0)
        
        photos_per_sec = self.photos_per_second
        if photos_per_sec <= 0:
            return None
        
        remaining_photos = self.total_photos - self.completed_photos
        remaining_seconds = remaining_photos / photos_per_sec
        return timedelta(seconds=remaining_seconds)
    
    @property
    def summary_line(self) -> str:
        """Get single-line progress summary."""
        eta_str = ""
        if self.estimated_time_remaining:
            eta_minutes = int(self.estimated_time_remaining.total_seconds() / 60)
            eta_str = f" | ETA: {eta_minutes}m"
        
        return (f"{self.completed_photos}/{self.total_photos} photos "
                f"({self.completion_percentage:.1f}%) | "
                f"{self.bytes_downloaded / 1024 / 1024:.1f}MB downloaded | "
                f"{self.photos_per_second:.1f} photos/s{eta_str}")


class ProgressTracker:
    """Thread-safe progress tracking with real-time updates."""
    
    def __init__(self, total_photos: PhotoCount, total_bytes: FileSizeBytes, logger: ProductionLogger) -> None:
        self.total_photos = total_photos
        self.total_bytes = total_bytes
        self.logger = logger
        
        # Progress state
        self.completed_photos = PhotoCount(0)
        self.failed_photos = PhotoCount(0)
        self.bytes_downloaded = FileSizeBytes(0)
        self.start_time = datetime.now()
        self.current_operation = "Initializing"
        self.current_photo: Optional[Photo] = None
        
        # Display state
        self.last_progress_time = datetime.now()
        self.progress_interval = timedelta(seconds=2)  # Update every 2 seconds
    
    def start_operation(self, operation_name: str) -> None:
        """Start a new operation."""
        self.current_operation = operation_name
        self.logger.info(f"Starting {operation_name}")
        self._maybe_show_progress()
    
    def set_current_photo(self, photo: Photo) -> None:
        """Set currently processing photo."""
        self.current_photo = photo
        self._maybe_show_progress()
    
    def photo_completed(self, photo: Photo, bytes_downloaded: FileSizeBytes) -> None:
        """Mark a photo as completed."""
        self.completed_photos = PhotoCount(self.completed_photos + 1)
        self.bytes_downloaded = FileSizeBytes(self.bytes_downloaded + bytes_downloaded)
        self.logger.info(
            f"Completed {photo.filename}",
            size_mb=f"{bytes_downloaded / 1024 / 1024:.1f}MB",
            progress=f"{self.completed_photos}/{self.total_photos}"
        )
        self._maybe_show_progress()
    
    def photo_failed(self, photo: Photo, error_message: str) -> None:
        """Mark a photo as failed."""
        self.failed_photos = PhotoCount(self.failed_photos + 1)
        self.logger.warning(
            f"Failed {photo.filename}: {error_message}",
            progress=f"{self.completed_photos}/{self.total_photos}"
        )
        self._maybe_show_progress()
    
    def _maybe_show_progress(self) -> None:
        """Show progress if enough time has elapsed."""
        now = datetime.now()
        if now - self.last_progress_time >= self.progress_interval:
            self._show_current_progress()
            self.last_progress_time = now
    
    def _show_current_progress(self) -> None:
        """Display current progress to user."""
        snapshot = self.get_snapshot()
        
        # Create progress bar
        bar_width = 40
        completed_width = int(snapshot.completion_percentage / 100 * bar_width)
        bar = "█" * completed_width + "░" * (bar_width - completed_width)
        
        # Format current operation
        current_file = ""
        if snapshot.current_photo:
            current_file = f" | {snapshot.current_photo.filename}"
        
        progress_line = (f"\r{snapshot.current_operation}: [{bar}] "
                        f"{snapshot.summary_line}{current_file}")
        
        # Print without newline to overwrite previous line
        print(progress_line[:120], end='', flush=True)
    
    def get_snapshot(self) -> ProgressSnapshot:
        """Get current progress snapshot."""
        elapsed = datetime.now() - self.start_time
        
        return ProgressSnapshot(
            total_photos=self.total_photos,
            completed_photos=self.completed_photos,
            failed_photos=self.failed_photos,
            bytes_downloaded=self.bytes_downloaded,
            bytes_total=self.total_bytes,
            elapsed_time=elapsed,
            current_operation=self.current_operation,
            current_photo=self.current_photo,
        )
    
    def finish(self) -> ProgressSnapshot:
        """Finish progress tracking and return final snapshot."""
        final_snapshot = self.get_snapshot()
        
        # Clear progress line and show final result
        print("\r" + " " * 120, end='\r')  # Clear line
        
        success_rate = (self.completed_photos / max(1, self.total_photos)) * 100
        
        self.logger.info(
            f"Operation completed",
            total_photos=self.total_photos,
            completed=self.completed_photos,
            failed=self.failed_photos,
            success_rate=f"{success_rate:.1f}%",
            elapsed_time=str(final_snapshot.elapsed_time).split('.')[0],  # Remove microseconds
            total_size_mb=f"{self.bytes_downloaded / 1024 / 1024:.1f}MB"
        )
        
        return final_snapshot


class UserFeedbackService:
    """Service for providing rich user feedback and interaction."""
    
    def __init__(self, logger: ProductionLogger, verbose: bool = False) -> None:
        self.logger = logger
        self.verbose = verbose
    
    def show_sync_start(self, total_photos: PhotoCount, total_size_mb: float, config_summary: str) -> None:
        """Show sync start information."""
        print(f"\n🚀 Starting iCloud Photos sync")
        print(f"   Photos to process: {total_photos}")
        print(f"   Total size: {total_size_mb:.1f}MB")
        print(f"   Configuration: {config_summary}")
        print("")
    
    def show_filtering_results(self, original_count: int, filtered_count: int, filters_applied: list[str]) -> None:
        """Show filtering results."""
        if not filters_applied:
            return
        
        reduction = ((original_count - filtered_count) / max(1, original_count)) * 100
        
        print(f"🔍 Filtering applied:")
        print(f"   Original photos: {original_count}")
        print(f"   After filtering: {filtered_count}")
        print(f"   Reduction: {reduction:.1f}%")
        print(f"   Filters: {', '.join(filters_applied)}")
        print("")
    
    def show_change_detection_results(self, new_count: int, deleted_count: int, modified_count: int) -> None:
        """Show change detection results."""
        total_changes = new_count + deleted_count + modified_count
        
        if total_changes == 0:
            print("✅ No changes detected - your library is up to date!")
            return
        
        print(f"📋 Changes detected:")
        if new_count > 0:
            print(f"   📥 New photos: {new_count}")
        if modified_count > 0:
            print(f"   🔄 Modified photos: {modified_count}")
        if deleted_count > 0:
            print(f"   🗑️  Deleted photos: {deleted_count}")
        print("")
    
    def show_directory_structure(self, data_dir: str, timeline_dir: str, library_dir: str) -> None:
        """Show directory structure information."""
        if self.verbose:
            print(f"📁 Directory structure:")
            print(f"   Data: {data_dir}")
            print(f"   Timeline: {timeline_dir}")
            print(f"   Library: {library_dir}")
            print("")
    
    def show_operation_summary(self, operation: str, success_count: int, failure_count: int, elapsed_time: str) -> None:
        """Show operation summary."""
        total = success_count + failure_count
        success_rate = (success_count / max(1, total)) * 100
        
        status_emoji = "✅" if failure_count == 0 else "⚠️" if success_rate >= 80 else "❌"
        
        print(f"\n{status_emoji} {operation} completed")
        print(f"   Successful: {success_count}")
        if failure_count > 0:
            print(f"   Failed: {failure_count}")
        print(f"   Success rate: {success_rate:.1f}%")
        print(f"   Time elapsed: {elapsed_time}")
    
    def show_final_summary(self, total_downloaded: int, total_size_mb: float, errors_summary: str) -> None:
        """Show final operation summary."""
        print(f"\n🎉 Sync completed!")
        print(f"   Photos downloaded: {total_downloaded}")
        print(f"   Total size: {total_size_mb:.1f}MB")
        
        if errors_summary:
            print(f"\n⚠️  Errors encountered:")
            print(f"   {errors_summary}")
        
        print(f"\n💡 Your photos are organized in:")
        print(f"   📅 Timeline view (by date)")
        print(f"   📚 Library view (by album)")
        print("")
    
    def prompt_user_confirmation(self, message: str, default: bool = True) -> bool:
        """Prompt user for confirmation."""
        default_text = "Y/n" if default else "y/N"
        
        try:
            response = input(f"{message} ({default_text}): ").strip().lower()
            
            if not response:
                return default
            
            return response in ['y', 'yes', 'true', '1']
            
        except (KeyboardInterrupt, EOFError):
            return False
    
    def show_warning(self, message: str, details: Optional[str] = None) -> None:
        """Show warning message to user."""
        print(f"\n⚠️  Warning: {message}")
        if details:
            print(f"   {details}")
        print("")
    
    def show_error(self, message: str, recovery_suggestion: Optional[str] = None) -> None:
        """Show error message to user."""
        print(f"\n❌ Error: {message}")
        if recovery_suggestion:
            print(f"   💡 Suggestion: {recovery_suggestion}")
        print("")


# Factory functions
def create_progress_tracker(total_photos: PhotoCount, total_bytes: FileSizeBytes, logger: ProductionLogger) -> ProgressTracker:
    """Factory function to create a progress tracker."""
    return ProgressTracker(total_photos, total_bytes, logger)


def create_user_feedback(logger: ProductionLogger, verbose: bool = False) -> UserFeedbackService:
    """Factory function to create user feedback service."""
    return UserFeedbackService(logger, verbose)