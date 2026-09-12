"""Vendor-neutral STT client contract and domain error (ТЗ section 20).

The concrete transport (OpenAI-compatible endpoint over httpx, see
:mod:`voice_gateway.stt.client`) is NOT part of this contract.
"""
from abc import ABC, abstractmethod
from pathlib import Path

from backend.src.voice_gateway.models import Transcript


class STTClientError(Exception):
    """Failure to call the STT endpoint at the transport level.

    Covers network errors, timeouts, non-2xx HTTP responses and
    empty/unusable response bodies. The pipeline maps this to status
    ``stt_failed`` (ТЗ section 32, ``ErrorCode.STT_FAILED``). Never
    includes the API key or request/response bodies (ТЗ section 33).
    """


class STTProvider(ABC):
    """Vendor-neutral speech-to-text contract.

    Input is a Path to an already finalized WAV file; the concrete
    transport and vendor are not part of this contract.
    """

    @abstractmethod
    def transcribe(self, wav: Path) -> Transcript:
        """Transcribe the WAV file at ``wav`` into a Transcript."""
