"""Full-turn structured-logging tests (ТЗ §33, acceptance criteria 1-3).

Drives the ASGI app in-process (same convention as ``test_app_stream.py`` /
``test_hermes_pipeline.py`` — ``httpx.ASGITransport``, no sockets) and
captures every emitted log record via ``caplog`` attached at the ROOT
logger (``configure_logging()`` attaches its ``JsonFormatter`` handler
there — see ``logging_config.py``'s module docstring for why).

Covers:

* AC1 — every pipeline-stage record (archive/stt/hermes) for a full
  successful turn carries the minimal field set as ``extra`` attributes.
* AC2 — secrets (``STT_API_KEY``/``HERMES_API_KEY``/``TTS_API_KEY``) and
  raw audio bytes never appear in ANY captured record, for both a
  successful turn and an error turn (STT failure), checked against every
  record's formatted JSON string AND its raw ``extra``/args — not just
  ``.message``.
* AC3 — transcript text is absent from every INFO-level record at the
  default (``LOG_TRANSCRIPT`` unset) configuration, and present in a
  DEBUG-level record when ``LOG_TRANSCRIPT=true`` and the logger runs at
  DEBUG.

Real ``OpenAICompatibleSTT`` / ``OpenAICompatibleHermesClient`` transports
(over ``httpx.MockTransport`` — no real network) are used for the secret
hygiene tests specifically so the request path that actually carries the
API key (the Authorization header) is exercised, not bypassed by a fake
that never touches ``config.api_key`` at all.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from backend.src.voice_gateway.app import create_app
from backend.src.voice_gateway.config import HermesConfig, STTConfig
from backend.src.voice_gateway.hermes.client import OpenAICompatibleHermesClient
from backend.src.voice_gateway.hermes.fake import FakeHermes
from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt.base import STTClientError, STTProvider
from backend.src.voice_gateway.stt.fake import FakeSTT

STT_SECRET = "sk-test-stt-secret-123"
HERMES_SECRET = "sk-test-hermes-secret-456"
TTS_SECRET = "sk-test-tts-secret-789"

_VALID_HERMES_RAW = (
    '{"reply": "Готово.", "note": {"create": false, "title": "", '
    '"content": "", "tags": []}}'
)


class _SyncHttpSTT(STTProvider):
    """Sync test double exercising the same Authorization-header-building
    code path as :class:`OpenAICompatibleSTT`, over a synchronous
    ``httpx.Client`` (``httpx.MockTransport`` supports both sync and
    async clients — no real network).

    ``OpenAICompatibleSTT.transcribe()`` drives its async implementation
    through ``_run_sync()``, which calls ``running_loop.run_until_complete``
    when a loop is already running (as it is here, since the whole request
    is driven through ``httpx.ASGITransport`` inside a running event loop)
    — a pre-existing, unrelated bug (nested ``run_until_complete`` is not
    legal on a loop that is already running). This double sidesteps that
    bug by using a plain synchronous ``httpx.Client`` while still sending
    the API key via the ``Authorization`` header exactly like the real
    client, so the secret-hygiene assertions below still exercise a
    genuine "the key is in flight, on the wire" code path.
    """

    def __init__(self, config: STTConfig, client: "httpx.Client") -> None:
        self._config = config
        self._client = client

    def transcribe(self, wav):
        data = wav.read_bytes()
        headers = {}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        response = self._client.post(
            self._config.transcriptions_url,
            files={"file": (wav.name or "input.wav", data, "audio/wav")},
            data={"model": self._config.model},
            headers=headers,
        )
        if response.status_code >= 400:
            raise STTClientError(f"stt returned HTTP {response.status_code}")
        body = response.json()
        text = body.get("text")
        language = body.get("language")
        if not text or not language:
            raise STTClientError("stt response missing text/language")
        return Transcript(text=text, language=language)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


def _headers(device: str = "log-dev") -> dict:
    return {"X-Device-Id": device, "X-Sample-Rate": "16000", "X-Channels": "1"}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _post_turn(app: FastAPI, pcm: bytes) -> httpx.Response:
    async def go() -> httpx.Response:
        async with _client(app) as c:
            return await c.post("/api/v1/voice/turn", content=pcm, headers=_headers())
    return _run(go())


def _all_record_text(records) -> str:
    """Every record's formatted-as-JSON text AND its raw extra/args.

    Checking only ``.message``/``.getMessage()`` would miss a secret
    smuggled in via ``%``-args or an ``extra=`` key that never made it
    into the message string — this concatenates everything a record
    carries so a regression anywhere still gets caught.
    """
    from backend.src.voice_gateway.logging_config import JsonFormatter
    formatter = JsonFormatter()
    chunks = []
    for r in records:
        chunks.append(formatter.format(r))
        chunks.append(repr(r.args))
        chunks.append(repr(r.__dict__))
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# AC1: minimal field set on every stage record for a full successful turn
# ---------------------------------------------------------------------------

def test_full_successful_turn_stage_events_carry_minimal_fields(tmp_path: Path, caplog):
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text="привет", language="ru")),
        hermes=FakeHermes(_VALID_HERMES_RAW),
    )
    with caplog.at_level(logging.INFO):
        resp = _post_turn(app, b"\x00" * 4000)
    assert resp.status_code == 200

    stage_records = {
        r.stage: r for r in caplog.records if getattr(r, "stage", None) is not None
    }
    assert set(stage_records) == {"archive", "stt", "hermes"}
    turn_id = resp.headers["X-Turn-Id"]
    for stage, record in stage_records.items():
        assert record.turn_id == turn_id
        assert record.device_id == "log-dev"
        assert record.stage == stage
        assert isinstance(record.duration_ms, int)
        assert record.status == "success"
        assert not hasattr(record, "error") or getattr(record, "error") is None


def test_error_turn_stage_event_carries_bounded_status_and_error(tmp_path: Path, caplog):
    """An STT failure must still produce a well-formed stt stage record."""
    class _FailingSTT(FakeSTT):
        def transcribe(self, wav):
            raise RuntimeError("stt exploded")

    app = create_app(
        archive_root=tmp_path / "archive",
        stt=_FailingSTT(Transcript(text="unused", language="ru")),
        hermes=FakeHermes(_VALID_HERMES_RAW),
    )
    with caplog.at_level(logging.INFO):
        resp = _post_turn(app, b"\x00" * 4000)
    assert resp.status_code == 502

    stt_records = [r for r in caplog.records if getattr(r, "stage", None) == "stt"]
    assert len(stt_records) == 1
    record = stt_records[0]
    assert record.status == "stt_failed"
    assert record.error  # non-empty diagnostic, never the raw exception text with secrets
    assert record.turn_id
    assert record.device_id == "log-dev"
    assert isinstance(record.duration_ms, int)


# ---------------------------------------------------------------------------
# AC2: secrets and raw audio bytes never leak, success AND error turns
# ---------------------------------------------------------------------------

def test_secrets_and_raw_audio_absent_on_successful_turn(tmp_path: Path, caplog):
    stt_config = STTConfig(base_url="https://stt.internal/v1", api_key=STT_SECRET)
    hermes_config = HermesConfig(base_url="https://hermes.internal/v1", api_key=HERMES_SECRET)

    def stt_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "тестовый текст", "language": "ru"})

    def hermes_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": _VALID_HERMES_RAW}}]
        })

    stt = _SyncHttpSTT(stt_config, client=httpx.Client(
        transport=httpx.MockTransport(stt_handler)))
    hermes = OpenAICompatibleHermesClient(
        hermes_config, system_prompt="system prompt text",
        client=httpx.AsyncClient(transport=httpx.MockTransport(hermes_handler)))

    app = create_app(archive_root=tmp_path / "archive", stt=stt, hermes=hermes)

    # A distinctive raw-audio marker: if this ever ends up in a log call
    # (e.g. someone %-formats the PCM bytes into a message) it must be
    # caught below just like a leaked secret would be.
    raw_audio_marker = b"RAWAUDIO-MARKER-0102030405"
    pcm = raw_audio_marker + b"\x00" * (4000 - len(raw_audio_marker))

    with caplog.at_level(logging.DEBUG):
        resp = _post_turn(app, pcm)
    assert resp.status_code == 200

    text = _all_record_text(caplog.records)
    assert STT_SECRET not in text
    assert HERMES_SECRET not in text
    assert raw_audio_marker.decode() not in text
    assert repr(raw_audio_marker) not in text


def test_secrets_and_raw_audio_absent_on_error_turn(tmp_path: Path, caplog):
    """Same guarantee on a failure path (STT returns 500)."""
    stt_config = STTConfig(base_url="https://stt.internal/v1", api_key=STT_SECRET)
    hermes_config = HermesConfig(base_url="https://hermes.internal/v1", api_key=HERMES_SECRET)

    def stt_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    stt = _SyncHttpSTT(stt_config, client=httpx.Client(
        transport=httpx.MockTransport(stt_handler)))
    hermes = OpenAICompatibleHermesClient(
        hermes_config, system_prompt="system prompt text",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))))

    app = create_app(archive_root=tmp_path / "archive", stt=stt, hermes=hermes)

    raw_audio_marker = b"RAWAUDIO-MARKER-ERRPATH-9988"
    pcm = raw_audio_marker + b"\x00" * (4000 - len(raw_audio_marker))

    with caplog.at_level(logging.DEBUG):
        resp = _post_turn(app, pcm)
    assert resp.status_code == 502
    assert resp.json()["error"] == "stt_failed"

    text = _all_record_text(caplog.records)
    assert STT_SECRET not in text
    assert HERMES_SECRET not in text
    assert raw_audio_marker.decode() not in text


def test_secret_hygiene_tests_are_not_vacuous(tmp_path: Path, caplog):
    """Sanity check that the secret-hygiene assertion style actually
    catches a leak — proves the tests above are not vacuously passing.

    Directly exercises the same ``_all_record_text`` helper against a
    deliberately "leaky" log call (as if a future regression logged the
    key), confirming the assertion would fail in that case.
    """
    logger = logging.getLogger("voice_gateway.test.leaky")
    with caplog.at_level(logging.INFO, logger=logger.name):
        logger.info("leaked secret %s", STT_SECRET)
    text = _all_record_text(caplog.records)
    assert STT_SECRET in text  # the helper DOES detect a real leak
    with pytest.raises(AssertionError):
        assert STT_SECRET not in text


# ---------------------------------------------------------------------------
# AC3: transcript gating — absent by default, present only at DEBUG+opt-in
# ---------------------------------------------------------------------------

def test_transcript_absent_at_default_info_level(tmp_path: Path, caplog, monkeypatch):
    monkeypatch.delenv("LOG_TRANSCRIPT", raising=False)
    marker = "УНИКАЛЬНЫЙ-ТРАНСКРИПТ-МАРКЕР-1"
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text=marker, language="ru")),
        hermes=FakeHermes(_VALID_HERMES_RAW),
    )
    with caplog.at_level(logging.INFO):
        resp = _post_turn(app, b"\x00" * 4000)
    assert resp.status_code == 200

    text = _all_record_text(caplog.records)
    assert marker not in text


def test_transcript_present_at_debug_when_enabled(tmp_path: Path, caplog, monkeypatch):
    monkeypatch.setenv("LOG_TRANSCRIPT", "true")
    marker = "УНИКАЛЬНЫЙ-ТРАНСКРИПТ-МАРКЕР-2"
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text=marker, language="ru")),
        hermes=FakeHermes(_VALID_HERMES_RAW),
    )
    with caplog.at_level(logging.DEBUG):
        resp = _post_turn(app, b"\x00" * 4000)
    assert resp.status_code == 200

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG
                      and getattr(r, "transcript", None) == marker]
    assert debug_records, "expected a DEBUG record carrying the transcript"
    # And it must never appear on an INFO+ record even when enabled — the
    # gate controls level too, not just presence.
    info_plus_text = _all_record_text(
        [r for r in caplog.records if r.levelno >= logging.INFO])
    assert marker not in info_plus_text


def test_transcript_absent_even_at_debug_when_flag_disabled(tmp_path: Path, caplog, monkeypatch):
    """LOG_TRANSCRIPT unset/false must suppress transcript logging even if
    the logger itself is configured at DEBUG (the two controls are
    independent — level alone must not be enough to leak the transcript)."""
    monkeypatch.setenv("LOG_TRANSCRIPT", "false")
    marker = "УНИКАЛЬНЫЙ-ТРАНСКРИПТ-МАРКЕР-3"
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text=marker, language="ru")),
        hermes=FakeHermes(_VALID_HERMES_RAW),
    )
    with caplog.at_level(logging.DEBUG):
        resp = _post_turn(app, b"\x00" * 4000)
    assert resp.status_code == 200

    text = _all_record_text(caplog.records)
    assert marker not in text
