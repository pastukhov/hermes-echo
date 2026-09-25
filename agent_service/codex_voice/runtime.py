"""Version-specific adapter around the pinned OpenAI Codex Python SDK."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openai_codex import ApprovalMode, AsyncCodex, Sandbox
from openai_codex.errors import InvalidRequestError

from .config import RuntimeConfig


logger = logging.getLogger(__name__)


_SAFE_FAILURES: dict[str, tuple[str, bool]] = {
    "agent_auth_required": ("Codex authentication is required", False),
    "agent_rate_limited": ("Codex account quota is unavailable", True),
    "agent_timeout": ("Codex turn timed out", True),
    "agent_unavailable": ("Codex runtime is unavailable", True),
    "agent_invalid_response": ("Codex returned no usable response", False),
    "agent_model_unavailable": ("Configured Codex model is unavailable", False),
    "permission_required": ("Codex tool request requires permission", False),
    "interrupted": ("Codex turn was interrupted", True),
}


class RuntimeFailure(Exception):
    """A stable, safe-to-return failure from the local Codex runtime."""

    def __init__(self, code: str) -> None:
        if code not in _SAFE_FAILURES:
            code = "agent_unavailable"
        self.code = code
        self.retryable = _SAFE_FAILURES[code][1]
        super().__init__(_SAFE_FAILURES[code][0])


@dataclass(slots=True)
class _ActiveTurn:
    handle: Any
    task: asyncio.Task[Any]


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _error_code(error: object | None) -> str | None:
    if error is None:
        return None
    info = getattr(error, "codex_error_info", None)
    root = getattr(info, "root", None)
    value = _enum_value(root) if root is not None else ""
    if value in {"unauthorized", "authenticationRequired"}:
        return "agent_auth_required"
    if value in {"usageLimitExceeded", "rateLimitExceeded"}:
        return "agent_rate_limited"
    if value in {"contextWindowExceeded", "serverOverloaded"}:
        return "agent_unavailable"
    return None


def _is_declined_permission(item: object) -> bool:
    payload = getattr(item, "root", item)
    item_type = _enum_value(getattr(payload, "type", ""))
    status = _enum_value(getattr(payload, "status", ""))
    return item_type in {"commandExecution", "fileChange", "permissions"} and status == "declined"


class CodexRuntime:
    """A long-lived SDK process, with read-only and no-auto-approval defaults."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or AsyncCodex
        self._codex: Any | None = None
        self._threads: dict[str, Any] = {}
        self._active: dict[str, _ActiveTurn] = {}
        self._model: str | None = None
        self._started = False

    @property
    def model(self) -> str | None:
        return self._model

    @property
    def provider(self) -> str:
        return "codex"

    @property
    def sdk_version(self) -> str:
        from openai_codex import __version__

        return __version__

    @property
    def runtime_version(self) -> str:
        from importlib.metadata import version

        return version("openai-codex-cli-bin")

    async def start(self) -> None:
        if self._started:
            return
        client = self._client_factory()
        self._codex = client
        try:
            await client.__aenter__()
            # `requires_openai_auth` describes the active provider's auth mode,
            # not whether this user has a valid ChatGPT session. Let Codex validate
            # the actual turn and classify its terminal auth error instead.
            catalog = await client.models()
            models = list(catalog.data)
            if self.config.model:
                selected = next(
                    (
                        item
                        for item in models
                        if self.config.model in {item.id, item.model}
                    ),
                    None,
                )
                if selected is None:
                    raise RuntimeFailure("agent_model_unavailable")
            else:
                selected = next((item for item in models if item.is_default), None)
                if selected is None:
                    raise RuntimeFailure("agent_model_unavailable")
            self._model = selected.model
            self._started = True
        except RuntimeFailure:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise self._classify_exception(exc) from None

    async def start_thread(self, *, ephemeral: bool = False) -> str:
        client = self._require_client()
        try:
            thread = await client.thread_start(
                model=self._model,
                cwd=self.config.cwd,
                ephemeral=ephemeral,
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
            )
        except Exception as exc:
            raise self._classify_exception(exc) from None
        thread_id = str(thread.id)
        self._threads[thread_id] = thread
        return thread_id

    async def resume_thread(self, thread_id: str) -> str:
        client = self._require_client()
        if thread_id in self._threads:
            return thread_id
        try:
            try:
                thread = await client.thread_resume(
                    thread_id,
                    model=self._model,
                    cwd=self.config.cwd,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                )
            except InvalidRequestError as exc:
                # Desktop Codex can own an idle thread's writer lock. Never
                # remove that lock: fork its history and persist the new ID.
                if exc.message != f"thread {thread_id} already has an active writer":
                    raise
                thread = await client.thread_fork(
                    thread_id,
                    model=self._model,
                    cwd=self.config.cwd,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                )
                logger.warning("Voice conversation forked after writer ownership conflict")
        except Exception as exc:
            raise self._classify_exception(exc) from None
        resolved_id = str(thread.id)
        self._threads[resolved_id] = thread
        return resolved_id

    async def run(self, thread_id: str, prompt: str) -> str:
        thread = self._threads.get(thread_id)
        if thread is None:
            thread_id = await self.resume_thread(thread_id)
            thread = self._threads[thread_id]

        try:
            handle = await thread.turn(
                prompt,
                model=self._model,
                cwd=self.config.cwd,
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
            )
        except Exception as exc:
            raise self._classify_exception(exc) from None

        task = asyncio.create_task(handle.run())
        self._active[thread_id] = _ActiveTurn(handle=handle, task=task)
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), timeout=self.config.turn_timeout_seconds
            )
        except TimeoutError:
            await self._stop_turn(thread_id, handle, task)
            raise RuntimeFailure("agent_timeout") from None
        except asyncio.CancelledError:
            await self._stop_turn(thread_id, handle, task)
            raise
        except Exception as exc:
            raise self._classify_exception(exc) from None
        finally:
            self._active.pop(thread_id, None)

        items = getattr(result, "items", None) or []
        if any(_is_declined_permission(item) for item in items):
            raise RuntimeFailure("permission_required")

        code = _error_code(getattr(result, "error", None))
        if code is not None:
            raise RuntimeFailure(code)
        status = _enum_value(getattr(result, "status", ""))
        if status == "interrupted":
            raise RuntimeFailure("interrupted")
        if status != "completed":
            raise RuntimeFailure("agent_unavailable")

        response = getattr(result, "final_response", None)
        if not isinstance(response, str) or not response.strip():
            raise RuntimeFailure("agent_invalid_response")
        return response.strip()

    async def interrupt(self, thread_id: str) -> None:
        active = self._active.get(thread_id)
        if active is None:
            return
        try:
            await active.handle.interrupt()
        except Exception as exc:
            raise self._classify_exception(exc) from None

    async def close(self) -> None:
        for thread_id, active in tuple(self._active.items()):
            await self._stop_turn(thread_id, active.handle, active.task)
        self._active.clear()
        client, self._codex = self._codex, None
        self._threads.clear()
        self._started = False
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                # Shutdown must not leak SDK internals into logs or user output.
                pass

    @staticmethod
    async def _stop_turn(thread_id: str, handle: Any, task: asyncio.Task[Any]) -> None:
        try:
            await handle.interrupt()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _require_client(self) -> Any:
        if not self._started or self._codex is None:
            raise RuntimeFailure("agent_unavailable")
        return self._codex

    @staticmethod
    def _classify_exception(exc: Exception) -> RuntimeFailure:
        name = type(exc).__name__.lower()
        detail = str(exc).lower()
        if "auth" in name or "unauthorized" in detail or "not logged in" in detail:
            return RuntimeFailure("agent_auth_required")
        if "rate" in name or "quota" in detail or "usage limit" in detail:
            return RuntimeFailure("agent_rate_limited")
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return RuntimeFailure("agent_timeout")
        return RuntimeFailure("agent_unavailable")
