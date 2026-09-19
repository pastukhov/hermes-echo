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

from backend.src.voice_gateway.app import FALLBACK_REPLY, create_app
from backend.src.voice_gateway.hermes.base import HermesClient, HermesClientError
from backend.src.voice_gateway.hermes.fake import FakeHermes
from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt.base import STTClientError, STTProvider
from backend.src.voice_gateway.stt.fake import FakeSTT

_VALID_HERMES_RAW = (
    '{"reply": "Готово.", "note": {"create": false, "title": "", '
    '"content": "", "tags": []}}'
)


def _make_app(tmp_path: Path,
              hermes: HermesClient | None = None) -> FastAPI:
    return create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text="тестовая расшифровка", language="ru")),
        hermes=hermes if hermes is not None else FakeHermes(_VALID_HERMES_RAW),
    )


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
            if "metadata.json" in files]


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
    assert not (turn_dir / "input.pcm").exists()
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

    # The reassembled PCM is now consumed and deleted after a successful
    # finalize; the WAV-frame comparison below already covers correctness.
    assert not (turn_dir / "input.pcm").exists()

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


def test_turn_odd_byte_body_audio_invalid_metadata(tmp_path: Path) -> None:
    """Odd-byte PCM is a KNOWN error (ТЗ §13/§32): metadata.json is saved
    atomically with status=audio_invalid + sanitized error BEFORE the 502 is
    answered, and the response carries only the bounded code + turn_id —
    never the diagnostic text."""
    app = _make_app(tmp_path)
    pcm = b"\x00" * 3  # odd byte count: not valid PCM S16LE

    resp = _post_turn(app, pcm)

    assert resp.status_code == 502
    body = resp.json()
    assert set(body) == {"error", "turn_id"}
    assert body["error"] == "audio_invalid"
    uuid.UUID(body["turn_id"])

    turn_dir = _last_turn_dir(tmp_path / "archive")
    assert turn_dir.name == body["turn_id"]
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "audio_invalid"
    assert meta["error"]  # diagnostic text lives in metadata only
    assert meta["turn_id"] == body["turn_id"]
    assert meta["input_bytes"] == 3
    assert not (turn_dir / "input.wav").exists()  # finalized only after validation


def test_turn_empty_body_audio_invalid_metadata(tmp_path: Path) -> None:
    """Empty body → audio_invalid with the same guarantees (ТЗ §13/§32)."""
    app = _make_app(tmp_path)

    resp = _post_turn(app, b"")

    assert resp.status_code == 502
    body = resp.json()
    assert set(body) == {"error", "turn_id"}
    assert body["error"] == "audio_invalid"

    meta = json.loads(
        (_last_turn_dir(tmp_path / "archive") / "metadata.json")
        .read_text(encoding="utf-8"))
    assert meta["status"] == "audio_invalid"
    assert meta["error"] == "empty audio body"
    assert meta["turn_id"] == body["turn_id"]


def test_health_after_many_turns(tmp_path: Path) -> None:
    # _MultiCallHermes: the shared single-call FakeHermes raises on the 2nd
    # turn; a load test needs a stand-in that serves every turn.
    app = _make_app(tmp_path, hermes=_MultiCallHermes())
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


# ---------------------------------------------------------------------------
# STT → Hermes pipeline (ТЗ §21–24, §30, §32) — providers injected as fakes
# ---------------------------------------------------------------------------

class _RaisingSTT(STTProvider):
    """STT stand-in that fails at the transport level (ТЗ §20)."""

    def transcribe(self, wav: Path) -> Transcript:
        raise STTClientError("simulated stt transport failure")


