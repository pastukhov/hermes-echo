"""Tests for the OpenAI-compatible STT client (ТЗ sections 20, 31, 33, 51.13).

Uses ``httpx.MockTransport`` — no real network. Covers request shape
(endpoint, multipart file+model, auth header), successful parse to
``Transcript``, every failure path that must surface as
``STTClientError`` (pipeline maps to ``stt_failed``, ТЗ section 32),
explicit timeouts on every outbound call (ТЗ section 51.13), and the
guarantee that the API key never leaks into logs, exception text,
serialized requests, or response diagnostics (ТЗ section 33).
"""
import asyncio
import logging
from pathlib import Path

import httpx
import pytest

from backend.src.voice_gateway.config import STTConfig
from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt import STTClientError, STTProvider
from backend.src.voice_gateway.stt.client import OpenAICompatibleSTT

API_KEY = "sekret"
WAV_BYTES = b"RIFF....WAVE....fmt...."


def _config(**overrides) -> STTConfig:
    base = {
        "base_url": "https://stt.internal",
        "api_key": API_KEY,
        "model": "whisper-1",
        "timeout": 60.0,
    }
    base.update(overrides)
    return STTConfig(**base)


def _make_client(
    transport: httpx.MockTransport,
    config: STTConfig | None = None,
) -> OpenAICompatibleSTT:
    injected = httpx.AsyncClient(transport=transport)
    return OpenAICompatibleSTT(config or _config(), client=injected)


def _ok_response(text: str, language: str | None = "en") -> httpx.Response:
    body = {"text": text}
    if language is not None:
        body["language"] = language
    return httpx.Response(200, json=body)


def _write_wav(tmp_path: Path) -> Path:
    wav = tmp_path / "input.wav"
    wav.write_bytes(WAV_BYTES)
    return wav


def _run(coro):
    return asyncio.run(coro)


class TestSuccessfulRequest:
    def test_parses_transcript(self, tmp_path):
        wav = _write_wav(tmp_path)
        transport = httpx.MockTransport(
            lambda req: _ok_response("Привет, мир.", "ru")
        )
        client = _make_client(transport)
        result = client.transcribe(wav)
        assert result == Transcript(text="Привет, мир.", language="ru")

    def test_request_shape(self, tmp_path):
        wav = _write_wav(tmp_path)
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = request.content
            return _ok_response("ok")

        client = _make_client(httpx.MockTransport(handler))
        client.transcribe(wav)

        # Endpoint: {base}/audio/transcriptions (ТЗ section 20)
        assert seen["url"] == "https://stt.internal/audio/transcriptions"
        # Method: POST (ТЗ section 20)
        assert seen["method"] == "POST"
        # Bearer auth from STT_API_KEY
        assert seen["headers"]["authorization"] == f"Bearer {API_KEY}"
        # Multipart body carries file bytes + model field
        assert WAV_BYTES in seen["body"]
        assert b"whisper-1" in seen["body"]
        assert b'name="file"' in seen["body"]
        assert b'name="model"' in seen["body"]
        assert b'name="response_format"' in seen["body"]
        assert b'verbose_json' in seen["body"]
        # The file part declares audio/wav (ТЗ section 20)
        assert b"audio/wav" in seen["body"]

    def test_no_api_key_means_no_auth_header(self, tmp_path):
        wav = _write_wav(tmp_path)
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            return _ok_response("ok")

        client = _make_client(
            httpx.MockTransport(handler), config=_config(api_key="")
        )
        client.transcribe(wav)
        assert "authorization" not in seen["headers"]

    def test_missing_file_raises_before_http(self):
        called = []

        def handler(request: httpx.Request) -> httpx.Response:
            called.append(1)
            return _ok_response("ok")

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(STTClientError, match="cannot read"):
            client.transcribe(Path("/nonexistent/never.wav"))
        assert called == []


class TestExplicitTimeouts:
    """ТЗ section 51.13: every outbound call carries an explicit timeout."""

    def test_client_built_with_default_config_uses_60s(self):
        from backend.src.voice_gateway.config import (
            DEFAULT_STT_TIMEOUT,
            STTConfig,
        )

        config = STTConfig(base_url="https://stt.internal")
        assert config.timeout == DEFAULT_STT_TIMEOUT == 60.0
        client = OpenAICompatibleSTT(config)
        timeout = client._client.timeout
        assert timeout == httpx.Timeout(60.0)
        # The single bound applies to every phase httpx supports.
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
            60.0,
            60.0,
            60.0,
            60.0,
        )
        # Never fall back to httpx's implicit 5s default.
        assert timeout != httpx.Timeout(5.0)

    def test_configured_timeout_is_propagated(self):
        client = OpenAICompatibleSTT(_config(timeout=90.5))
        timeout = client._client.timeout
        assert timeout == httpx.Timeout(90.5)
        assert timeout.read == 90.5


