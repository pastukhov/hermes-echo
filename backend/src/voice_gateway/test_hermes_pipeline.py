"""Integration tests for the Hermes-stage wiring (ТЗ §13, §21–§24, §31, §32).

Complements ``test_app_stream.py`` with the deterministic paths it does not
cover:

- HERMES_TIMEOUT: a timeout surfaces at the stage boundary as a
  ``HermesClientError`` and must map to ``hermes_failed`` (ТЗ §31/§32);
- the single repair pass (ТЗ §24): a mechanically damaged payload that IS
  repaired must produce a normal success, and a payload that is still invalid
  AFTER the repair attempt must produce ``hermes_invalid_response``;
- the stage order STT transcript → HermesClient → next stage;
- no artifact overwrite between turns, both sequential and concurrent
  (the shared :class:`HermesStage` routes the raw payload through a
  task-local ContextVar);
- the exact HTTP answer contract of ТЗ §12/§13 (locked in card t_30ce6e75):
  success = 200 + ``Content-Type: audio/wav`` + ``X-Turn-Id`` + empty body
  (the WAV body arrives in Milestone 6); failure = 502 + ``application/json``
  with a body of EXACTLY ``{"error": <status>, "turn_id": <id>}``.

All tests are deterministic: no real network, no sockets — the ASGI app is
driven in-process through ``httpx.ASGITransport`` with scripted fakes, the
same convention as the rest of the suite (no pytest-asyncio).
"""
import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI

from backend.src.voice_gateway.app import FALLBACK_REPLY, create_app
from backend.src.voice_gateway.hermes.base import HermesClient, HermesClientError
from backend.src.voice_gateway.hermes.fake import FakeHermes
from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt.base import STTProvider
from backend.src.voice_gateway.stt.fake import FakeSTT

_PCM = b"\x00" * 4000  # 125 ms of 16 kHz mono s16le


# ---------------------------------------------------------------------------
# Scripted fakes (deterministic, no I/O)
# ---------------------------------------------------------------------------

class _TimeoutHermes(HermesClient):
    """Hermes stand-in whose request exceeds HERMES_TIMEOUT (ТЗ §31).

    The real client enforces the timeout with httpx and surfaces it as
    ``HermesClientError``; at the stage boundary that is the only thing the
    pipeline sees, so this is the deterministic shape of a timed-out turn.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, transcript: str) -> str:
        self.calls += 1
        raise HermesClientError("hermes request timed out")


class _ScriptedSTT(STTProvider):
    """Returns pre-set transcripts one per turn (deterministic rotation)."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls = 0

    def transcribe(self, wav: Path) -> Transcript:
        self.calls += 1
        if not self._texts:
            raise AssertionError("more turns than scripted transcripts")
        return Transcript(text=self._texts.pop(0), language="ru")