class _RaisingHermes(HermesClient):
    """Hermes stand-in that fails at the transport level (ТЗ §21)."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, transcript: str) -> str:
        self.calls += 1
        raise HermesClientError("simulated hermes transport failure")


class _MultiCallHermes(HermesClient):
    """Hermes stand-in that serves MANY turns with the same valid payload.

    ``FakeHermes`` enforces the single-call rule (one turn per instance) and
    therefore cannot back a multi-turn load test; this one answers every
    turn deterministically.
    """

    def __init__(self, raw: str = _VALID_HERMES_RAW) -> None:
        self._raw = raw
        self.calls = 0

    async def complete(self, transcript: str) -> str:
        self.calls += 1
        self.last_transcript = transcript
        return self._raw


def _post_turn(app: FastAPI, pcm: bytes, device: str = "pipe-dev") -> httpx.Response:
    async def run() -> httpx.Response:
        async with _client(app) as client:
            return await client.post("/api/v1/voice/turn", content=pcm,
                                     headers=_headers(device))

    return _run(asyncio.new_event_loop(), run())


def _last_turn_dir(archive_root: Path) -> Path:
    dirs = _find_turn_dirs(archive_root)
    assert dirs, "no turn directory found in archive"
    return max(dirs, key=lambda p: p.stat().st_mtime)


def test_pipeline_success_writes_all_artifacts(tmp_path: Path) -> None:
    """ТЗ §18/§19/§30: success path archives transcript/request/response/reply
    and records them in metadata.json; response shape stays 200 audio/wav."""
    stt = FakeSTT(Transcript(text="привет", language="ru"))
    hermes = FakeHermes(
        '{"reply": "привет", "note": {"create": false, "title": "", '
        '"content": "", "tags": []}}')
    app = create_app(archive_root=tmp_path / "archive", stt=stt, hermes=hermes)
    pcm = b"\x00" * 8000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    turn_id = resp.headers.get("X-Turn-Id")
    assert turn_id and uuid.UUID(turn_id)
    assert resp.content == b""  # M2/M5/6 contract: empty body for now

    turn_dir = _last_turn_dir(tmp_path / "archive")
    assert turn_dir.name == turn_id
    assert (turn_dir / "transcript.txt").read_text(encoding="utf-8") == "привет"
    req = json.loads((turn_dir / "hermes-request.json").read_text(encoding="utf-8"))
    assert req == {"turn_id": turn_id, "transcript": "привет"}
    resp_doc = json.loads((turn_dir / "hermes-response.json").read_text(encoding="utf-8"))
    assert resp_doc["reply"] == "привет"
    assert resp_doc["note_create"] is False
    assert json.loads(resp_doc["raw"])["reply"] == "привет"
    assert (turn_dir / "reply.txt").read_text(encoding="utf-8") == "привет"

    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"
    assert meta["transcript"] == "привет"
    assert meta["reply"] == "привет"
    assert meta["turn_id"] == turn_id


def test_pipeline_hermes_called_exactly_once(tmp_path: Path) -> None:
    """ТЗ §24: one call + one repair — the client is invoked exactly once."""
    hermes = FakeHermes('{"reply": "один раз", "note": {"create": false}}')
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text="раз", language="ru")),
        hermes=hermes)

    resp = _post_turn(app, b"\x00" * 4000)

    assert resp.status_code == 200
    assert hermes.calls == 1


def test_pipeline_stt_failure(tmp_path: Path) -> None:
    """STTClientError → 502 JSON + metadata status stt_failed (ТЗ §32)."""
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=_RaisingSTT(),
        hermes=FakeHermes(_VALID_HERMES_RAW))
    pcm = b"\x00" * 4000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["error"] == "stt_failed"
    assert uuid.UUID(body["turn_id"])

    turn_dir = _last_turn_dir(tmp_path / "archive")
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "stt_failed"
    assert meta["error"]
    assert meta["turn_id"] == body["turn_id"]
    # Hermes stage never ran.
    assert not (turn_dir / "hermes-request.json").exists()


def test_stt_failure_without_hermes_wired_stays_stt_failed(tmp_path: Path) -> None:
    """This card's scope: an STT-only app (no Hermes injected) whose STT
    provider raises must still fail the turn with ``stt_failed`` — never a
    silent/misleading success, never a bare 500/internal_error. No
    transcript.txt is created (STT never produced a transcript), and no
    downstream (Hermes) call happens since Hermes isn't even wired in."""
    app = create_app(archive_root=tmp_path / "archive", stt=_RaisingSTT())
    pcm = b"\x00" * 4000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"error", "turn_id"}
    assert body["error"] == "stt_failed"
    assert uuid.UUID(body["turn_id"])

    turn_dir = _last_turn_dir(tmp_path / "archive")
    assert turn_dir.name == body["turn_id"]
    assert not (turn_dir / "transcript.txt").exists()
    assert not (turn_dir / "hermes-request.json").exists()
    assert not (turn_dir / "hermes-response.json").exists()

    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "stt_failed"
    assert meta["error"]
    assert meta["turn_id"] == body["turn_id"]
    # Diagnostic error must not leak into the client-facing response.
    assert "error" not in body or body["error"] == "stt_failed"


