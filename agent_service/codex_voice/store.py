"""Small SQLite persistence layer for device sessions and idempotent jobs."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class StoreConflict(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class SQLiteAgentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        # Queued transcripts are needed for restart recovery; keep this local DB private.
        os.chmod(self.path, 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS device_sessions (
                    device_id TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL,
                    thread_id TEXT
                );
                CREATE TABLE IF NOT EXISTS session_history (
                    device_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    thread_id TEXT NOT NULL,
                    closed_at TEXT NOT NULL,
                    PRIMARY KEY (device_id, generation)
                );
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    transcript TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS requests_device_status
                    ON requests(device_id, status);
                CREATE INDEX IF NOT EXISTS requests_status_created
                    ON requests(status, created_at);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(requests)").fetchall()
            }
            if "transcript" not in columns:
                connection.execute(
                    "ALTER TABLE requests ADD COLUMN transcript TEXT NOT NULL DEFAULT ''"
                )

            if "context_json" not in columns:
                connection.execute("ALTER TABLE requests ADD COLUMN context_json TEXT")

    def accept_request(
        self,
        request_id: str,
        device_id: str,
        input_hash: str,
        transcript: str,
        queue_limit: int,
        context_json: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM requests WHERE request_id = ?", (request_id,)
                ).fetchone()
                if existing is not None:
                    if (
                        existing["device_id"] != device_id
                        or existing["input_hash"] != input_hash
                    ):
                        raise StoreConflict("idempotency_conflict")
                    connection.commit()
                    return dict(existing), False

                active = connection.execute(
                    "SELECT 1 FROM requests WHERE device_id = ? "
                    "AND status IN ('queued', 'running') LIMIT 1",
                    (device_id,),
                ).fetchone()
                if active is not None:
                    raise StoreConflict("device_busy")

                queued = connection.execute(
                    "SELECT COUNT(*) FROM requests WHERE status = 'queued'"
                ).fetchone()[0]
                if queued >= queue_limit:
                    raise StoreConflict("agent_busy")

                now = _now()
                connection.execute(
                    "INSERT INTO requests "
                    "(request_id, device_id, input_hash, transcript, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                    (request_id, device_id, input_hash, transcript, now, now),
                )
                connection.execute("UPDATE requests SET context_json=? WHERE request_id=?",
                                   (context_json, request_id))
                row = connection.execute(
                    "SELECT * FROM requests WHERE request_id = ?", (request_id,)
                ).fetchone()
                connection.commit()
                return dict(row), True
            except Exception:
                connection.rollback()
                raise

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def queued_requests(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requests WHERE status = 'queued' ORDER BY created_at, request_id"
            ).fetchall()
            return [dict(row) for row in rows]

    def interrupt_running(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE requests SET status = 'interrupted', error_code = 'interrupted', "
                "updated_at = ? WHERE status = 'running'",
                (_now(),),
            )
            connection.commit()

    def mark_running(self, request_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE requests SET status = 'running', updated_at = ? "
                "WHERE request_id = ? AND status = 'queued'",
                (_now(), request_id),
            )
            return cursor.rowcount == 1

    def set_terminal(
        self,
        request_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        result_json = json.dumps(result, ensure_ascii=False, separators=(",", ":")) if result else None
        with self._connect() as connection:
            connection.execute(
                "UPDATE requests SET status = ?, result_json = ?, error_code = ?, updated_at = ? "
                "WHERE request_id = ? AND status IN ('queued', 'running')",
                (status, result_json, error_code, _now(), request_id),
            )

    def active_for_device(self, device_id: str) -> bool:
        with self._connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM requests WHERE device_id = ? "
                    "AND status IN ('queued', 'running') LIMIT 1",
                    (device_id,),
                ).fetchone()
                is not None
            )

    def get_session(self, device_id: str) -> tuple[int, str | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT generation, thread_id FROM device_sessions WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            return (int(row[0]), row[1]) if row is not None else None

    def save_thread(self, device_id: str, generation: int, thread_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO device_sessions(device_id, generation, thread_id) VALUES (?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET "
                "generation = excluded.generation, thread_id = excluded.thread_id",
                (device_id, generation, thread_id),
            )

    def reset_session(self, device_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT generation, thread_id FROM device_sessions WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO device_sessions(device_id, generation, thread_id) "
                    "VALUES (?, 1, NULL)",
                    (device_id,),
                )
            else:
                generation, thread_id = int(row[0]), row[1]
                if thread_id:
                    connection.execute(
                        "INSERT OR REPLACE INTO session_history "
                        "(device_id, generation, thread_id, closed_at) VALUES (?, ?, ?, ?)",
                        (device_id, generation, thread_id, _now()),
                    )
                connection.execute(
                    "UPDATE device_sessions SET generation = ?, thread_id = NULL "
                    "WHERE device_id = ?",
                    (generation + 1, device_id),
                )
            connection.commit()

    def session_history(self, device_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT thread_id FROM session_history WHERE device_id = ? ORDER BY generation",
                (device_id,),
            ).fetchall()
            return [str(row[0]) for row in rows]
