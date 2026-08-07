"""In-memory ring buffer of log records, tailed by the web UI."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any


class RingBufferHandler(logging.Handler):
    """Keeps the most recent log records so the GUI can tail them."""

    def __init__(self, capacity: int = 3000) -> None:
        super().__init__()
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - never let logging break the app
            return
        with self._lock:
            self._seq += 1
            self._records.append(
                {
                    "seq": self._seq,
                    "ts": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "message": message,
                }
            )

    def tail(self, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            records = [r for r in self._records if r["seq"] > after]
        return records[-limit:]

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._seq


ring_handler = RingBufferHandler()


def configure_logging(level: str = "INFO") -> None:
    """Wire up stdout logging plus the ring buffer used by the GUI."""
    ring_handler.setFormatter(logging.Formatter("%(message)s"))

    stream = logging.StreamHandler()
    stream.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s %(name)-22s %(message)s")
    )

    root = logging.getLogger()
    root.handlers = [stream, ring_handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # These are chatty and rarely useful at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