def test_pipeline_hermes_transport_failure(tmp_path: Path) -> None:
    """HermesClientError → 502 + hermes_failed + fallback artifacts (ТЗ §32)."""
    hermes = _RaisingHermes()
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text="запрос", language="ru")),
        hermes=hermes)
    pcm = b"\x00" * 4000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "hermes_failed"
    assert uuid.UUID(body["turn_id"])
    assert hermes.calls == 1  # no retry of the transport call

    turn_dir = _last_turn_dir(tmp_path / "archive")
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "hermes_failed"
    assert meta["error"]
    # Fallback artifacts are archived even on a transport failure.
    assert (turn_dir / "reply.txt").read_text(encoding="utf-8") == FALLBACK_REPLY
    resp_doc = json.loads(
        (turn_dir / "hermes-response.json").read_text(encoding="utf-8"))
    assert resp_doc["raw"] is None
    assert resp_doc["fallback"] == FALLBACK_REPLY


def test_pipeline_hermes_invalid_response(tmp_path: Path) -> None:
    """Unrepairable payload → 502 + hermes_invalid_response, ORIGINAL raw
    archived (the single repair pass already happened inside the parser)."""
    hermes = FakeHermes("not json at all")
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text="вопрос", language="ru")),
        hermes=hermes)
    pcm = b"\x00" * 4000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "hermes_invalid_response"
    assert uuid.UUID(body["turn_id"])
    assert hermes.calls == 1  # repair happened in the parser, not via a 2nd call

    turn_dir = _last_turn_dir(tmp_path / "archive")
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "hermes_invalid_response"
    assert meta["error"]
    assert (turn_dir / "reply.txt").read_text(encoding="utf-8") == FALLBACK_REPLY
    resp_doc = json.loads(
        (turn_dir / "hermes-response.json").read_text(encoding="utf-8"))
    assert resp_doc["raw"] == "not json at all"
    assert resp_doc["fallback"] == FALLBACK_REPLY


def test_pipeline_empty_transcript(tmp_path: Path) -> None:
    """Deterministic guard: empty transcript → hermes_failed, Hermes not
    called at all (it would have nothing to answer)."""
    hermes = FakeHermes(_VALID_HERMES_RAW)
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text="   ", language="ru")),
        hermes=hermes)
    pcm = b"\x00" * 4000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "hermes_failed"
    assert uuid.UUID(body["turn_id"])
    assert hermes.calls == 0  # the guard fires before the single call

    turn_dir = _last_turn_dir(tmp_path / "archive")
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "hermes_failed"
    assert meta["error"] == "empty transcript"
    # transcript.txt is still archived (it was produced by STT).
    assert (turn_dir / "transcript.txt").read_text(encoding="utf-8") == "   "
    assert not (turn_dir / "hermes-request.json").exists()


def test_pipeline_providers_unconfigured(tmp_path: Path) -> None:
    """App built without STT/Hermes still preserves the M2 ingest-only
    contract (this card's task body): a plain audio turn succeeds with
    200 + X-Turn-Id and no audio body, no STT/Hermes/TTS involved. The
    STT-pipeline milestone injects real providers later via
    ``create_app(stt=..., hermes=...)``; until then every turn must not
    502 just because those providers are absent."""
    app = create_app(archive_root=tmp_path / "archive")
    pcm = b"\x00" * 8000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 200
    turn_id = resp.headers.get("X-Turn-Id")
    assert turn_id and uuid.UUID(turn_id)
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == b""

    turn_dir = _last_turn_dir(tmp_path / "archive")
    assert (turn_dir / "input.wav").exists()
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"
    assert meta["turn_id"] == turn_id
    # No STT/Hermes artifacts — this milestone's contract is ingest-only.
    assert not (turn_dir / "transcript.txt").exists()
    assert not (turn_dir / "hermes-request.json").exists()


def test_production_app_default_wiring_succeeds(tmp_path: Path, monkeypatch) -> None:
    """Regression test for the module-level production singleton.

    ``app.py:app = create_app()`` (the only instance the Dockerfile's
    ``uvicorn backend.src.voice_gateway.app:app`` serves) is built with NO
    injected STT/Hermes. This exercises exactly that production wiring path
    — no fakes injected anywhere — and asserts it still answers 200 +
    X-Turn-Id for a plain audio turn, per this card's acceptance criteria.
    """
    monkeypatch.setenv("ARCHIVE_ROOT", str(tmp_path / "archive"))
    monkeypatch.delenv("STT_BASE_URL", raising=False)
    app = create_app()  # mirrors the production `app = create_app()` call
    pcm = b"\x00" * 4000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 200
    turn_id = resp.headers.get("X-Turn-Id")
    assert turn_id and uuid.UUID(turn_id)

    turn_dir = _last_turn_dir(tmp_path / "archive")
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"


# ---------------------------------------------------------------------------
# STT runs independently of Hermes wiring (this card: STT-pipeline
# integration, ТЗ §20; Hermes wiring is a separate concern/card)
# ---------------------------------------------------------------------------