class TestFailurePaths:
    def test_http_500_raises(self, tmp_path):
        wav = _write_wav(tmp_path)
        transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
        client = _make_client(transport)
        with pytest.raises(STTClientError, match="500"):
            client.transcribe(wav)

    def test_non_json_body_raises(self, tmp_path):
        wav = _write_wav(tmp_path)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text="nope"))
        client = _make_client(transport)
        with pytest.raises(STTClientError, match="non-JSON"):
            client.transcribe(wav)

    def test_non_object_body_raises(self, tmp_path):
        wav = _write_wav(tmp_path)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=["a"]))
        client = _make_client(transport)
        with pytest.raises(STTClientError, match="JSON object"):
            client.transcribe(wav)

    def test_missing_text_field_raises(self, tmp_path):
        wav = _write_wav(tmp_path)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
        client = _make_client(transport)
        with pytest.raises(STTClientError, match="text"):
            client.transcribe(wav)

    def test_empty_text_raises(self, tmp_path):
        wav = _write_wav(tmp_path)
        transport = httpx.MockTransport(lambda req: _ok_response("   "))
        client = _make_client(transport)
        with pytest.raises(STTClientError, match="text"):
            client.transcribe(wav)

    def test_missing_language_field_raises(self, tmp_path):
        wav = _write_wav(tmp_path)
        transport = httpx.MockTransport(
            lambda req: _ok_response("hello", language=None)
        )
        client = _make_client(transport)
        with pytest.raises(STTClientError, match="language"):
            client.transcribe(wav)

    def test_non_string_language_raises(self, tmp_path):
        wav = _write_wav(tmp_path)
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"text": "hello", "language": 42})
        )
        client = _make_client(transport)
        with pytest.raises(STTClientError, match="language"):
            client.transcribe(wav)

    def test_timeout_raises_stt_client_error(self, tmp_path, caplog):
        wav = _write_wav(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        client = _make_client(httpx.MockTransport(handler))
        with caplog.at_level(logging.WARNING), pytest.raises(
            STTClientError, match="failed"
        ) as excinfo:
            client.transcribe(wav)
        # API key must not leak into the exception text (ТЗ section 33)
        assert API_KEY not in str(excinfo.value)
        assert API_KEY not in repr(excinfo.value)
        assert API_KEY not in caplog.text

    def test_connection_error_raises(self, tmp_path):
        wav = _write_wav(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(STTClientError, match="failed"):
            client.transcribe(wav)


class TestSecretHygiene:
    def test_api_key_not_in_any_exception_text(self, tmp_path):
        """The API key never appears in exception text (ТЗ section 33)."""
        wav = _write_wav(tmp_path)
        # A 401 path that echoes back the key in the body must not leak it.
        transport = httpx.MockTransport(
            lambda req: httpx.Response(401, json={"error": f"bad key {API_KEY}"})
        )
        client = _make_client(transport)
        with pytest.raises(STTClientError) as excinfo:
            client.transcribe(wav)
        assert API_KEY not in str(excinfo.value)
        # And the request URL itself must never carry the key.
        assert API_KEY not in _config().transcriptions_url

    def test_key_only_in_auth_header_never_in_logs_or_serialized_request(
        self, tmp_path, caplog
    ):
        """ТЗ section 33: no key in logs, URLs, or serialized request body."""
        wav = _write_wav(tmp_path)
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content
            return httpx.Response(401, text="unauthorized")

        client = _make_client(httpx.MockTransport(handler))
        with caplog.at_level(logging.WARNING):
            with pytest.raises(STTClientError, match="401"):
                client.transcribe(wav)

        # The key belongs only in the Authorization header — never in the
        # URL, never in the serialized multipart body, never in logs.
        assert API_KEY not in seen["url"]
        assert API_KEY.encode() not in seen["body"]
        assert API_KEY not in caplog.text


class TestLifecycleAndContract:
    def test_satisfies_stt_provider_contract(self):
        client = _make_client(httpx.MockTransport(lambda req: _ok_response("ok")))
        assert isinstance(client, STTProvider)
        assert STTProvider.__abstractmethods__ == frozenset({"transcribe"})

    def test_injected_client_not_closed_by_aclose(self):
        injected = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: _ok_response("ok"))
        )
        client = OpenAICompatibleSTT(_config(), client=injected)
        _run(client.aclose())
        assert injected.is_closed is False

    def test_owned_client_is_closed(self):
        client = OpenAICompatibleSTT(_config())
        _run(client.aclose())
        assert client._client.is_closed is True
        # idempotent
        _run(client.aclose())

    def test_async_context_manager(self):
        async def scenario() -> None:
            async with OpenAICompatibleSTT(_config()) as client:
                assert isinstance(client, STTProvider)
            assert client._client.is_closed is True

        _run(scenario())
