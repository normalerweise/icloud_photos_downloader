"""Progress reporting interface and implementations."""

import logging
import sys
import time
from abc import ABC, abstractmethod
from typing import Any, Dict

logger = logging.getLogger(__name__)


class ProgressReporter(ABC):
    """Abstract interface for progress reporting."""

    @abstractmethod
    def phase_start(self, phase_name: str, total_items: int) -> None:
        """Called when a new phase starts.
        
        Args:
            phase_name: Name of the phase (e.g., "Change detection", "Downloading assets")
            total_items: Total number of items to process in this phase
        """
        pass

    @abstractmethod
    def phase_progress(self, current: int, total: int, **kwargs: Any) -> None:
        """Called to report progress within a phase.
        
        Args:
            current: Current number of processed items
            total: Total number of items to process
            **kwargs: Additional progress information (speed, eta, etc.)
        """
        pass

    @abstractmethod
    def phase_complete(self, phase_name: str, stats: Dict[str, Any]) -> None:
        """Called when a phase completes.
        
        Args:
            phase_name: Name of the completed phase
            stats: Statistics about the completed phase
        """
        pass

    @abstractmethod
    def sync_complete(self, final_stats: Dict[str, Any]) -> None:
        """Called when the entire sync process completes.
        
        Args:
            final_stats: Final statistics for the entire sync
        """
        pass


class TerminalProgressReporter(ProgressReporter):
    """Terminal-based progress reporter with beautiful progress bars."""

    def __init__(self):
        """Initialize terminal progress reporter."""
        self.current_phase: str | None = None
        self.total_items: int = 0
        self.current_item: int = 0
        self.phase_start_time: float | None = None
        self.last_update_time: float | None = None

    def phase_start(self, phase_name: str, total_items: int) -> None:
        """Start a new phase with progress bar."""
        self.current_phase = phase_name
        self.total_items = total_items
        self.current_item = 0
        self.phase_start_time = time.time()
        self.last_update_time = time.time()

        print(f"\n{phase_name}")
        self._print_progress_bar(0, total_items)

    def phase_progress(self, current: int, total: int, **kwargs: Any) -> None:
        """Update progress within current phase."""
        self.current_item = current
        
        # Calculate speed and ETA
        current_time = time.time()
        speed_info = ""
        if self.phase_start_time and current > 0:
            elapsed = current_time - self.phase_start_time
            if elapsed > 0:
                speed = current / elapsed * 60  # items per minute
                eta_seconds = (total - current) / (current / elapsed) if current > 0 else 0
                eta_minutes = int(eta_seconds / 60)
                speed_info = f" (speed: {speed:.1f} assets/min; ETA: {eta_minutes} min)"

        # Update progress bar
        self._print_progress_bar(current, total, speed_info)
        self.last_update_time = current_time

    def phase_complete(self, phase_name: str, stats: Dict[str, Any]) -> None:
        """Complete the current phase."""
        if self.current_phase == phase_name:
            # Print final progress bar at 100%
            self._print_progress_bar(self.total_items, self.total_items)
            
            # Print phase completion message
            if "processed" in stats:
                print(f"\n{phase_name} completed: {stats['processed']} assets processed")
            if "to_download" in stats:
                print(f"{phase_name} completed: {stats['to_download']} assets to download")

    def sync_complete(self, final_stats: Dict[str, Any]) -> None:
        """Complete the entire sync process."""
        print("\n" + "="*50)
        print("SYNC COMPLETED")
        print("="*50)
        print(f"Total assets: {final_stats.get('total_assets', 0)}")
        print(f"Downloaded assets: {final_stats.get('downloaded_assets', 0)}")
        print(f"Failed assets: {final_stats.get('failed_assets', 0)}")
        
        if 'disk_usage_gb' in final_stats:
            print(f"Disk usage: {final_stats['disk_usage_gb']:.2f} GB")
        print("="*50)

    def _print_progress_bar(self, current: int, total: int, additional_info: str = "") -> None:
        """Print a beautiful progress bar to terminal."""
        if total == 0:
            return

        percentage = (current / total) * 100
        bar_length = 40
        filled_length = int(bar_length * current // total)
        
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        
        # Clear line and print progress
        sys.stdout.write(f"\r{bar} {percentage:5.1f}% ({current}/{total}){additional_info}")
        sys.stdout.flush()
        
        # If complete, add newline
        if current >= total:
            print()


class LoggingProgressReporter(ProgressReporter):
    """Simple logging-based progress reporter for non-interactive environments."""

    def phase_start(self, phase_name: str, total_items: int) -> None:
        """Log phase start."""
        logger.info(f"Starting {phase_name} with {total_items} items")

    def phase_progress(self, current: int, total: int, **kwargs: Any) -> None:
        """Log progress."""
        if current % 10 == 0 or current == total:  # Log every 10 items or on completion
            percentage = (current / total) * 100 if total > 0 else 0
            logger.info(f"Progress: {current}/{total} ({percentage:.1f}%)")

    def phase_complete(self, phase_name: str, stats: Dict[str, Any]) -> None:
        """Log phase completion."""
        logger.info(f"Completed {phase_name}: {stats}")

    def sync_complete(self, final_stats: Dict[str, Any]) -> None:
        """Log sync completion."""
        logger.info(f"Sync completed: {final_stats}") 