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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest

from backend.common.error_codes import ErrorCode
from backend.src.voice_gateway.health import check_live, check_ready
from backend.src.voice_gateway.archive import (
    ArchiveStore,
    atomic_write_bytes,
    atomic_write_json,
)
from backend.src.voice_gateway.config import STTConfig, STTConfigError
from backend.src.voice_gateway.hermes.base import HermesClient
from backend.src.voice_gateway.hermes.stage import (
    HermesStage,
    HermesStageError,
    last_raw_response,
)
from backend.src.voice_gateway.metrics import init_metrics
from backend.src.voice_gateway.stt.base import STTProvider
from backend.src.voice_gateway.stt.client import OpenAICompatibleSTT

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_DEVICE_ID = "atom-echo-01"
_STREAM_CHUNK = 1024 * 1024

#: ТЗ §24: safe fallback reply spoken/archived when the Hermes stage fails.
FALLBACK_REPLY = "Не удалось обработать ответ."


def _ms(total_seconds: float) -> int:
    return int(round(total_seconds * 1000))


def _default_stt_provider() -> STTProvider | None:
    """Build the production STTProvider from environment config (ТЗ §20).

    Returns ``None`` when STT is not configured (``STT_BASE_URL`` unset) so
    the caller can fall back to this milestone's ingest-only contract — the
    same behavior as before STT was wired in. Only ever consulted when the
    caller did not explicitly inject a provider (tests always pass one
    explicitly, so this path only matters for the production singleton).
    """
    try:
        config = STTConfig.from_env()
    except STTConfigError:
        return None
    return OpenAICompatibleSTT(config)


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


