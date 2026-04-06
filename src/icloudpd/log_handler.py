"""Logging handler that captures log records into a LogBuffer for the web UI."""

import logging

from icloudpd.log_buffer import LogBuffer


class WebUILogHandler(logging.Handler):
    """Logging handler that captures log records into a LogBuffer."""

    def __init__(self, log_buffer: LogBuffer) -> None:
        super().__init__()
        self._log_buffer = log_buffer
        self._formatter = logging.Formatter(
            fmt="%(asctime)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            timestamp = self._formatter.formatTime(record)
            self._log_buffer.append(timestamp, record.levelname, record.getMessage())
        except Exception:
            self.handleError(record)