class _EchoHermes(HermesClient):
    """Hermes stand-in whose reply depends on the transcript.

    Every turn's archived artifacts become self-identifying: a turn's
    ``reply.txt`` / ``hermes-response.json`` can only contain "ответ на: X"
    for the transcript X that turn actually transcribed — which is exactly
    what cross-turn isolation must guarantee.
    """

    def __init__(self) -> None:
        self.transcripts: list[str] = []

    async def complete(self, transcript: str) -> str:
        self.transcripts.append(transcript)
        return json.dumps(
            {"reply": f"ответ на: {transcript}",
             "note": {"create": False, "title": "", "content": "", "tags": []}},
            ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers (same in-process ASGI convention as test_app_stream.py)
# ---------------------------------------------------------------------------

def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


def _headers(device: str = "pipe-dev") -> dict:
    return {"X-Device-Id": device, "X-Sample-Rate": "16000", "X-Channels": "1"}


def _run(loop: asyncio.AbstractEventLoop, coro):
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _post_turn(app: FastAPI, pcm: bytes = _PCM) -> httpx.Response:
    async def run() -> httpx.Response:
        async with _client(app) as client:
            return await client.post("/api/v1/voice/turn", content=pcm,
                                     headers=_headers())
    return _run(asyncio.new_event_loop(), run())


def _turn_dirs(archive_root: Path) -> list[Path]:
    return [Path(p) for p, _d, files in os.walk(archive_root)
            if "metadata.json" in files]


def _check_artifacts_consistent(turn_dir: Path, transcript: str) -> None:
    """One turn's archived artifacts must agree with ITS transcript only."""
    reply = f"ответ на: {transcript}"
    assert (turn_dir / "transcript.txt").read_text(encoding="utf-8") == transcript
    req = json.loads((turn_dir / "hermes-request.json").read_text(encoding="utf-8"))
    assert req["transcript"] == transcript
    resp_doc = json.loads((turn_dir / "hermes-response.json").read_text(encoding="utf-8"))
    assert resp_doc["reply"] == reply
    assert json.loads(resp_doc["raw"])["reply"] == reply
    assert resp_doc["note_create"] is False
    assert (turn_dir / "reply.txt").read_text(encoding="utf-8") == reply
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"
    assert meta["transcript"] == transcript
    assert meta["reply"] == reply


# ---------------------------------------------------------------------------
# Timeout (ТЗ §31/§32)
# ---------------------------------------------------------------------------

def test_timeout_maps_to_hermes_failed(tmp_path: Path) -> None:
    """HERMES_TIMEOUT → transport-level failure → hermes_failed (ТЗ §32)."""
    hermes = _TimeoutHermes()
    app = create_app(archive_root=tmp_path / "archive",
                     stt=FakeSTT(Transcript(text="запрос", language="ru")),
                     hermes=hermes)

    resp = _post_turn(app)

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "hermes_failed"
    assert hermes.calls == 1  # a timed-out turn is never retried
    turn_dir = _turn_dirs(tmp_path / "archive")[0]
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "hermes_failed"
    assert meta["error"]


# ---------------------------------------------------------------------------
# The single repair pass (ТЗ §24)
# ---------------------------------------------------------------------------

def test_repair_fenced_json_succeeds(tmp_path: Path) -> None:
    """Mechanically damaged payload that the ONE repair pass fixes → normal
    success: the client is still called exactly once, the ORIGINAL raw text
    (fences included) is archived, the parsed reply flows on (ТЗ §24)."""
    raw = '<json>{"reply": "починено", "note": {"create": false}}</json>'
    hermes = FakeHermes(raw)
    app = create_app(archive_root=tmp_path / "archive",
                     stt=FakeSTT(Transcript(text="вопрос", language="ru")),
                     hermes=hermes)

    resp = _post_turn(app)

    assert resp.status_code == 200
    assert hermes.calls == 1  # repair is in the parser, not a second call
    turn_dir = _turn_dirs(tmp_path / "archive")[0]
    resp_doc = json.loads((turn_dir / "hermes-response.json").read_text(encoding="utf-8"))
    assert resp_doc["reply"] == "починено"
    assert resp_doc["raw"] == raw  # ORIGINAL text, not the repaired candidate
    assert (turn_dir / "reply.txt").read_text(encoding="utf-8") == "починено"
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"
    assert meta["reply"] == "починено"


def test_repair_trailing_comma_succeeds(tmp_path: Path) -> None:
    """The second mechanical repair candidate: a trailing comma."""
    raw = '{"reply": "ок", "note": {"create": false},}'
    hermes = FakeHermes(raw)
    app = create_app(archive_root=tmp_path / "archive",
                     stt=FakeSTT(Transcript(text="вопрос", language="ru")),
                     hermes=hermes)

    resp = _post_turn(app)

    assert resp.status_code == 200
    assert hermes.calls == 1
    turn_dir = _turn_dirs(tmp_path / "archive")[0]
    resp_doc = json.loads((turn_dir / "hermes-response.json").read_text(encoding="utf-8"))
    assert resp_doc["reply"] == "ок"
    assert resp_doc["raw"] == raw


# ---------------------------------------------------------------------------
# Invalid response AFTER the repair attempt (ТЗ §24 step 2)
# ---------------------------------------------------------------------------

def test_invalid_after_repair(tmp_path: Path) -> None:
    """Repair ran (the fenced block was extracted) but the payload is still
    invalid (``reply`` must be a string) → hermes_invalid_response with the
    ORIGINAL raw archived, fallback reply, no note, exactly one call."""
    raw = '<json>{"reply": 123}</json>'
    hermes = FakeHermes(raw)
    app = create_app(archive_root=tmp_path / "archive",
                     stt=FakeSTT(Transcript(text="вопрос", language="ru")),
                     hermes=hermes)

    resp = _post_turn(app)

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "hermes_invalid_response"
    assert hermes.calls == 1  # no second attempt after a failed repair
    turn_dir = _turn_dirs(tmp_path / "archive")[0]
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "hermes_invalid_response"
    assert meta["error"]
    resp_doc = json.loads((turn_dir / "hermes-response.json").read_text(encoding="utf-8"))
    assert resp_doc["raw"] == raw  # ORIGINAL text, not the repaired fragment
    assert resp_doc["fallback"] == FALLBACK_REPLY
    assert (turn_dir / "reply.txt").read_text(encoding="utf-8") == FALLBACK_REPLY
    # ТЗ §24: never create a note on an unrepairable payload.
    assert resp_doc.get("note_create") is None


# ---------------------------------------------------------------------------
# Stage order: STT transcript → HermesClient → next stage (ТЗ §15, §21)
# ---------------------------------------------------------------------------

def test_order_stt_transcript_to_hermes_to_next_stage(tmp_path: Path) -> None:
    """The exact STT transcript is what Hermes receives, and the parsed
    reply is what the next stage (archive/metadata for M5/6) receives."""
    hermes = FakeHermes(
        '{"reply": "готово", "note": {"create": false, "title": "", '
        '"content": "", "tags": []}}')
    app = create_app(archive_root=tmp_path / "archive",
                     stt=FakeSTT(Transcript(text="привет мир", language="ru")),
                     hermes=hermes)

    resp = _post_turn(app)

    assert resp.status_code == 200
    turn_id = resp.headers["X-Turn-Id"]
    # 1) STT → HermesClient: the client saw the transcript, verbatim.
    assert hermes.last_transcript == "привет мир"
    turn_dir = _turn_dirs(tmp_path / "archive")[0]
    assert (turn_dir / "transcript.txt").read_text(encoding="utf-8") == "привет мир"
    req = json.loads((turn_dir / "hermes-request.json").read_text(encoding="utf-8"))
    assert req == {"turn_id": turn_id, "transcript": "привет мир"}
    # 2) HermesClient → next stage: the PARSED result (not the raw JSON)
    #    is what reply.txt / hermes-response.json / metadata.json carry.
    assert (turn_dir / "reply.txt").read_text(encoding="utf-8") == "готово"
    resp_doc = json.loads((turn_dir / "hermes-response.json").read_text(encoding="utf-8"))
    assert resp_doc["reply"] == "готово"
    assert resp_doc["note_create"] is False
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["transcript"] == "привет мир"
    assert meta["reply"] == "готово"
    assert meta["turn_id"] == turn_id


# ---------------------------------------------------------------------------
# No overwrite between turns (ТЗ §18: one directory per turn)
# ---------------------------------------------------------------------------

def test_sequential_turns_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """Two turns through the SAME app instance (shared stage/STT wiring):
    each turn's directory must contain only its own transcript, request,
    response and reply."""
    stt = _ScriptedSTT(["первый вопрос", "второй вопрос"])
    hermes = _EchoHermes()
    app = create_app(archive_root=tmp_path / "archive", stt=stt, hermes=hermes)

    r1 = _post_turn(app)
    r2 = _post_turn(app)
    assert r1.status_code == 200 and r2.status_code == 200
    t1, t2 = r1.headers["X-Turn-Id"], r2.headers["X-Turn-Id"]
    assert t1 != t2
    assert set(hermes.transcripts) == {"первый вопрос", "второй вопрос"}

    dirs = _turn_dirs(tmp_path / "archive")
    assert {d.name for d in dirs} == {t1, t2}
    _check_artifacts_consistent(next(d for d in dirs if d.name == t1), "первый вопрос")
    _check_artifacts_consistent(next(d for d in dirs if d.name == t2), "второй вопрос")


def test_concurrent_turns_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """Two turns in flight at the SAME time on one shared HermesStage.

    The raw payload travels through the task-local ContextVar
    (``last_raw_response``); if it leaked across tasks one turn would
    archive the other's response. Each turn's artifacts must stay
    self-consistent with its own transcript."""
    stt = _ScriptedSTT(["конкурентный первый", "конкурентный второй"])
    hermes = _EchoHermes()
    app = create_app(archive_root=tmp_path / "archive", stt=stt, hermes=hermes)

    async def run() -> tuple[httpx.Response, httpx.Response]:
        async with _client(app) as client:
            req1 = client.build_request("POST", "/api/v1/voice/turn",
                                        content=_PCM, headers=_headers("dev-A"))
            req2 = client.build_request("POST", "/api/v1/voice/turn",
                                        content=_PCM, headers=_headers("dev-B"))
            resp1, resp2 = await asyncio.gather(
                client.send(req1), client.send(req2))
        return resp1, resp2

    r1, r2 = _run(asyncio.new_event_loop(), run())
    assert r1.status_code == 200 and r2.status_code == 200
    t1, t2 = r1.headers["X-Turn-Id"], r2.headers["X-Turn-Id"]
    assert t1 != t2
    assert set(hermes.transcripts) == {"конкурентный первый", "конкурентный второй"}

    dirs = _turn_dirs(tmp_path / "archive")
    assert {d.name for d in dirs} == {t1, t2}
    for d in dirs:
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        # Self-consistency: whatever transcript this turn got, every
        # archived artifact in ITS directory must match it.
        _check_artifacts_consistent(d, meta["transcript"])
    # And the two turns must have gotten DIFFERENT transcripts (no shared
    # state shortcut: both cannot have been served the same turn).
    metas = {d.name: json.loads((d / "metadata.json").read_text(encoding="utf-8"))
             for d in dirs}
    assert metas[t1]["transcript"] != metas[t2]["transcript"]
    assert {metas[t1]["device_id"], metas[t2]["device_id"]} == {"dev-A", "dev-B"}


# ---------------------------------------------------------------------------
# Exact HTTP answer contract (ТЗ §12/§13, locked in t_30ce6e75)
# ---------------------------------------------------------------------------

def test_success_http_contract_exact(tmp_path: Path) -> None:
    """Success: 200, Content-Type audio/wav, X-Turn-Id, EMPTY body (the WAV
    body arrives in Milestone 6)."""
    app = create_app(archive_root=tmp_path / "archive",
                     stt=FakeSTT(Transcript(text="привет", language="ru")),
                     hermes=FakeHermes('{"reply": "ок", "note": {"create": false}}'))

    resp = _post_turn(app)

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    turn_id = resp.headers.get("X-Turn-Id")
    assert turn_id and uuid.UUID(turn_id)
    assert resp.content == b""


def _failure_contract(resp: httpx.Response, status: str) -> str:
    """Assert the exact ТЗ §13 failure answer; return the turn_id."""
    assert 500 <= resp.status_code < 600, "failures must be 5xx"
    assert resp.status_code == 502, "locked contract: deterministic 502, never 500"
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    # EXACTLY two keys — no diagnostics leak to the client (ТЗ §13/§33).
    assert set(body) == {"error", "turn_id"}, body
    assert body["error"] == status
    assert uuid.UUID(body["turn_id"])
    return body["turn_id"]


def test_failure_http_contract_exact_hermes_failed(tmp_path: Path) -> None:
    hermes = _TimeoutHermes()
    app = create_app(archive_root=tmp_path / "archive",
                     stt=FakeSTT(Transcript(text="запрос", language="ru")),
                     hermes=hermes)
    resp = _post_turn(app)
    turn_id = _failure_contract(resp, "hermes_failed")
    turn_dir = next(d for d in _turn_dirs(tmp_path / "archive") if d.name == turn_id)
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "hermes_failed"
    assert meta["turn_id"] == turn_id  # response and metadata agree


def test_failure_http_contract_exact_invalid_response(tmp_path: Path) -> None:
    app = create_app(archive_root=tmp_path / "archive",
                     stt=FakeSTT(Transcript(text="запрос", language="ru")),
                     hermes=FakeHermes("not json at all"))
    resp = _post_turn(app)
    turn_id = _failure_contract(resp, "hermes_invalid_response")
    turn_dir = next(d for d in _turn_dirs(tmp_path / "archive") if d.name == turn_id)
    meta = json.loads((turn_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "hermes_invalid_response"
    assert meta["turn_id"] == turn_id
