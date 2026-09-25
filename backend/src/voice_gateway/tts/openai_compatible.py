"""OpenAI-compatible TTS client over httpx (ТЗ sections 28, 29, 31, 33).

Concrete transport for the
:class:`~backend.src.voice_gateway.tts.base.TTSProvider` contract.
``synthesize`` POSTs to the configured (already-full) TTS endpoint URL
with a JSON body of ``model``/``voice``/``input`` and writes the raw WAV
response body to ``out_path``.

Unlike the STT and Hermes transports, this contract's ``synthesize`` is
synchronous end to end, so the adapter is built on ``httpx.Client``
rather than ``httpx.AsyncClient`` (ТЗ architecture note: sync `httpx.Client`
injected via constructor so tests can use ``httpx.MockTransport``).

Timeout policy (ТЗ section 31): a single ``TTS_TIMEOUT`` (default 60 s)
covers the whole request via ``httpx.Timeout``. Retries are intentionally
absent. The API key is never logged, put into exception text, or
embedded in URLs (ТЗ section 33).
"""
from __future__ import annotations

import logging
import time
import wave
from io import BytesIO
from pathlib import Path

import httpx

from backend.common.error_codes import ErrorCode
from backend.src.voice_gateway.logging_config import log_stage_event
from backend.src.voice_gateway.models import TTSResult
from backend.src.voice_gateway.tts.base import TTSProvider, TTSProviderError
from backend.src.voice_gateway.tts.config import TTSConfig

logger = logging.getLogger(__name__)

_SUPPORTED_SAMPLE_RATES = (16000, 24000)


class OpenAICompatibleTTS(TTSProvider):
    """Talks to any OpenAI-compatible TTS endpoint (``base_url`` is the
    full endpoint URL — no suffix is appended, ТЗ section 33)."""

    def __init__(
        self,
        config: TTSConfig,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        # httpx.Timeout(float) applies the same bound to every phase the
        # client supports (connect/read/write/pool) — ТЗ section 31.
        self._client = client or httpx.Client(timeout=httpx.Timeout(config.timeout))
        self._owns_client = client is None

    def synthesize(
        self,
        text: str,
        out_path: Path,
        *,
        turn_id: str | None = None,
        device_id: str | None = None,
    ) -> TTSResult:
        """Synthesize ``text`` into the WAV file at ``out_path``.

        Raises :class:`TTSProviderError` for transport failures, timeouts,
        non-2xx responses, non-WAV bodies and WAV bodies whose sample rate
        is not one of the supported rates (pipeline maps this to status
        ``tts_failed``, ТЗ section 32).

        ``turn_id``/``device_id`` are optional, keyword-only, and default
        to ``None`` — this class has no turn context of its own today (TTS
        is not yet wired into ``app.py``'s ``voice_turn`` handler, ТЗ §33
        card scope), so they are accepted here purely so a future call
        site (once TTS is wired in) can pass the turn's identifiers through
        for structured logging (:func:`log_stage_event`) without breaking
        this method's existing signature/callers.
        """
        stage_start = time.perf_counter()
        headers = {}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        payload = {
            "model": self._config.model,
            "voice": self._config.voice,
            "input": text,
        }
        try:
            response = self._client.post(
                self._config.base_url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(self._config.timeout),
            )
        except httpx.HTTPError as exc:
            log_stage_event(
                logger, "tts", turn_id=turn_id, device_id=device_id,
                duration_ms=int(round((time.perf_counter() - stage_start) * 1000)),
                status=ErrorCode.TTS_FAILED.value,
                error=exc.__class__.__name__,
            )
            raise TTSProviderError(
                f"tts request failed: {exc.__class__.__name__}"
            ) from exc

        if response.status_code >= 400:
            logger.warning("tts returned HTTP %s", response.status_code)
            log_stage_event(
                logger, "tts", turn_id=turn_id, device_id=device_id,
                duration_ms=int(round((time.perf_counter() - stage_start) * 1000)),
                status=ErrorCode.TTS_FAILED.value,
                error=f"tts returned HTTP {response.status_code}",
            )
            raise TTSProviderError(f"tts returned HTTP {response.status_code}")

        body = response.content
        if not body:
            log_stage_event(
                logger, "tts", turn_id=turn_id, device_id=device_id,
                duration_ms=int(round((time.perf_counter() - stage_start) * 1000)),
                status=ErrorCode.TTS_FAILED.value,
                error="tts returned an empty response body",
            )
            raise TTSProviderError("tts returned an empty response body")

        try:
            sample_rate = self._validate_wav(body)
        except TTSProviderError as exc:
            log_stage_event(
                logger, "tts", turn_id=turn_id, device_id=device_id,
                duration_ms=int(round((time.perf_counter() - stage_start) * 1000)),
                status=ErrorCode.TTS_FAILED.value,
                error=str(exc),
            )
            raise

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(body)
        log_stage_event(
            logger, "tts", turn_id=turn_id, device_id=device_id,
            duration_ms=int(round((time.perf_counter() - stage_start) * 1000)),
            status="success",
        )
        return TTSResult(wav_path=out_path, sample_rate=sample_rate)

    @staticmethod
    def _validate_wav(body: bytes) -> int:
        """Parse ``body`` as a WAV header and return its sample rate.

        Raises :class:`TTSProviderError` if the body is not a valid PCM16
        mono WAV, or its sample rate is unsupported. Never includes the
        raw audio bytes in the error (ТЗ section 33).
        """
        try:
            with wave.open(BytesIO(body), "rb") as wav:
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                sample_rate = wav.getframerate()
        except (wave.Error, EOFError) as exc:
            raise TTSProviderError(
                f"tts returned an invalid WAV response: {exc}"
            ) from exc

        if channels != 1 or sample_width != 2:
            raise TTSProviderError(
                "tts returned a WAV that is not PCM16 mono "
                f"(channels={channels}, sample_width={sample_width})"
            )
        if sample_rate not in _SUPPORTED_SAMPLE_RATES:
            raise TTSProviderError(
                f"tts returned an unsupported sample rate: {sample_rate}"
            )
        return sample_rate

    def close(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        if self._owns_client:
            self._client.close()
            self._owns_client = False

    def __enter__(self) -> "OpenAICompatibleTTS":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
