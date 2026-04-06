"""Thread-safe bounded ring buffer for log entries."""

import collections
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    level: str
    message: str
    sequence: int


class LogBuffer:
    """Thread-safe bounded ring buffer for log entries."""

    def __init__(self, max_size: int = 200) -> None:
        self._lock = threading.Lock()
        self._buffer: collections.deque[LogEntry] = collections.deque(maxlen=max_size)
        self._sequence: int = 0

    def append(self, timestamp: str, level: str, message: str) -> None:
        with self._lock:
            self._sequence += 1
            self._buffer.append(LogEntry(timestamp, level, message, self._sequence))

    def get_all(self) -> list[LogEntry]:
        with self._lock:
            return list(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
