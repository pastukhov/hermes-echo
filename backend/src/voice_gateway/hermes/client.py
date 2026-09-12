"""OpenAI-compatible Hermes client over httpx (ТЗ sections 16, 21).

Concrete transport for the :class:`~backend.src.voice_gateway.hermes.base.HermesClient`
contract. ``complete`` sends the STT transcript to the endpoint's
``/chat/completions`` and returns the RAW response text from
``choices[0].message.content`` — structured parsing and repair live in
:mod:`voice_gateway.hermes.validation`, not here.

Timeout policy (ТЗ section 31): a single ``HERMES_TIMEOUT`` (default 120 s)
covers the whole request. Retries are intentionally absent — a voice turn
is one-shot and unbounded retry is forbidden by ТЗ section 24.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.src.voice_gateway.config import HermesConfig
from backend.src.voice_gateway.hermes.base import HermesClient, HermesClientError

logger = logging.getLogger(__name__)


class OpenAICompatibleHermesClient(HermesClient):
    """Talks to any OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(
        self,
        config: HermesConfig,
        system_prompt: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not system_prompt or not system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty string")
        self._config = config
        self._system_prompt = system_prompt
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout)
        )
        self._owns_client = client is None

    async def complete(self, transcript: str) -> str:
        """Send ``transcript`` to Hermes and return the raw response text.

        Raises :class:`HermesClientError` for transport failures, timeouts,
        non-2xx responses and empty/unusable responses (pipeline maps this
        to status ``hermes_failed``, ТЗ section 32).
        """
        text = transcript.strip()
        if not text:
            raise HermesClientError("empty transcript, nothing to send to Hermes")

        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": text},
            ],
        }
        try:
            response = await self._client.post(
                self._config.chat_completions_url,
                json=payload,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise HermesClientError(
                f"hermes request failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            logger.warning(
                "hermes returned HTTP %s", response.status_code
            )
            raise HermesClientError(
                f"hermes returned HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise HermesClientError(
                "hermes returned a non-JSON response body"
            ) from exc

        try:
            content: Any = (
                body["choices"][0]["message"]["content"]
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise HermesClientError(
                "hermes response is missing choices[0].message.content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise HermesClientError("hermes returned empty content")
        return content

    async def aclose(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        if self._owns_client:
            await self._client.aclose()
            self._owns_client = False

    async def __aenter__(self) -> "OpenAICompatibleHermesClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
