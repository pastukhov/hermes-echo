"""Vendor-neutral Hermes client contract (ТЗ section 16, 21).

The concrete transport (OpenAI-compatible endpoint over httpx, see card
t_0b5f3899) is NOT part of this contract. ``complete`` receives the STT
transcript text and returns the RAW response text; structured parsing,
repair and fallback live in ``voice_gateway.hermes.validation``.
"""
from abc import ABC, abstractmethod


class HermesClientError(Exception):
    """Failure to call Hermes at the transport level.

    Covers network errors, timeouts and non-2xx HTTP responses. The
    pipeline maps this to status ``hermes_failed`` (ТЗ section 32).
    """


class HermesClient(ABC):
    """Contract for the Hermes stage: transcript in, raw response out."""

    @abstractmethod
    async def complete(self, transcript: str) -> str:
        """Send ``transcript`` to Hermes and return the raw response text."""