def create_app(
    archive_root: str | os.PathLike | None = None,
    stt: STTProvider | None = None,
    hermes: HermesClient | None = None,
) -> FastAPI:
    """Build the gateway app.

    ``archive_root`` defaults to ``$ARCHIVE_ROOT`` or ``./archive`` (tests
    pass a tmp dir). ``stt`` / ``hermes`` are optional provider
    dependencies injected by the caller (tests pass fakes explicitly). When
    ``stt`` is left ``None`` the production STTProvider is built from
    environment config (ТЗ §20, ``_default_stt_provider()``); when neither
    an env-configured endpoint nor an injected fake is available, STT is
    simply not run. ``hermes`` has no such env-based default (wiring it in
    is a separate card's concern) — Hermes only runs when explicitly
    injected. When STT is unavailable the app still boots and a plain audio
    turn still succeeds with 200 + X-Turn-Id — this milestone's ingest-only
    contract ("backend может ответить простым 200 OK без аудио-тела [без
    STT/Hermes/TTS]") is preserved.
    """
    root = Path(os.environ.get("ARCHIVE_ROOT", "archive")) if archive_root is None \
        else Path(archive_root)

    # One metric namespace per app instance (ТЗ §34) so tests can use
    # isolated registries and concurrent apps never share counters.
    metrics = init_metrics()

    # All archive access goes through the one ArchiveStore (M2-05): it owns
    # the turn-directory layout (ТЗ §18) and the atomic metadata.json write.
    store = ArchiveStore(root)

    # STT provider: explicit injection wins; otherwise fall back to the
    # env-configured production provider (ТЗ §20). Tests always inject a
    # fake explicitly, so this fallback only matters for the production
    # singleton at the bottom of this module.
    stt_provider = stt if stt is not None else _default_stt_provider()

    # Hermes stage: the single-call policy (ТЗ §21–24) lives here. The
    # endpoint wraps the injected client once, per app instance; concurrent
    # turns share the stage safely because the raw payload travels through
    # a task-local ContextVar, not an instance attribute.
    hermes_stage = HermesStage(hermes) if hermes is not None else None

    app = FastAPI(title="Hermes Voice Gateway", version="0.2.0")

    @app.get("/health")
    async def health() -> dict:
        """Liveness/readiness probe (ТЗ §15.1)."""
        return {
            "status": "ok",
            "service": "voice-gateway",
            "version": app.version,
        }

    @app.get("/health/live")
    async def health_live() -> dict:
        """Liveness probe (ТЗ §35): process alive, nothing else."""
        return check_live()

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        """Readiness probe (ТЗ §35): config loaded + archive writable.

        Readiness must not depend on the momentary availability of
        Hermes/STT/TTS (ТЗ §35) — no network calls here, ever, so a
        hung provider cannot create a restart loop. The notes vault is
        probed and reported but never blocking (writable/optional, ТЗ
        §25).
        """
        report = check_ready(
            archive_root=root,
            note_root=os.environ.get("OBSIDIAN_VAULT_PATH"),
            env=os.environ,
        )
        return JSONResponse(
            status_code=200 if report.ready else 503,
            content=report.as_dict(),
        )

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
        # Captured ONCE and threaded through every save_metadata() call
        # below: ArchiveStore.turn_dir() defaults to the LOCAL date when no
        # ``day`` is given, so if a turn straddles local midnight while
        # running under a non-UTC TZ (e.g. MSK, UTC+3) the two dates
        # diverge and metadata.json would be written into a different
        # day-directory than input.pcm/input.wav — never happens as long as
        # every write reuses this same UTC day.
        turn_day = datetime.now(timezone.utc).date()
        turn_dir = store.turn_dir(turn_id, day=turn_day)
        turn_dir.mkdir(parents=True, exist_ok=True)
        pcm_path = turn_dir / "input.pcm"
        wav_path = turn_dir / "input.wav"

        def save_status(status: str, error: str | None = None,
                        input_bytes: int | None = None,
                        audio_duration_ms: int | None = None,
                        extra: dict | None = None) -> None:
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
            if extra:
                payload.update(extra)
            store.save_metadata(turn_id, payload, day=turn_day)

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

        # Terminal-failure helper (ТЗ §13/§32): persist the failure metadata
        # atomically BEFORE answering, then return the locked 502 JSON shape
        # ``{"error": <status>, "turn_id": <id>}`` — the diagnostic text of
        # ``metadata.error`` is never echoed to the client.
        def fail_turn(status: str, error: str,
                      input_bytes_: int | None = None,
                      audio_duration_ms_: int | None = None,
                      extra: dict | None = None) -> JSONResponse:
            """Persist the terminal failure and answer 502 JSON (ТЗ §13/§32)."""
            save_status(status, error, input_bytes_, audio_duration_ms_, extra=extra)
            metrics.turns_total.labels(status=status).inc()
            return JSONResponse(
                status_code=502,
                content={"error": status, "turn_id": turn_id},
                media_type="application/json",
            )

        input_bytes = pcm_path.stat().st_size

        # ------------------------------------------------------------------
        # ТЗ §32: deterministic validation of the raw PCM S16LE contract
        # (ТЗ §11) BEFORE WAV finalization. Invalid audio is a *known*
        # error — it gets its own bounded code, never degrades to
        # internal_error (design rule: known errors must not fall back to
        # internal_error). The client is still present (the body completed),
        # so it gets the standard 502 JSON answer; metadata.json is saved
        # first with all already-known turn fields.
        # ------------------------------------------------------------------
        if input_bytes == 0:
            return fail_turn(ErrorCode.AUDIO_INVALID.value, "empty audio body",
                             input_bytes, None)
        if input_bytes % 2 != 0:
            return fail_turn(
                ErrorCode.AUDIO_INVALID.value,
                f"odd byte count ({input_bytes}): not valid PCM S16LE",
                input_bytes, None)

        try:
            pcm_to_wav(pcm_path, wav_path, sample_rate, channels)
        except Exception:
            save_status(ErrorCode.INTERNAL_ERROR.value, "wav finalization failed")
            raise HTTPException(status_code=500, detail=str(ErrorCode.INTERNAL_ERROR))

        bytes_per_second = sample_rate * channels * 2
        audio_duration_ms = input_bytes * 1000 // bytes_per_second if bytes_per_second else None

        # ------------------------------------------------------------------
        # STT → Hermes pipeline (ТЗ §20–§24). Runs immediately after WAV
        # finalization, before the success metadata is persisted — but only
        # when the pipeline is actually wired in (``stt``/``hermes`` were
        # injected). This milestone (M2) only guarantees ingest + archive;
        # the STT/Hermes integration is a separate milestone/card, and its
        # own spec requires that until it lands, THIS milestone's plain
        # 200-OK success contract must be preserved unmodified.
        # ------------------------------------------------------------------

        if stt_provider is None:
            # STT is not wired in for this app instance (no injected fake
            # and no STT_BASE_URL configured). Finalize the turn as a plain
            # ingest success — no STT/Hermes call, no fallback machinery.
            save_status("success", None, input_bytes, audio_duration_ms)
            metrics.turns_total.labels(status="success").inc()
            return Response(status_code=200, media_type="audio/wav",
                            headers={"X-Turn-Id": turn_id})

        # --- STT (sync contract: call directly, no thread pool) -----------
        stt_start = time.perf_counter()
        metrics.active_turns.inc()
        try:
            transcript = stt_provider.transcribe(wav_path)
        except Exception as exc:  # STTClientError + any unexpected STT break
            metrics.stt_duration.observe(max(0.0, time.perf_counter() - stt_start))
            metrics.active_turns.dec()
            return fail_turn(ErrorCode.STT_FAILED.value, str(exc),
                             input_bytes, audio_duration_ms)
        metrics.stt_duration.observe(max(0.0, time.perf_counter() - stt_start))
        metrics.active_turns.dec()

        # --- Archive the transcript (ТЗ §18/§19) -------------------------
        atomic_write_bytes(turn_dir / "transcript.txt",
                           transcript.text.encode("utf-8"))

        if hermes_stage is None:
            # STT ran but Hermes is not wired in for this app instance —
            # wiring Hermes in is a separate card's concern. The turn still
            # succeeds; the transcript is archived in both transcript.txt
            # and metadata.json.
            save_status("success", None, input_bytes, audio_duration_ms,
                        extra={"transcript": transcript.text})
            metrics.turns_total.labels(status="success").inc()
            return Response(status_code=200, media_type="audio/wav",
                            headers={"X-Turn-Id": turn_id})

        # Deterministic empty-transcript guard: Hermes has nothing to answer.
        if transcript.text.strip() == "":
            return fail_turn(ErrorCode.HERMES_FAILED.value, "empty transcript",
                             input_bytes, audio_duration_ms,
                             extra={"transcript": transcript.text})

        # --- Single Hermes call (ТЗ §21–§24) -----------------------------
        atomic_write_json(turn_dir / "hermes-request.json",
                          {"turn_id": turn_id, "transcript": transcript.text})

        hermes_start = time.perf_counter()
        metrics.active_turns.inc()
        try:
            response = await hermes_stage.run(transcript.text)
        except HermesStageError as e:
            raw = e.raw  # ORIGINAL text, or None on a transport-level failure
            atomic_write_json(turn_dir / "hermes-response.json",
                              {"raw": raw, "fallback": FALLBACK_REPLY})
            atomic_write_bytes(turn_dir / "reply.txt",
                               FALLBACK_REPLY.encode("utf-8"))
            return fail_turn(e.status, e.error, input_bytes, audio_duration_ms,
                             extra={"transcript": transcript.text})
        finally:
            metrics.hermes_duration.observe(max(0.0, time.perf_counter() - hermes_start))
            metrics.active_turns.dec()

        # --- Success (ТЗ §19/§30): archive reply + note flag. The M2
        # response shape is preserved so the TTS child card can swap the
        # body for real audio later.
        raw = last_raw_response.get()
        atomic_write_json(turn_dir / "hermes-response.json",
                          {"raw": raw, "reply": response.reply,
                           "note_create": response.note.create})
        atomic_write_bytes(turn_dir / "reply.txt", response.reply.encode("utf-8"))
        save_status("success", None, input_bytes, audio_duration_ms,
                    extra={"transcript": transcript.text, "reply": response.reply})
        metrics.turns_total.labels(status="success").inc()

        # ТЗ §12: 200 OK + X-Turn-Id. The WAV *body* arrives in Milestone 6;
        # this milestone answers plain 200 with no audio body (task spec).
        return Response(status_code=200, media_type="audio/wav",
                        headers={"X-Turn-Id": turn_id})

    return app


app = create_app()
