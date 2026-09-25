"""Bounded asyncio worker for durable v2 voice turn jobs."""

from __future__ import annotations

import asyncio
import wave
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .store import VoiceJobStore
from backend.src.voice_gateway.archive import atomic_write_json


Progress = Callable[[str], None]
Processor = Callable[[dict[str, Any], Progress], Awaitable[str | Path]]


class VoiceJobWorker:
    def __init__(
        self,
        store: VoiceJobStore,
        processor: Processor,
        *,
        max_queue: int = 8,
    ) -> None:
        if max_queue < 1:
            raise ValueError("max_queue must be positive")
        self.store = store
        self.processor = processor
        self.max_queue = max_queue
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max_queue)
        self._tasks: list[asyncio.Task] = []
        self._active: dict[str, asyncio.Task] = {}
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self.store.initialize()
        self.store.recover_uploads()
        self.store.recover_after_restart()
        for job in self.store.queued():
            self.queue.put_nowait(job["turn_id"])
        self._tasks = [asyncio.create_task(self._run(), name="voice-job-0")]
        self._started = True

    async def close(self) -> None:
        if not self._started:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.store.recover_after_restart()
        self._started = False

    async def enqueue(self, turn_id: str) -> None:
        if not self._started:
            raise RuntimeError("voice job worker is not started")
        self.queue.put_nowait(turn_id)

    async def cancel(self, turn_id: str) -> None:
        task = self._active.get(turn_id)
        if task is not None:
            task.cancel()

    async def _run(self) -> None:
        while True:
            turn_id = await self.queue.get()
            try:
                if not self.store.mark_running(turn_id):
                    continue
                job = self.store.get(turn_id)
                if job is None:
                    continue
                current = asyncio.create_task(
                    self.processor(
                        job,
                        lambda status: self.store.set_progress(turn_id, status),
                    )
                )
                self._active[turn_id] = current
                try:
                    output_path = Path(await current)
                    if not self._valid_reply_wav(output_path):
                        self._archive_failure(job, "tts_failed")
                        self.store.finish(turn_id, "failed", error_code="tts_failed")
                    else:
                        self.store.finish(turn_id, "ready", result=str(output_path))
                except asyncio.CancelledError:
                    self._archive_failure(job, "interrupted")
                    self.store.finish(turn_id, "interrupted", error_code="interrupted")
                    # Cancelling a child turn must not retire the queue consumer.
                    # Propagate only cancellation of the worker itself (shutdown).
                    if asyncio.current_task().cancelling():
                        raise
                except Exception as exc:
                    code = getattr(exc, "code", "agent_unavailable")
                    self._archive_failure(job, code)
                    self.store.finish(turn_id, "failed", error_code=code)
                finally:
                    self._active.pop(turn_id, None)
            except asyncio.CancelledError:
                # A cancelled queued request is already terminal in the store.
                if self.store.get(turn_id) and self.store.get(turn_id)["status"] == "running":
                    self.store.finish(turn_id, "interrupted", error_code="interrupted")
                raise
            finally:
                self.queue.task_done()

    @staticmethod
    def _valid_reply_wav(path: Path) -> bool:
        try:
            with wave.open(str(path), "rb") as wav:
                return (
                    path.stat().st_size >= 44
                    and wav.getnchannels() == 1
                    and wav.getsampwidth() == 2
                    and wav.getframerate() == 24000
                    and wav.getnframes() > 0
                )
        except (OSError, wave.Error, EOFError):
            return False

    @staticmethod
    def _archive_failure(job: dict[str, Any], code: str) -> None:
        try:
            atomic_write_json(
                Path(job["audio_path"]).parent / "metadata.json",
                {
                    "turn_id": job["turn_id"],
                    "device_id": job["device_id"],
                    "started_at": job["created_at"],
                    "status": code,
                    "input_bytes": job.get("audio_bytes"),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )
        except OSError:
            pass
