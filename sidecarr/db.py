"""SQLite-backed run history.

Each sync produces one ``runs`` row plus a ``run_items`` row per title, so the
GUI can show not just what was added but what was skipped and why.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id      TEXT    NOT NULL,
    list_name    TEXT    NOT NULL,
    media_type   TEXT    NOT NULL,
    started_at   REAL    NOT NULL,
    finished_at  REAL,
    status       TEXT    NOT NULL,
    dry_run      INTEGER NOT NULL DEFAULT 0,
    candidates   INTEGER NOT NULL DEFAULT 0,
    filtered     INTEGER NOT NULL DEFAULT 0,
    existing     INTEGER NOT NULL DEFAULT 0,
    excluded     INTEGER NOT NULL DEFAULT 0,
    added        INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    message      TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS run_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    year        INTEGER,
    external_id TEXT,
    action      TEXT    NOT NULL,
    reason      TEXT    NOT NULL DEFAULT '',
    at          REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_started  ON runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_list     ON runs (list_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_run     ON run_items (run_id);
"""


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else settings.DB_FILE
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(_SCHEMA)

    # -- writes ------------------------------------------------------------ #

    def start_run(self, list_id: str, list_name: str, media_type: str, dry_run: bool) -> int:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs (list_id, list_name, media_type, started_at, status, dry_run)"
                " VALUES (?, ?, ?, ?, 'running', ?)",
                (list_id, list_name, media_type, time.time(), int(dry_run)),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, message: str = "", **counters: int) -> None:
        fields = ["finished_at = ?", "status = ?", "message = ?"]
        values: list[Any] = [time.time(), status, message]
        for name in ("candidates", "filtered", "existing", "excluded", "added", "failed"):
            if name in counters:
                fields.append(f"{name} = ?")
                values.append(int(counters[name]))
        values.append(run_id)
        with self._lock, self.connect() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", values)

    def add_item(
        self,
        run_id: int,
        title: str,
        year: int | None,
        external_id: str | None,
        action: str,
        reason: str = "",
    ) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO run_items (run_id, title, year, external_id, action, reason, at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, title, year, external_id, action, reason, time.time()),
            )

    def add_items(self, run_id: int, rows: list[tuple[str, int | None, str | None, str, str]]) -> None:
        if not rows:
            return
        now = time.time()
        with self._lock, self.connect() as conn:
            conn.executemany(
                "INSERT INTO run_items (run_id, title, year, external_id, action, reason, at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(run_id, *row, now) for row in rows],
            )

    def prune(self, keep_runs: int = 200) -> None:
        """Drop the oldest runs so /config doesn't grow without bound."""
        with self._lock, self.connect() as conn:
            conn.execute(
                "DELETE FROM run_items WHERE run_id IN ("
                "  SELECT id FROM runs ORDER BY started_at DESC LIMIT -1 OFFSET ?"
                ")",
                (keep_runs,),
            )
            conn.execute(
                "DELETE FROM runs WHERE id IN ("
                "  SELECT id FROM runs ORDER BY started_at DESC LIMIT -1 OFFSET ?"
                ")",
                (keep_runs,),
            )

    # -- reads ------------------------------------------------------------- #

    def recent_runs(self, limit: int = 25, list_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM runs"
        params: list[Any] = []
        if list_id:
            query += " WHERE list_id = ?"
            params.append(list_id)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params)]

    def run_items(self, run_id: int, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_items WHERE run_id = ? ORDER BY id LIMIT ?",
                (run_id, limit),
            )
            return [dict(row) for row in rows]

    def last_success_at(self, list_id: str) -> float | None:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT finished_at FROM runs"
                " WHERE list_id = ? AND status IN ('success', 'partial') AND finished_at IS NOT NULL"
                " ORDER BY finished_at DESC LIMIT 1",
                (list_id,),
            ).fetchone()
        return float(row["finished_at"]) if row and row["finished_at"] else None

    def totals(self) -> dict[str, int]:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS runs, COALESCE(SUM(added), 0) AS added FROM runs"
            ).fetchone()
        return {"runs": int(row["runs"]), "added": int(row["added"])}


db = Database()
