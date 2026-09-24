"""OpenAI-compatible STT client over httpx (ТЗ sections 20, 31).

Concrete transport for the :class:`~voice_gateway.stt.base.STTProvider`
contract. ``transcribe`` sends the WAV file to the endpoint's
``/audio/transcriptions`` endpoint as a multipart form (fields ``file``
and ``model``, ТЗ section 20) and returns a :class:`Transcript`.

The ``STTProvider`` contract is synchronous (``transcribe(wav: Path) ->
Transcript``), while the real transport is async httpx per project
convention (ТЗ section 51.13: every external call carries an explicit
timeout; see :mod:`voice_gateway.hermes.client`). The async call is
driven from ``transcribe`` through a private loop helper.

Timeout policy (ТЗ section 31): a single ``STT_TIMEOUT`` (default 60 s)
covers the whole request via ``httpx.Timeout``. The API key is never
logged, put into exception text, or embedded in URLs (ТЗ section 33).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from backend.src.voice_gateway.config import STTConfig
from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt.base import STTClientError, STTProvider

logger = logging.getLogger(__name__)

_WAV_CONTENT_TYPE = "audio/wav"


def _run_sync(coro):
    """Run an async ``coro`` from a synchronous context.

    Uses the running loop's ``run_until_complete`` when one exists
    (embedded use); otherwise a fresh short-lived loop.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return running.run_until_complete(coro)  # pragma: no cover


class OpenAICompatibleSTT(STTProvider):
    """Talks to any OpenAI-compatible ``/audio/transcriptions`` endpoint."""

    def __init__(
        self,
        config: STTConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        # httpx.Timeout(float) applies the same bound to every phase the
        # client supports (connect/read/write/pool) — ТЗ section 51.13.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout)
        )
        self._owns_client = client is None

    def transcribe(self, wav: Path) -> Transcript:
        """Transcribe the WAV file at ``wav`` (sync ABC method)."""
        return _run_sync(self._transcribe_async(wav))

    async def transcribe_async(self, wav: Path) -> Transcript:
        """Transcribe within an existing event loop without nesting loops."""
        return await self._transcribe_async(wav)

    async def _transcribe_async(self, wav: Path) -> Transcript:
        try:
            data = Path(wav).read_bytes()
        except OSError as exc:
            raise STTClientError(f"cannot read WAV file: {exc}") from exc

        headers = {}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        try:
            response = await self._client.post(
                self._config.transcriptions_url,
                files={
                    "file": (
                        Path(wav).name or "input.wav",
                        data,
                        _WAV_CONTENT_TYPE,
                    )
                },
                data={"model": self._config.model,
                      "response_format": "verbose_json"},
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise STTClientError(f"stt request failed: {exc.__class__.__name__}") from exc

        if response.status_code >= 400:
            logger.warning("stt returned HTTP %s", response.status_code)
            raise STTClientError(f"stt returned HTTP {response.status_code}")
        try:
            body = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise STTClientError("stt returned a non-JSON response body") from exc
        if not isinstance(body, dict):
            raise STTClientError("stt response body is not a JSON object")
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise STTClientError("stt response is missing a non-empty 'text' field")
        language = body.get("language")
        if not isinstance(language, str) or not language.strip():
            raise STTClientError("stt response is missing a non-empty 'language' field")
        return Transcript(text=text, language=language)

    async def aclose(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        if self._owns_client:
            await self._client.aclose()
            self._owns_client = False

    async def __aenter__(self) -> "OpenAICompatibleSTT":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
