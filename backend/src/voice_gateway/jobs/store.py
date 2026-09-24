"""Durable ownership, deduplication and upload metadata for voice turns."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator


class JobConflict(Exception):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class VoiceJobStore:
    def __init__(
        self,
        database: str | Path,
        archive_root: str | Path,
        *,
        max_bytes: int = 3_840_000,
        max_queue: int = 8,
    ) -> None:
        self.database = Path(database)
        self.archive_root = Path(archive_root)
        self.max_bytes = max_bytes
        self.max_queue = max_queue
        self.staging = self.archive_root / ".uploads"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        os.chmod(self.database, 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.staging.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS voice_jobs (
                    turn_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    upload_path TEXT NOT NULL,
                    audio_path TEXT,
                    audio_bytes INTEGER,
                    audio_sha256 TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(device_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS voice_jobs_status_created
                    ON voice_jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS voice_jobs_device_status
                    ON voice_jobs(device_id, status);
                """
            )

    def claim_upload(self, device_id: str, request_id: str) -> tuple[bool, dict]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM voice_jobs WHERE device_id = ? AND request_id = ?",
                (device_id, request_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["status"] == "upload_failed"
                    and existing["audio_sha256"] is None
                ):
                    stage_path = self.staging / f"{uuid.uuid4().hex}.part"
                    connection.execute(
                        "UPDATE voice_jobs SET status = 'uploading', upload_path = ?, "
                        "error_code = NULL, updated_at = ? WHERE turn_id = ?",
                        (str(stage_path), _now(), existing["turn_id"]),
                    )
                    row = connection.execute(
                        "SELECT * FROM voice_jobs WHERE turn_id = ?",
                        (existing["turn_id"],),
                    ).fetchone()
                    connection.commit()
                    return True, dict(row)
                connection.commit()
                if existing["status"] == "uploading":
                    raise JobConflict("upload_in_progress")
                return False, dict(existing)
            turn_id = str(uuid.uuid4())
            stage_path = self.staging / f"{uuid.uuid4().hex}.part"
            now = _now()
            connection.execute(
                "INSERT INTO voice_jobs "
                "(turn_id, device_id, request_id, status, upload_path, created_at, updated_at) "
                "VALUES (?, ?, ?, 'uploading', ?, ?, ?)",
                (turn_id, device_id, request_id, str(stage_path), now, now),
            )
            row = connection.execute(
                "SELECT * FROM voice_jobs WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            connection.commit()
            return True, dict(row)

    def complete_upload(
        self,
        device_id: str,
        request_id: str,
        *,
        stage_path: str | Path,
        size: int,
        digest: str,
    ) -> dict:
        if size <= 0 or size > self.max_bytes or size % 2:
            raise JobConflict("audio_invalid" if size <= self.max_bytes else "audio_too_large")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM voice_jobs WHERE device_id = ? AND request_id = ?",
                (device_id, request_id),
            ).fetchone()
            if row is None or row["status"] != "uploading":
                connection.rollback()
                raise JobConflict("upload_not_owned")
            queued = connection.execute(
                "SELECT COUNT(*) FROM voice_jobs WHERE status = 'queued'"
            ).fetchone()[0]
            if queued >= self.max_queue:
                connection.rollback()
                raise JobConflict("agent_busy")
            day = datetime.now(UTC)
            turn_dir = (
                self.archive_root
                / f"{day.year:04d}"
                / f"{day.month:02d}"
                / f"{day.day:02d}"
                / row["turn_id"]
            )
            turn_dir.mkdir(parents=True, exist_ok=True)
            audio_path = turn_dir / "input.pcm"
            os.replace(stage_path, audio_path)
            connection.execute(
                "UPDATE voice_jobs SET status = 'queued', audio_path = ?, audio_bytes = ?, "
                "audio_sha256 = ?, updated_at = ? WHERE turn_id = ?",
                (str(audio_path), size, digest, _now(), row["turn_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM voice_jobs WHERE turn_id = ?", (row["turn_id"],)
            ).fetchone()
            connection.commit()
            return dict(updated)

    def verify_duplicate(self, device_id: str, request_id: str, digest: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT turn_id, audio_sha256 FROM voice_jobs "
                "WHERE device_id = ? AND request_id = ?",
                (device_id, request_id),
            ).fetchone()
        if row is None:
            raise JobConflict("request_not_found")
        if row["audio_sha256"] != digest:
            raise JobConflict("idempotency_conflict")
        return str(row["turn_id"])

    def mark_upload_failed(self, turn_id: str, code: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE voice_jobs SET status = 'upload_failed', error_code = ?, updated_at = ? "
                "WHERE turn_id = ? AND status = 'uploading'",
                (code, _now(), turn_id),
            )

    def queued(self) -> list[dict]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM voice_jobs WHERE status = 'queued' ORDER BY created_at"
                ).fetchall()
            ]

    def mark_running(self, turn_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE voice_jobs SET status = 'running', updated_at = ? "
                "WHERE turn_id = ? AND status = 'queued'",
                (_now(), turn_id),
            )
            return cursor.rowcount == 1

    def set_progress(self, turn_id: str, status: str) -> bool:
        if status not in {"transcribing", "thinking", "synthesizing"}:
            raise ValueError("invalid voice job progress status")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE voice_jobs SET status = ?, updated_at = ? "
                "WHERE turn_id = ? AND status IN "
                "('running', 'transcribing', 'thinking', 'synthesizing')",
                (status, _now(), turn_id),
            )
            return cursor.rowcount == 1

    def recover_after_restart(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE voice_jobs SET status = 'interrupted', error_code = 'interrupted', "
                "updated_at = ? WHERE status IN "
                "('running', 'transcribing', 'thinking', 'synthesizing')",
                (_now(),),
            )

    def recover_uploads(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT turn_id, upload_path FROM voice_jobs WHERE status = 'uploading'"
            ).fetchall()
            connection.execute(
                "UPDATE voice_jobs SET status = 'upload_failed', "
                "error_code = 'audio_receive_failed', updated_at = ? "
                "WHERE status = 'uploading'",
                (_now(),),
            )
        for row in rows:
            Path(row["upload_path"]).unlink(missing_ok=True)

    def get(self, turn_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM voice_jobs WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_by_request(self, device_id: str, request_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM voice_jobs WHERE device_id = ? AND request_id = ?",
                (device_id, request_id),
            ).fetchone()
            return dict(row) if row else None

    def get_owned(self, device_id: str, turn_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM voice_jobs WHERE device_id = ? AND turn_id = ?",
                (device_id, turn_id),
            ).fetchone()
            return dict(row) if row else None

    def finish(self, turn_id: str, status: str, *, result: str | None = None,
               error_code: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE voice_jobs SET status = ?, result_json = ?, error_code = ?, "
                "updated_at = ? WHERE turn_id = ? AND status IN "
                "('running', 'transcribing', 'thinking', 'synthesizing')",
                (status, result, error_code, _now(), turn_id),
            )

    def cancel(self, device_id: str, turn_id: str) -> str | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM voice_jobs WHERE device_id = ? AND turn_id = ?",
                (device_id, turn_id),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            status = str(row[0])
            if status in {"queued", "running", "transcribing", "thinking", "synthesizing"}:
                connection.execute(
                    "UPDATE voice_jobs SET status = 'cancelled', error_code = 'interrupted', "
                    "updated_at = ? WHERE device_id = ? AND turn_id = ?",
                    (_now(), device_id, turn_id),
                )
                status = "cancelled"
            connection.commit()
            return status

    @staticmethod
    def hash_file(path: str | Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as stream:
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()
