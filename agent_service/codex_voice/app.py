"""Loopback-only deployment entrypoint API for the Codex voice agent."""

from __future__ import annotations

import hmac
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .git_sync import GitSync
from .config import RuntimeConfig
from .runtime import CodexRuntime, RuntimeFailure
from .service import AgentReply, AgentRequest, AgentService, AgentServiceError


class TurnInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    transcript: str = Field(min_length=1, max_length=40000)
    knowledge_context: dict | None = None


class ResetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


def create_app(
    service: AgentService | None = None,
    *,
    token: str | None = None,
    database: str | Path | None = None,
) -> FastAPI:
    bearer = token if token is not None else os.environ.get("CODEX_AGENT_TOKEN", "")
    if not bearer:
        raise ValueError("CODEX_AGENT_TOKEN is required")
    if service is None:
        config = RuntimeConfig.from_env()
        db_path = database or os.environ.get(
            "CODEX_AGENT_DATABASE", "~/.local/state/hermes-echo/codex-voice/agent.sqlite3"
        )
        runtime = CodexRuntime(config)
        service = AgentService(Path(db_path).expanduser(), runtime)

    sync_path = os.environ.get("OBSIDIAN_SYNC_VAULT")
    git_sync = GitSync(Path(sync_path)) if sync_path else None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        app.state.startup_error = None
        try:
            await service.start()
        except RuntimeFailure as exc:
            app.state.startup_error = exc.code
        sync_task = asyncio.create_task(git_sync.run()) if git_sync else None
        try:
            yield
        finally:
            if sync_task:
                sync_task.cancel()
                await asyncio.gather(sync_task, return_exceptions=True)
            if app.state.startup_error is None:
                await service.close()

    app = FastAPI(title="Codex Voice Agent", version="1.0.0", lifespan=lifespan)
    app.state.agent_service = service
    app.state.startup_error = None

    @app.get("/health/live")
    async def live():
        return {"status": "ok", "service": "codex-voice-agent"}

    @app.get("/health/ready")
    async def ready():
        if app.state.startup_error:
            return {"status": "not_ready", "error": app.state.startup_error}
        return {"status": "ok", "provider": "codex"}

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied, bearer):
            raise HTTPException(status_code=401, detail={"code": "unauthorized"})

    auth = [Depends(authenticate)]

    def translate_error(error: AgentServiceError) -> HTTPException:
        return HTTPException(
            status_code=error.status_code,
            detail={"code": error.code},
        )

    def encode_snapshot(snapshot):
        return {
            "request_id": snapshot.request_id,
            "device_id": snapshot.device_id,
            "status": snapshot.status,
            "reply": _encode_reply(snapshot.reply),
            "error": (
                {
                    "code": snapshot.error.code,
                    "message": snapshot.error.message,
                    "retryable": snapshot.error.retryable,
                }
                if snapshot.error
                else None
            ),
        }

    @app.get("/v1/knowledge/git", dependencies=auth)
    async def git_status():
        return git_sync.last_result if git_sync else {"status": "disabled"}

    @app.post("/v1/agent/turns", status_code=202, dependencies=auth)
    async def submit_turn(body: TurnInput):
        try:
            snapshot = await service.submit(
                AgentRequest(body.request_id, body.device_id, body.transcript, body.knowledge_context)
            )
        except AgentServiceError as exc:
            raise translate_error(exc) from None
        return encode_snapshot(snapshot)

    @app.get("/v1/agent/turns/{request_id}", dependencies=auth)
    async def get_turn(request_id: str):
        try:
            return encode_snapshot(await service.get(request_id))
        except AgentServiceError as exc:
            raise translate_error(exc) from None

    @app.post("/v1/agent/turns/{request_id}/cancel", dependencies=auth)
    async def cancel_turn(request_id: str):
        try:
            return encode_snapshot(await service.cancel(request_id))
        except AgentServiceError as exc:
            raise translate_error(exc) from None

    @app.post("/v1/agent/sessions/reset", dependencies=auth)
    async def reset_session(body: ResetInput):
        try:
            await service.reset(body.device_id)
        except AgentServiceError as exc:
            raise translate_error(exc) from None
        return {"device_id": body.device_id, "status": "reset"}

    return app


def _encode_reply(reply: AgentReply | None):
    if reply is None:
        return None
    return {
        "reply": reply.reply,
        "note": reply.note,
        "thread_id": reply.thread_id,
        "model": reply.model,
        "provider": "codex",
    }
