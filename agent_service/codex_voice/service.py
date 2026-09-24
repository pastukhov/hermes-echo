"""Durable, per-device conversation and request orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runtime import RuntimeFailure
from .store import SQLiteAgentStore, StoreConflict


@dataclass(frozen=True, slots=True)
class AgentRequest:
    request_id: str
    device_id: str
    transcript: str


@dataclass(frozen=True, slots=True)
class AgentReply:
    reply: str
    note: dict[str, Any] | None
    thread_id: str
    model: str | None


@dataclass(frozen=True, slots=True)
class AgentFailure:
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    request_id: str
    device_id: str
    status: str
    reply: AgentReply | None = None
    error: AgentFailure | None = None


class AgentServiceError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


_FAILURES = {
    "agent_auth_required": ("Codex authentication is required", False),
    "agent_rate_limited": ("Codex account quota is unavailable", True),
    "agent_timeout": ("Codex turn timed out", True),
    "agent_unavailable": ("Codex runtime is unavailable", True),
    "agent_invalid_response": ("Codex returned no usable response", False),
    "agent_model_unavailable": ("Configured Codex model is unavailable", False),
    "permission_required": ("Codex tool request requires permission", False),
    "interrupted": ("Codex turn was interrupted", True),
}


class AgentService:
    def __init__(
        self,
        database: str | Path,
        runtime: Any,
        *,
        max_workers: int = 2,
        max_queue: int = 8,
    ) -> None:
        if max_workers < 1 or max_queue < 1:
            raise ValueError("worker and queue limits must be positive")
        self.store = SQLiteAgentStore(database)
        self.runtime = runtime
        self.max_workers = max_workers
        self.max_queue = max_queue
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max_queue)
        self._workers: list[asyncio.Task[None]] = []
        self._accept_lock = asyncio.Lock()
        self._events: dict[str, asyncio.Event] = {}
        self._started = False
        self._closing = False

    async def start(self) -> None:
        if self._started:
            return
        self.store.initialize()
        await self.runtime.start()
        self.store.interrupt_running()
        for row in self.store.queued_requests():
            self._queue.put_nowait(row["request_id"])
        self._closing = False
        self._workers = [
            asyncio.create_task(self._worker(), name=f"codex-agent-worker-{index}")
            for index in range(self.max_workers)
        ]
        self._started = True

    async def close(self) -> None:
        if not self._started:
            return
        self._closing = True
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self.store.interrupt_running()
        await self.runtime.close()
        self._started = False

    async def submit(self, request: AgentRequest) -> JobSnapshot:
        if not self._started or self._closing:
            raise AgentServiceError("agent_unavailable", 503)
        if (
            not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request.request_id)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request.device_id)
            or not request.transcript.strip()
        ):
            raise AgentServiceError("invalid_request", 422)
        digest = hashlib.sha256(request.transcript.encode("utf-8")).hexdigest()
        async with self._accept_lock:
            try:
                row, created = self.store.accept_request(
                    request.request_id,
                    request.device_id,
                    digest,
                    request.transcript,
                    self.max_queue,
                )
            except StoreConflict as exc:
                code = "device_busy" if exc.code == "device_busy" else exc.code
                status = 429 if code == "agent_busy" else 409
                raise AgentServiceError(code, status) from None
            if created:
                self._events.setdefault(request.request_id, asyncio.Event())
                self._queue.put_nowait(request.request_id)
            return self._snapshot(row)

    async def get(self, request_id: str) -> JobSnapshot:
        row = self.store.get_request(request_id)
        if row is None:
            raise AgentServiceError("request_not_found", 404)
        return self._snapshot(row)

    async def wait(self, request_id: str, *, timeout: float = 5.0) -> JobSnapshot:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            snapshot = await self.get(request_id)
            if snapshot.status not in {"queued", "running"}:
                return snapshot
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return snapshot
            event = self._events.setdefault(request_id, asyncio.Event())
            try:
                await asyncio.wait_for(event.wait(), min(remaining, 0.05))
            except TimeoutError:
                pass
            event.clear()

    async def cancel(self, request_id: str) -> JobSnapshot:
        row = self.store.get_request(request_id)
        if row is None:
            raise AgentServiceError("request_not_found", 404)
        if row["status"] in {"queued", "running"}:
            session = self.store.get_session(row["device_id"])
            if row["status"] == "running" and session and session[1]:
                try:
                    await self.runtime.interrupt(session[1])
                except Exception:
                    pass
            self.store.set_terminal(request_id, "cancelled", error_code="interrupted")
            self._signal(request_id)
        return await self.get(request_id)

    async def reset(self, device_id: str) -> None:
        async with self._accept_lock:
            if self.store.active_for_device(device_id):
                raise AgentServiceError("device_busy", 409)
            self.store.reset_session(device_id)

    def session_for(self, device_id: str) -> str | None:
        session = self.store.get_session(device_id)
        return session[1] if session else None

    def session_history_for(self, device_id: str) -> list[str]:
        return self.store.session_history(device_id)

    async def _worker(self) -> None:
        while True:
            request_id = await self._queue.get()
            try:
                if not self.store.mark_running(request_id):
                    continue
                row = self.store.get_request(request_id)
                if row is None:
                    continue
                await self._execute(row)
            except asyncio.CancelledError:
                self.store.set_terminal(request_id, "interrupted", error_code="interrupted")
                self._signal(request_id)
                raise
            except Exception:
                self.store.set_terminal(
                    request_id, "failed", error_code="agent_unavailable"
                )
                self._signal(request_id)
            finally:
                self._queue.task_done()

    async def _execute(self, row: dict[str, Any]) -> None:
        request_id, device_id = row["request_id"], row["device_id"]
        session = self.store.get_session(device_id)
        try:
            if session is None:
                generation = 1
                thread_id = await self.runtime.start_thread()
                self.store.save_thread(device_id, generation, thread_id)
            elif session[1] is None:
                generation = session[0]
                thread_id = await self.runtime.start_thread()
                self.store.save_thread(device_id, generation, thread_id)
            else:
                thread_id = await self.runtime.resume_thread(session[1])
                self.store.save_thread(device_id, session[0], thread_id)
            prompt = (
                "Ты голосовой помощник. Ответь кратко и по-русски. Верни только JSON-объект "
                'формата {"reply":"...","note":{"create":false,"title":"",'
                '"content":"","tags":[]}}. Не выполняй запросы, требующие разрешения.\n'
                "Фраза пользователя: "
                + json.dumps(row["transcript"], ensure_ascii=False)
            )
            response = await self.runtime.run(thread_id, prompt)
            payload = json.loads(response)
            if not isinstance(payload, dict) or not isinstance(payload.get("reply"), str):
                raise RuntimeFailure("agent_invalid_response")
            note = payload.get("note")
            if note is not None:
                if (
                    not isinstance(note, dict)
                    or not isinstance(note.get("create", False), bool)
                    or not isinstance(note.get("title", ""), str)
                    or not isinstance(note.get("content", ""), str)
                    or not isinstance(note.get("tags", []), list)
                    or not all(isinstance(tag, str) for tag in note.get("tags", []))
                ):
                    raise RuntimeFailure("agent_invalid_response")
                note = {
                    "create": note.get("create", False),
                    "title": str(note.get("title", "")),
                    "content": str(note.get("content", "")),
                    "tags": [str(tag) for tag in note.get("tags", [])]
                    if isinstance(note.get("tags", []), list)
                    else [],
                }
            if not payload["reply"].strip():
                raise RuntimeFailure("agent_invalid_response")
            reply = AgentReply(
                reply=payload["reply"].strip(),
                note=note,
                thread_id=thread_id,
                model=getattr(self.runtime, "model", None),
            )
            self.store.set_terminal(request_id, "completed", result=asdict(reply))
        except RuntimeFailure as exc:
            self.store.set_terminal(request_id, "failed", error_code=exc.code)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.store.set_terminal(request_id, "failed", error_code="agent_unavailable")
        finally:
            self._signal(request_id)

    def _signal(self, request_id: str) -> None:
        self._events.setdefault(request_id, asyncio.Event()).set()

    @staticmethod
    def _snapshot(row: dict[str, Any]) -> JobSnapshot:
        reply: AgentReply | None = None
        if row.get("result_json"):
            try:
                payload = json.loads(row["result_json"])
                reply = AgentReply(**payload)
            except (TypeError, ValueError):
                reply = None
        error = None
        code = row.get("error_code")
        if code:
            message, retryable = _FAILURES.get(
                code, ("Codex runtime is unavailable", True)
            )
            error = AgentFailure(code, message, retryable)
        return JobSnapshot(
            request_id=row["request_id"],
            device_id=row["device_id"],
            status=row["status"],
            reply=reply,
            error=error,
        )