def test_stt_wired_without_hermes_still_transcribes(tmp_path: Path) -> None:
    """An STTProvider injected with NO Hermes client still runs: the turn
    succeeds, calls STT exactly once, and archives the exact transcript to
    transcript.txt (UTF-8) and metadata.json's ``transcript`` field — no
    Hermes artifacts are produced (Hermes wiring is out of this card's
    scope)."""
    stt = FakeSTT(Transcript(text="привет без гермеса", language="ru"))
    app = create_app(archive_root=tmp_path / "archive", stt=stt)
    pcm = b"\x00" * 4000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 200
    turn_id = resp.headers.get("X-Turn-Id")
    assert turn_id and uuid.UUID(turn_id)

    turn_dir = _last_turn_dir(tmp_path / "archive")
    assert turn_dir.name == turn_id
    assert (turn_dir / "transcript.txt").read_text(encoding="utf-8") == \
        "привет без гермеса"
    assert not (turn_dir / "hermes-request.json").exists()
    assert not (turn_dir / "hermes-response.json").exists()

    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"
    assert meta["transcript"] == "привет без гермеса"
    assert meta["turn_id"] == turn_id


class _ValidatingSTT(STTProvider):
    """STT stand-in that PROVES the WAV it receives is already finalized.

    Opens the given path with the stdlib ``wave`` reader (which raises on
    a truncated/incomplete RIFF file) and records how many times and with
    what path it was called - the concrete proof that finalize-before-
    transcribe ordering (this card's contract) actually holds, not just
    that a WAV file happens to exist somewhere by the time the response
    is returned."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0
        self.seen_paths: list[Path] = []

    def transcribe(self, wav: Path) -> Transcript:
        self.calls += 1
        self.seen_paths.append(wav)
        import wave
        with wave.open(str(wav), "rb") as w:
            # A partially written / still-open-for-write WAV would not
            # parse as valid RIFF/WAVE; a successful open here is the
            # proof that pcm_to_wav() already completed and closed the
            # file before this call happened.
            w.getnframes()
        return Transcript(text=self._text, language="ru")


def test_stt_called_once_after_wav_finalized_with_exact_utf8_transcript(
    tmp_path: Path,
) -> None:
    """Vertical tracer for this card's core contract (TZ section 20 wiring):

    1. STTProvider.transcribe() is called exactly once per turn;
    2. it is called with a path to an already-closed, valid WAV file
       (finalize-before-transcribe ordering - never a half-written PCM);
    3. the exact (Unicode/Cyrillic) Transcript.text it returns lands
       byte-for-byte, UTF-8, in both transcript.txt and metadata.json's
       ``transcript`` field."""
    stt = _ValidatingSTT("привет, это тестовая расшифровка")
    app = create_app(archive_root=tmp_path / "archive", stt=stt)
    pcm = b"\x00" * 4000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 200
    turn_id = resp.headers.get("X-Turn-Id")
    assert turn_id and uuid.UUID(turn_id)

    # Exactly one STT call, against the turn's own finalized input.wav.
    assert stt.calls == 1
    assert stt.seen_paths == [_last_turn_dir(tmp_path / "archive") / "input.wav"]

    turn_dir = _last_turn_dir(tmp_path / "archive")
    assert turn_dir.name == turn_id
    assert (turn_dir / "input.wav").exists()
    assert (turn_dir / "transcript.txt").read_text(encoding="utf-8") == \
        "привет, это тестовая расшифровка"

    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"
    assert meta["transcript"] == "привет, это тестовая расшифровка"
    assert meta["turn_id"] == turn_id


def test_create_app_wires_default_stt_provider_when_not_injected(
    tmp_path: Path, monkeypatch
) -> None:
    """``create_app()`` with no ``stt=`` argument falls back to the module's
    ``_default_stt_provider()`` (built from ``STT_BASE_URL`` etc, ТЗ §20) —
    the production singleton ``app = create_app()`` must automatically pick
    up a configured STT endpoint without any caller wiring it in by hand."""
    from backend.src.voice_gateway import app as app_module

    sentinel_stt = FakeSTT(Transcript(text="из окружения", language="ru"))
    monkeypatch.setattr(app_module, "_default_stt_provider", lambda: sentinel_stt)

    app = app_module.create_app(archive_root=tmp_path / "archive")
    pcm = b"\x00" * 4000

    resp = _post_turn(app, pcm)

    assert resp.status_code == 200
    turn_dir = _last_turn_dir(tmp_path / "archive")
    assert (turn_dir / "transcript.txt").read_text(encoding="utf-8") == \
        "из окружения"
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"
    assert meta["transcript"] == "из окружения"
