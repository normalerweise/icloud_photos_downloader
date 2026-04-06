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
    """Terminal-based progress reporter with progress bars (TTY) or log lines (container)."""

    def __init__(self) -> None:
        self.current_phase: str | None = None
        self.total_items: int = 0
        self.current_item: int = 0
        self.phase_start_time: float | None = None
        self._is_tty: bool = sys.stdout.isatty()

    def phase_start(self, phase_name: str, total_items: int) -> None:
        self.current_phase = phase_name
        self.total_items = total_items
        self.current_item = 0
        self.phase_start_time = time.time()

        logger.info(f"{phase_name} ({total_items} items)")
        if self._is_tty:
            self._write_progress_bar(0, total_items)

    def phase_progress(self, current: int, total: int, **kwargs: Any) -> None:
        self.current_item = current

        speed_info = ""
        if self.phase_start_time and current > 0:
            elapsed = time.time() - self.phase_start_time
            if elapsed > 0:
                speed = current / elapsed * 60
                eta_seconds = (total - current) / (current / elapsed)
                eta_minutes = int(eta_seconds / 60)
                speed_info = f" (speed: {speed:.1f} assets/min; ETA: {eta_minutes} min)"

        if self._is_tty:
            self._write_progress_bar(current, total, speed_info)

    def phase_complete(self, phase_name: str, stats: Dict[str, Any]) -> None:
        if self.current_phase == phase_name:
            if self._is_tty:
                self._write_progress_bar(self.total_items, self.total_items)
            if "processed" in stats:
                logger.info(f"{phase_name} completed: {stats['processed']} assets processed")
            if "to_download" in stats:
                logger.info(f"{phase_name} completed: {stats['to_download']} assets to download")

    def sync_complete(self, final_stats: Dict[str, Any]) -> None:
        lines = [
            f"Total assets: {final_stats.get('total_assets', 0)}",
            f"Downloaded assets: {final_stats.get('downloaded_assets', 0)}",
            f"Failed assets: {final_stats.get('failed_assets', 0)}",
        ]
        deleted = final_stats.get('deleted_assets', 0)
        if deleted > 0:
            lines.append(f"Deleted (mirrored from iCloud): {deleted}")
        if 'disk_usage_gb' in final_stats:
            lines.append(f"Disk usage: {final_stats['disk_usage_gb']:.2f} GB")
        logger.info("SYNC COMPLETED — " + ", ".join(lines))

    def _write_progress_bar(self, current: int, total: int, additional_info: str = "") -> None:
        if total == 0:
            return
        percentage = (current / total) * 100
        bar_length = 40
        filled_length = int(bar_length * current // total)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        sys.stdout.write(f"\r{bar} {percentage:5.1f}% ({current}/{total}){additional_info}")
        sys.stdout.flush()
        if current >= total:
            sys.stdout.write("\n")


class WebUIProgressReporter(ProgressReporter):
    """Bridges ProgressReporter to the web UI's Progress object."""

    def __init__(self, progress: Any) -> None:
        self.progress = progress

    def phase_start(self, phase_name: str, total_items: int) -> None:
        self.progress.photos_count = total_items
        self.progress.photos_counter = 0
        self.progress.photos_last_message = f"Starting {phase_name}"

    def phase_progress(self, current: int, total: int, **kwargs: Any) -> None:
        self.progress.photos_counter = current

    def phase_complete(self, phase_name: str, stats: Dict[str, Any]) -> None:
        self.progress.photos_last_message = f"Completed {phase_name}"

    def sync_complete(self, final_stats: Dict[str, Any]) -> None:
        self.progress.photos_last_message = "Sync complete"


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