"""Streaming HTTP endpoints for authenticated, asynchronous voice turns."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .auth import owns_device
from .store import JobConflict, VoiceJobStore
from .worker import VoiceJobWorker


def install_voice_job_routes(
    app: FastAPI,
    store: VoiceJobStore,
    worker: VoiceJobWorker,
    device_tokens: dict[str, str],
    *,
    reset_device: Callable[[str], object] | None = None,
) -> None:
    async def authenticate(request: Request) -> str:
        device_id = request.headers.get("X-Device-Id", "")
        if not device_id or not owns_device(
            device_id, request.headers.get("Authorization"), device_tokens
        ):
            raise HTTPException(status_code=401, detail={"error": "unauthorized"})
        return device_id

    def status_payload(job: dict) -> dict:
        payload = {
            "turn_id": job["turn_id"],
            "request_id": job["request_id"],
            "status": job["status"],
        }
        if job.get("error_code"):
            payload["error"] = job["error_code"]
        return payload

    @app.post("/api/v2/voice/turns", status_code=202)
    async def upload_turn(request: Request):
        device_id = await authenticate(request)
        raw_request_id = request.headers.get("X-Request-Id", "")
        try:
            request_id = str(uuid.UUID(raw_request_id))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail={"error": "invalid_request_id"}) from None
        try:
            is_new, job = store.claim_upload(device_id, request_id)
        except JobConflict as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from None

        stage_path = (
            Path(job["upload_path"])
            if is_new
            else store.staging / f"duplicate-{uuid.uuid4().hex}.part"
        )
        size = 0
        digest = hashlib.sha256()
        try:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > store.max_bytes:
                raise JobConflict("audio_too_large")
            with stage_path.open("wb") as output:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > store.max_bytes:
                        raise JobConflict("audio_too_large")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size == 0 or size % 2:
                raise JobConflict("audio_invalid")
            audio_hash = digest.hexdigest()
            if not is_new:
                store.verify_duplicate(device_id, request_id, audio_hash)
                old = store.get_by_request(device_id, request_id)
                return JSONResponse(status_code=202, content=status_payload(old))
            completed = store.complete_upload(
                device_id,
                request_id,
                stage_path=stage_path,
                size=size,
                digest=audio_hash,
            )
            try:
                await worker.enqueue(completed["turn_id"])
            except (RuntimeError, asyncio.QueueFull):
                # The durable queued row is recovered by the next worker start.
                pass
            return JSONResponse(status_code=202, content=status_payload(completed))
        except JobConflict as exc:
            stage_path.unlink(missing_ok=True)
            if is_new:
                store.mark_upload_failed(job["turn_id"], str(exc))
            status = 413 if str(exc) == "audio_too_large" else 400
            if str(exc) in {"upload_in_progress", "idempotency_conflict", "agent_busy"}:
                status = 429 if str(exc) == "agent_busy" else 409
            raise HTTPException(status_code=status, detail={"error": str(exc)}) from None
        except Exception:
            stage_path.unlink(missing_ok=True)
            if is_new:
                store.mark_upload_failed(job["turn_id"], "audio_receive_failed")
            raise

    @app.get("/api/v2/voice/requests/{request_id}")
    async def lookup_request(request: Request, request_id: str):
        device_id = await authenticate(request)
        try:
            request_id = str(uuid.UUID(request_id))
        except ValueError:
            raise HTTPException(status_code=404, detail={"error": "not_found"}) from None
        job = store.get_by_request(device_id, request_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        return status_payload(job)

    @app.get("/api/v2/voice/turns/{turn_id}")
    async def get_turn(request: Request, turn_id: str):
        device_id = await authenticate(request)
        try:
            turn_id = str(uuid.UUID(turn_id))
        except ValueError:
            raise HTTPException(status_code=404, detail={"error": "not_found"}) from None
        job = store.get_owned(device_id, turn_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        return status_payload(job)

    @app.get("/api/v2/voice/turns/{turn_id}/audio")
    async def get_audio(request: Request, turn_id: str):
        device_id = await authenticate(request)
        job = store.get_owned(device_id, turn_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        if job["status"] != "ready" or not job.get("result_json"):
            raise HTTPException(status_code=409, detail={"error": "audio_not_ready"})
        path = Path(job["result_json"]).resolve()
        if store.archive_root.resolve() not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        return FileResponse(path, media_type="audio/wav", filename="reply.wav")

    @app.post("/api/v2/voice/turns/{turn_id}/cancel")
    async def cancel_turn(request: Request, turn_id: str):
        device_id = await authenticate(request)
        try:
            turn_id = str(uuid.UUID(turn_id))
        except ValueError:
            raise HTTPException(status_code=404, detail={"error": "not_found"}) from None
        status = store.cancel(device_id, turn_id)
        if status is None:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        if status == "cancelled":
            await worker.cancel(turn_id)
        job = store.get_owned(device_id, turn_id)
        return status_payload(job)

    @app.post("/api/v2/voice/sessions/reset")
    async def reset_session(request: Request):
        device_id = await authenticate(request)
        if reset_device is None:
            raise HTTPException(status_code=503, detail={"error": "agent_reset_unavailable"})
        result = reset_device(device_id)
        if hasattr(result, "__await__"):
            await result
        return {"device_id": device_id, "status": "reset"}
