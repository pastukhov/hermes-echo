"""Tests for the OpenAI-compatible TTS client (ТЗ sections 28, 29, 31, 33).

Uses ``httpx.MockTransport`` — no real network. Covers request shape
(exact URL, POST, JSON fields model/voice/input, auth header), successful
synthesis to ``out_path`` with the sample rate read from the WAV header,
every failure path that must surface as ``TTSProviderError`` (pipeline
maps to ``tts_failed``, ТЗ section 32), explicit timeouts on every
outbound call (ТЗ section 31), and the guarantee that the API key never
leaks into logs, exception text, or serialized requests (ТЗ section 33).

WAV bytes used as fixtures are generated programmatically via stdlib
``wave`` — no binary files are committed.
"""
import io
import logging
import wave
from pathlib import Path

import httpx
import pytest

from backend.src.voice_gateway.models import TTSResult
from backend.src.voice_gateway.tts import TTSProvider, TTSProviderError
from backend.src.voice_gateway.tts.config import TTSConfig
from backend.src.voice_gateway.tts.openai_compatible import OpenAICompatibleTTS

API_KEY = "sekret"


def _make_wav_bytes(sample_rate: int, *, channels: int = 1, sample_width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00" * sample_width * 400)
    return buf.getvalue()


WAV_16K = _make_wav_bytes(16000)
WAV_24K = _make_wav_bytes(24000)


def _config(**overrides) -> TTSConfig:
    base = {
        "base_url": "https://tts.internal/v1/audio/speech",
        "api_key": API_KEY,
        "model": "tts-1",
        "voice": "alloy",
        "timeout": 60.0,
    }
    base.update(overrides)
    return TTSConfig(**base)


def _make_client(
    transport: httpx.MockTransport,
    config: TTSConfig | None = None,
) -> OpenAICompatibleTTS:
    injected = httpx.Client(transport=transport)
    return OpenAICompatibleTTS(config or _config(), client=injected)


def _wav_response(body: bytes = WAV_16K, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=body, headers={"content-type": "audio/wav"})


class TestSuccessfulRequest:
    def test_request_shape(self, tmp_path: Path):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = request.content
            return _wav_response()

        client = _make_client(httpx.MockTransport(handler))
        out_path = tmp_path / "out.wav"
        client.synthesize("Привет, мир.", out_path)

        assert seen["url"] == "https://tts.internal/v1/audio/speech"
        assert seen["method"] == "POST"
        assert seen["headers"]["authorization"] == f"Bearer {API_KEY}"

        import json

        payload = json.loads(seen["body"])
        assert payload == {
            "model": "tts-1",
            "voice": "alloy",
            "input": "Привет, мир.",
        }

    def test_base_url_used_verbatim_no_suffix_appended(self, tmp_path: Path):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _wav_response()

        client = _make_client(
            httpx.MockTransport(handler),
            config=_config(base_url="https://tts.internal/custom/path"),
        )
        client.synthesize("hi", tmp_path / "out.wav")
        assert seen["url"] == "https://tts.internal/custom/path"

    def test_no_api_key_means_no_auth_header(self, tmp_path: Path):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            return _wav_response()

        client = _make_client(
            httpx.MockTransport(handler), config=_config(api_key="")
        )
        client.synthesize("hi", tmp_path / "out.wav")
        assert "authorization" not in seen["headers"]

    def test_writes_wav_bytes_to_out_path(self, tmp_path: Path):
        transport = httpx.MockTransport(lambda req: _wav_response(WAV_16K))
        client = _make_client(transport)
        out_path = tmp_path / "nested" / "out.wav"
        result = client.synthesize("hi", out_path)

        assert out_path.exists()
        assert out_path.read_bytes() == WAV_16K
        assert result == TTSResult(wav_path=out_path, sample_rate=16000)

    def test_returns_sample_rate_from_header_16k(self, tmp_path: Path):
        transport = httpx.MockTransport(lambda req: _wav_response(WAV_16K))
        client = _make_client(transport)
        result = client.synthesize("hi", tmp_path / "out.wav")
        assert result.sample_rate == 16000

    def test_returns_sample_rate_from_header_24k(self, tmp_path: Path):
        transport = httpx.MockTransport(lambda req: _wav_response(WAV_24K))
        client = _make_client(transport)
        result = client.synthesize("hi", tmp_path / "out.wav")
        assert result.sample_rate == 24000


class TestExplicitTimeouts:
    """ТЗ section 31: every outbound TTS call carries an explicit timeout."""

    def test_client_built_with_default_config_uses_60s(self):
        from backend.src.voice_gateway.tts.config import DEFAULT_TTS_TIMEOUT

        config = TTSConfig(base_url="https://tts.internal/speech", model="m")
        assert config.timeout == DEFAULT_TTS_TIMEOUT == 60.0
        client = OpenAICompatibleTTS(config)
        timeout = client._client.timeout
        assert timeout == httpx.Timeout(60.0)
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
            60.0,
            60.0,
            60.0,
            60.0,
        )
        assert timeout != httpx.Timeout(5.0)

    def test_configured_timeout_is_propagated_to_request(self, tmp_path: Path):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["timeout"] = request.extensions.get("timeout")
            return _wav_response()

        transport = httpx.MockTransport(handler)
        client = _make_client(transport, config=_config(timeout=12.5))
        # The per-request timeout is explicit (httpx.Timeout(12.5)), not
        # relying on the client's own default.
        client.synthesize("hi", tmp_path / "out.wav")
        assert seen["timeout"] == {
            "connect": 12.5,
            "read": 12.5,
            "write": 12.5,
            "pool": 12.5,
        }


