"""Integration tests for the ``/api/v1/voice/turn`` endpoint (M6-05).

The tests drive the ASGI app in-process through :mod:`httpx`'s
``ASGITransport`` (no sockets, no server process). The app is async-only, so
every request runs inside an event loop created per test — no pytest-asyncio
dependency, no sync/async transport mismatch.
"""
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI

from backend.src.voice_gateway.app import create_app


def _make_app(tmp_path: Path) -> FastAPI:
    return create_app(archive_root=tmp_path / "archive")


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


def _headers(device: str, rate: int = 16000, channels: int = 1) -> dict:
    return {"X-Device-Id": device,
            "X-Sample-Rate": str(rate),
            "X-Channels": str(channels)}


def _run(loop: asyncio.AbstractEventLoop, coro):
    """Run ``coro`` on a fresh event loop and close the loop afterwards."""
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _find_turn_dirs(archive_root: Path) -> list[Path]:
    import os
    return [Path(p) for p, _d, files in os.walk(archive_root)
            if "input.pcm" in files]


def _wait_for_turn_dir(archive_root: Path, timeout: float = 5.0) -> Path:
    deadline = time.monotonic() + timeout
    while True:
        dirs = _find_turn_dirs(archive_root)
        if dirs:
            return dirs[0]
        if time.monotonic() >= deadline:
            raise AssertionError("no turn directory created in archive")
        time.sleep(0.05)


def test_health(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    async def run() -> httpx.Response:
        async with _client(app) as client:
            return await client.get("/health")

    resp = _run(asyncio.new_event_loop(), run())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "voice-gateway"
    assert "version" in body


def test_metrics_endpoint_exists(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    async def run() -> httpx.Response:
        async with _client(app) as client:
            return await client.get("/metrics")

    resp = _run(asyncio.new_event_loop(), run())
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")


def test_turn_success(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    pcm = b"\x00" * 8000  # 0.25 s of 16 kHz mono s16le

    async def run() -> httpx.Response:
        async with _client(app) as client:
            return await client.post("/api/v1/voice/turn", content=pcm,
                                     headers=_headers("test-device"))

    resp = _run(asyncio.new_event_loop(), run())
    assert resp.status_code == 200
    turn_id = resp.headers.get("X-Turn-Id")
    assert turn_id and uuid.UUID(turn_id)  # valid UUID
    assert resp.headers["content-type"] == "audio/wav"

    # ТЗ §18: archive/YYYY/MM/DD/<turn-id>/ — check the partitioned path,
    # not just "a dir somewhere under the archive".
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).date()
    turn_dir = tmp_path / "archive" / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" / turn_id
    assert turn_dir.is_dir()
    assert (turn_dir / "input.pcm").exists()
    assert (turn_dir / "input.pcm").read_bytes() == pcm
    assert (turn_dir / "input.wav").exists()

    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["turn_id"] == turn_id
    assert meta["device_id"] == "test-device"
    assert meta["status"] == "success"
    assert meta["input_bytes"] == len(pcm)
    assert meta["audio_duration_ms"] == 250
    assert meta["started_at"] and meta["finished_at"]


def test_turn_fragmented_stream_finalizes_valid_wav(tmp_path: Path) -> None:
    """The ATOM sends the recording as many small chunks (ТЗ §11). The
    endpoint must reassemble them and finalize a *valid* WAV: the
    acceptance criterion for this milestone (task body)."""
    app = _make_app(tmp_path)
    pcm = bytes(range(256)) * 32  # 8192 B, non-zero pattern so corruption is visible

    async def run() -> httpx.Response:
        async with _client(app) as client:
            # An async generator body drives httpx into chunked streaming,
            # so the server side really processes the stream fragment by
            # fragment — this is the shape of the real ATOM upload.
            async def agen():
                chunk = 64  # small: forces many stream iterations
                for i in range(0, len(pcm), chunk):
                    yield pcm[i:i + chunk]

            return await client.post("/api/v1/voice/turn", content=agen(),
                                     headers=_headers("test-device"))

    resp = _run(asyncio.new_event_loop(), run())
    assert resp.status_code == 200
    turn_id = resp.headers.get("X-Turn-Id")
    assert turn_id and uuid.UUID(turn_id)

    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).date()
    turn_dir = tmp_path / "archive" / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" / turn_id
    assert turn_dir.is_dir(), "turn dir missing under YYYY/MM/DD partition"

    # The reassembled PCM must be byte-identical to what was sent.
    assert (turn_dir / "input.pcm").read_bytes() == pcm

    # input.wav must be a *valid* WAV per the stdlib parser.
    import wave
    with wave.open(str(turn_dir / "input.wav"), "rb") as w:
        assert w.getsampwidth() == 2  # s16le
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getnframes() == len(pcm) // 2
        frames = w.readframes(w.getnframes())
    assert frames == pcm

    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"
    assert meta["input_bytes"] == len(pcm)


def test_turn_metadata_on_disconnect(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    async def run() -> None:
        async with _client(app) as client:
            # Send an async body iterator that raises mid-stream to simulate
            # the client dropping before EOF (ТЗ §32: metadata must still land).
            # It must be an *async* generator: httpx.AsyncClient only accepts
            # an AsyncByteStream, and build_request wraps an async generator
            # (a sync generator would raise before the app is even reached).
            async def broken_body():
                yield b"\x00" * 100
                raise httpx.HTTPError("simulated client drop")

            req = client.build_request("POST", "/api/v1/voice/turn",
                                       content=broken_body(),
                                       headers=_headers("dev-disc"))
            try:
                await client.send(req)
            except httpx.HTTPError:
                pass  # expected: the stream broke

    _run(asyncio.new_event_loop(), run())

    # A failed turn must still leave a metadata.json with the failure status.
    turn_dir = _wait_for_turn_dir(tmp_path / "archive")
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] in ("audio_receive_failed", "internal_error")
    assert meta["error"]


def test_turn_invalid_sample_rate_falls_back(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    pcm = b"\x00" * 4000

    async def run() -> httpx.Response:
        async with _client(app) as client:
            return await client.post(
                "/api/v1/voice/turn", content=pcm,
                headers={"X-Device-Id": "dev-x",
                         "X-Sample-Rate": "not-a-number",
                         "X-Channels": "3"})

    resp = _run(asyncio.new_event_loop(), run())
    # Non-numeric headers fall back to defaults (16000/1) rather than 4xx.
    assert resp.status_code == 200
    assert resp.headers.get("X-Turn-Id")


def test_health_after_many_turns(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    pcm = b"\x00" * 2000

    async def run() -> None:
        async with _client(app) as client:
            for _ in range(5):
                r = await client.post("/api/v1/voice/turn", content=pcm,
                                      headers=_headers("load-dev"))
                assert r.status_code == 200

    _run(asyncio.new_event_loop(), run())

    async def run_health() -> httpx.Response:
        async with _client(app) as client:
            return await client.get("/health")

    resp = _run(asyncio.new_event_loop(), run_health())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
