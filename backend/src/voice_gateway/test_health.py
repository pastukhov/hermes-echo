"""Unit tests for the health endpoints (ТЗ §35, task t_82d5f45a).

Covers both endpoints in the normal case and with an unavailable
ARCHIVE_PATH (missing directory, read-only directory, missing required
config). Mirrors test_app_stream.py conventions: ASGITransport, one
event loop per test, no pytest-asyncio.
"""
import asyncio
import os
from pathlib import Path

import httpx
from fastapi import FastAPI

from backend.src.voice_gateway.app import create_app
from backend.src.voice_gateway.hermes.fake import FakeHermes
from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt.fake import FakeSTT

_VALID_HERMES_RAW = (
    '{"reply": "Готово.", "note": {"create": false, "title": "", '
    '"content": "", "tags": []}}'
)


def _make_app(tmp_path: Path, archive_root: Path | None) -> FastAPI:
    return create_app(
        archive_root=archive_root if archive_root is not None else tmp_path / "archive",
        stt=FakeSTT(Transcript(text="тестовая расшифровка", language="ru")),
        hermes=FakeHermes(_VALID_HERMES_RAW),
    )


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


def _run(loop: asyncio.AbstractEventLoop, coro):
    """Run ``coro`` on a fresh event loop and close the loop afterwards."""
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _get(app: FastAPI, path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        async with _client(app) as client:
            return await client.get(path)

    return _run(asyncio.new_event_loop(), run())


def _valid_env(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("STT_BASE_URL", "http://127.0.0.1:9/v1")


# --- /health/live ---------------------------------------------------------

def test_live_always_200(tmp_path: Path, monkeypatch) -> None:
    # Liveness is the process check only: no config, no filesystem, no
    # network. Even with the archive dir missing and no required env at
    # all it must answer 200 (ТЗ §35).
    monkeypatch.delenv("HERMES_BASE_URL", raising=False)
    monkeypatch.delenv("STT_BASE_URL", raising=False)
    app = _make_app(tmp_path, archive_root=tmp_path / "does-not-exist")

    resp = _get(app, "/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "voice-gateway"


# --- /health/ready: normal case ------------------------------------------

def test_ready_ok(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    _valid_env(monkeypatch)
    app = _make_app(tmp_path, archive_root=archive)

    resp = _get(app, "/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["config"] == "ok"
    assert body["checks"]["archive"] == "ok"
    # notes is not configured here — reported as disabled, never blocking
    assert body["checks"]["notes"] in {"disabled", "not_writable", "writable"}


def test_ready_ok_without_notes_vault(tmp_path: Path, monkeypatch) -> None:
    # OBSIDIAN_VAULT_PATH unset: notes storage is optional (ТЗ §25), so
    # the missing vault must NOT flip readiness to 503.
    archive = tmp_path / "archive"
    archive.mkdir()
    _valid_env(monkeypatch)
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    app = _make_app(tmp_path, archive_root=archive)

    resp = _get(app, "/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --- /health/ready: unavailable ARCHIVE_PATH ------------------------------

def test_ready_503_archive_missing(tmp_path: Path, monkeypatch) -> None:
    # temp dir without write rights / not existing at all
    _valid_env(monkeypatch)
    app = _make_app(tmp_path, archive_root=tmp_path / "no-such-archive")

    resp = _get(app, "/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["config"] == "ok"
    assert body["checks"]["archive"].startswith("error:")


def test_ready_503_archive_read_only(tmp_path: Path, monkeypatch) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:  # running as root
        import pytest
        pytest.skip("running as root: permission bits are ignored")
    archive = tmp_path / "archive-readonly"
    archive.mkdir()
    _valid_env(monkeypatch)
    app = _make_app(tmp_path, archive_root=archive)
    try:
        archive.chmod(0o555)  # read/execute only — no writes
        resp = _get(app, "/health/ready")
    finally:
        archive.chmod(0o755)

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["archive"].startswith("error:")


def test_ready_503_config_missing(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.delenv("HERMES_BASE_URL", raising=False)
    monkeypatch.setenv("STT_BASE_URL", "http://127.0.0.1:9/v1")
    app = _make_app(tmp_path, archive_root=archive)

    resp = _get(app, "/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert "HERMES_BASE_URL" in body["checks"]["config"]
    assert body["checks"]["archive"] == "ok"


# --- /health/ready must never touch the network ---------------------------

def test_ready_no_network_to_stt_hermes_tts(tmp_path: Path, monkeypatch) -> None:
    # The required roots point at 127.0.0.1:9 (closed port). If readiness
    # made even one network call to STT/Hermes/TTS it would hang or fail —
    # 200 here proves readiness is network-free (no restart loop, ТЗ §35).
    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.setenv("HERMES_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("STT_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("TTS_BASE_URL", "http://127.0.0.1:9/v1/audio/speech")
    app = _make_app(tmp_path, archive_root=archive)

    resp = _get(app, "/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --- notes storage: writable/optional (ТЗ §25) -----------------------------

def test_ready_notes_not_writable_does_not_block(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    vault = tmp_path / "vault"          # exists, writable
    vault.mkdir()
    _valid_env(monkeypatch)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "missing-vault"))
    app = _make_app(tmp_path, archive_root=archive)

    resp = _get(app, "/health/ready")
    # missing vault: reported, but never a 503 (writable/optional, ТЗ §25)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["notes"] == "not_writable"


def test_ready_notes_writable_reported(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    _valid_env(monkeypatch)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    app = _make_app(tmp_path, archive_root=archive)

    resp = _get(app, "/health/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"]["notes"] == "writable"