class TestFailurePaths:
    def test_http_500_raises(self, tmp_path: Path):
        transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
        client = _make_client(transport)
        with pytest.raises(TTSProviderError, match="500"):
            client.synthesize("hi", tmp_path / "out.wav")

    def test_http_401_raises(self, tmp_path: Path):
        transport = httpx.MockTransport(lambda req: httpx.Response(401, text="nope"))
        client = _make_client(transport)
        with pytest.raises(TTSProviderError, match="401"):
            client.synthesize("hi", tmp_path / "out.wav")

    def test_empty_body_raises(self, tmp_path: Path):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b""))
        client = _make_client(transport)
        with pytest.raises(TTSProviderError, match="empty"):
            client.synthesize("hi", tmp_path / "out.wav")

    def test_non_wav_body_raises(self, tmp_path: Path):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, content=b"not a wav file at all")
        )
        client = _make_client(transport)
        with pytest.raises(TTSProviderError, match="invalid WAV"):
            client.synthesize("hi", tmp_path / "out.wav")

    def test_non_json_error_body_does_not_require_json(self, tmp_path: Path):
        """A non-2xx binary/text body must not raise a JSON-parsing error."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(503, content=b"\x00\x01\x02")
        )
        client = _make_client(transport)
        with pytest.raises(TTSProviderError, match="503"):
            client.synthesize("hi", tmp_path / "out.wav")

    def test_stereo_wav_raises(self, tmp_path: Path):
        stereo = _make_wav_bytes(16000, channels=2)
        transport = httpx.MockTransport(lambda req: _wav_response(stereo))
        client = _make_client(transport)
        with pytest.raises(TTSProviderError, match="mono"):
            client.synthesize("hi", tmp_path / "out.wav")

    def test_wrong_sample_width_raises(self, tmp_path: Path):
        wide = _make_wav_bytes(16000, sample_width=1)
        transport = httpx.MockTransport(lambda req: _wav_response(wide))
        client = _make_client(transport)
        with pytest.raises(TTSProviderError, match="mono"):
            client.synthesize("hi", tmp_path / "out.wav")

    def test_unsupported_sample_rate_raises(self, tmp_path: Path):
        wrong_rate = _make_wav_bytes(8000)
        transport = httpx.MockTransport(lambda req: _wav_response(wrong_rate))
        client = _make_client(transport)
        with pytest.raises(TTSProviderError, match="sample rate"):
            client.synthesize("hi", tmp_path / "out.wav")

    def test_timeout_raises_tts_provider_error(self, tmp_path: Path, caplog):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        client = _make_client(httpx.MockTransport(handler))
        with caplog.at_level(logging.WARNING), pytest.raises(
            TTSProviderError, match="failed"
        ) as excinfo:
            client.synthesize("hi", tmp_path / "out.wav")
        assert API_KEY not in str(excinfo.value)
        assert API_KEY not in repr(excinfo.value)
        assert API_KEY not in caplog.text

    def test_connection_error_raises(self, tmp_path: Path):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(TTSProviderError, match="failed"):
            client.synthesize("hi", tmp_path / "out.wav")

    def test_output_file_not_written_on_failure(self, tmp_path: Path):
        transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
        client = _make_client(transport)
        out_path = tmp_path / "out.wav"
        with pytest.raises(TTSProviderError):
            client.synthesize("hi", out_path)
        assert not out_path.exists()


class TestSecretHygiene:
    def test_api_key_not_in_exception_text_on_error_body_echo(self, tmp_path: Path):
        """The API key never appears in exception text (ТЗ section 33)."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(401, text=f"bad key {API_KEY}")
        )
        client = _make_client(transport)
        with pytest.raises(TTSProviderError) as excinfo:
            client.synthesize("hi", tmp_path / "out.wav")
        assert API_KEY not in str(excinfo.value)
        assert API_KEY not in repr(excinfo.value)

    def test_key_only_in_auth_header_never_in_logs_or_body(
        self, tmp_path: Path, caplog
    ):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content
            return httpx.Response(401, text="unauthorized")

        client = _make_client(httpx.MockTransport(handler))
        with caplog.at_level(logging.WARNING):
            with pytest.raises(TTSProviderError, match="401"):
                client.synthesize("hi", tmp_path / "out.wav")

        assert API_KEY not in seen["url"]
        assert API_KEY.encode() not in seen["body"]
        assert API_KEY not in caplog.text

    def test_raw_wav_bytes_never_in_exception_text(self, tmp_path: Path):
        """A raw-bytes failure error must not embed the audio payload."""
        garbage = b"\x00\x01\x02garbage-not-a-wav\xff\xfe"
        transport = httpx.MockTransport(lambda req: _wav_response(garbage))
        client = _make_client(transport)
        with pytest.raises(TTSProviderError) as excinfo:
            client.synthesize("hi", tmp_path / "out.wav")
        assert garbage not in str(excinfo.value).encode(errors="ignore")


class TestLifecycleAndContract:
    def test_satisfies_tts_provider_contract(self):
        client = _make_client(httpx.MockTransport(lambda req: _wav_response()))
        assert isinstance(client, TTSProvider)
        assert TTSProvider.__abstractmethods__ == frozenset({"synthesize"})

    def test_injected_client_not_closed_by_close(self):
        injected = httpx.Client(transport=httpx.MockTransport(lambda req: _wav_response()))
        client = OpenAICompatibleTTS(_config(), client=injected)
        client.close()
        assert injected.is_closed is False

    def test_owned_client_is_closed(self):
        client = OpenAICompatibleTTS(_config())
        client.close()
        assert client._client.is_closed is True
        # idempotent
        client.close()

    def test_context_manager(self):
        with OpenAICompatibleTTS(_config()) as client:
            assert isinstance(client, TTSProvider)
        assert client._client.is_closed is True
