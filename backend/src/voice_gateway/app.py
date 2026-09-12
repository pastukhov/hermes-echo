"""Voice Gateway FastAPI app — Milestone 2 streaming ingest (ТЗ §11, §12, §17, §18).

Exposes ``POST /api/v1/voice/turn``: the ATOM streams raw PCM S16LE in a
chunked HTTP body while the backend writes it straight to the turn's
``input.pcm`` (never buffered in RAM, ТЗ §17.2). On a clean end-of-stream the
PCM is wrapped into ``archive/YYYY/MM/DD/<turn-id>/input.wav`` (ТЗ §17.3,
§18) and the endpoint answers ``200 OK`` with the ``X-Turn-Id`` header.
The WAV body itself appears in Milestone 6; per the task spec this milestone
answers with a plain 200 and no audio body.

A disconnect before the stream end (or any other failure) is recorded in the
turn's ``metadata.json`` — ``status: audio_receive_failed`` (ТЗ §32, §19:
metadata is always saved) — without attempting to answer a client that is
already gone.
"""
from __future__ import annotations

import asyncio
import os
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from backend.src.voice_gateway.archive import ArchiveStore
from backend.src.voice_gateway.metrics import init_metrics
from backend.common.error_codes import ErrorCode
from prometheus_client import generate_latest

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_DEVICE_ID = "atom-echo-01"
_STREAM_CHUNK = 1024 * 1024


def _ms(total_seconds: float) -> int:
    return int(round(total_seconds * 1000))


def pcm_to_wav(pcm_path: Path, wav_path: Path, sample_rate: int, channels: int) -> None:
    """Wrap a raw PCM S16LE file in a minimal WAV header.

    Streams in 1 MiB chunks — the full body is never resident in RAM
    (ТЗ §17.2). The 44-byte header is written with placeholder sizes, the
    PCM data is copied chunk-wise, then the sizes are patched in place.
    """
    pcm_bytes = pcm_path.stat().st_size
    if pcm_bytes % 2 != 0:
        raise ValueError("raw PCM S16LE must be an even number of bytes")
    data_size = pcm_bytes
    riff_size = 36 + data_size
    with open(pcm_path, "rb") as src, open(wav_path, "wb") as dst:
        dst.write(b"RIFF" + struct.pack("<I", riff_size) + b"WAVEfmt ")
        dst.write(struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                              sample_rate * channels * 2, channels * 2, 16))
        dst.write(b"data" + struct.pack("<I", data_size))
        while True:
            chunk = src.read(_STREAM_CHUNK)
            if not chunk:
                break
            dst.write(chunk)


def create_app(archive_root: str | os.PathLike | None = None) -> FastAPI:
    """Build the gateway app. ``archive_root`` defaults to ``$ARCHIVE_ROOT``
    or ``./archive`` (tests pass a tmp dir)."""
    root = Path(os.environ.get("ARCHIVE_ROOT", "archive")) if archive_root is None \
        else Path(archive_root)

    app = FastAPI(title="Hermes Voice Gateway", version="0.2.0")

    # One metric namespace per app instance (ТЗ §34) so tests can use
    # isolated registries and concurrent apps never share counters.
    metrics = init_metrics()

    # All archive access goes through the one ArchiveStore (M2-05): it owns
    # the turn-directory layout (ТЗ §18) and the atomic metadata.json write.
    store = ArchiveStore(root)

    @app.get("/health")
    async def health() -> dict:
        """Liveness/readiness probe (ТЗ §15.1)."""
        return {
            "status": "ok",
            "service": "voice-gateway",
            "version": app.version,
        }

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        """Prometheus scrape endpoint (ТЗ §34)."""
        payload = generate_latest(metrics.registry)
        return Response(content=payload, media_type="text/plain; version=0.0.4")

    @app.post("/api/v1/voice/turn")
    async def voice_turn(request: Request) -> Response:
        """Ingest one voice turn (ТЗ §11).

        Streams the chunked PCM body to ``<turn>/input.pcm`` on disk, then
        finalizes ``input.wav`` (ТЗ §17.3) and the turn's
        ``metadata.json`` (ТЗ §19).
        """
        turn_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        device_id = request.headers.get("X-Device-Id", DEFAULT_DEVICE_ID)
        try:
            sample_rate = int(request.headers.get("X-Sample-Rate", DEFAULT_SAMPLE_RATE))
            channels = int(request.headers.get("X-Channels", DEFAULT_CHANNELS))
        except ValueError:
            sample_rate, channels = DEFAULT_SAMPLE_RATE, DEFAULT_CHANNELS

        # ТЗ §18: archive/YYYY/MM/DD/<turn-id>/ — owned by ArchiveStore.
        # UTC date, so the partition is deterministic for a given instant.
        turn_dir = store.turn_dir(turn_id, day=datetime.now(timezone.utc).date())
        turn_dir.mkdir(parents=True, exist_ok=True)
        pcm_path = turn_dir / "input.pcm"
        wav_path = turn_dir / "input.wav"

        def save_status(status: str, error: str | None = None,
                        input_bytes: int | None = None,
                        audio_duration_ms: int | None = None) -> None:
            payload: dict = {
                "turn_id": turn_id,
                "device_id": device_id,
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "input_bytes": input_bytes,
                "status": status,
            }
            if error:
                payload["error"] = error
            if audio_duration_ms is not None:
                payload["audio_duration_ms"] = audio_duration_ms
            store.save_metadata(turn_id, payload)

        try:
            with open(pcm_path, "wb") as pcm_file:
                async for chunk in request.stream():
                    pcm_file.write(chunk)
        except (asyncio.CancelledError, anyio.ClosedResourceError, anyio.EndOfStream,
                OSError):
            # Client disconnected (or the stream broke) before EOF — ТЗ §32.
            # The client is gone: record the failure, never try to answer.
            save_status(ErrorCode.AUDIO_RECEIVE_FAILED.value, "client disconnected before EOF")
            raise
        except Exception as exc:  # noqa: BLE001 — any other stream failure
            save_status(ErrorCode.INTERNAL_ERROR.value, f"stream error: {exc}")
            raise

        try:
            pcm_to_wav(pcm_path, wav_path, sample_rate, channels)
        except Exception:
            save_status(ErrorCode.INTERNAL_ERROR.value, "wav finalization failed")
            raise HTTPException(status_code=500, detail=str(ErrorCode.INTERNAL_ERROR))

        input_bytes = pcm_path.stat().st_size
        bytes_per_second = sample_rate * channels * 2
        audio_duration_ms = input_bytes * 1000 // bytes_per_second if bytes_per_second else None
        save_status("success", None, input_bytes, audio_duration_ms)

        # ТЗ §12: 200 OK + X-Turn-Id. The WAV *body* arrives in Milestone 6;
        # this milestone answers plain 200 with no audio body (task spec).
        return Response(status_code=200, media_type="audio/wav",
                        headers={"X-Turn-Id": turn_id})

    return app


app = create_app()
