from abc import ABC, abstractmethod
from pathlib import Path

from backend.src.voice_gateway.models import TTSResult


class TTSProviderError(Exception):
    """Failure to call the TTS endpoint at the transport level.

    Covers network errors, timeouts, non-2xx HTTP responses, invalid WAV
    bodies and unsupported sample rates. The pipeline maps this to status
    ``tts_failed`` (ТЗ section 32). Never includes the API key, the
    Authorization header, response body text, raw audio bytes, or the
    full vendor response (ТЗ section 33).
    """


class TTSProvider(ABC):
    """Vendor-neutral text-to-speech contract.

    ``out_path`` is the path where the implementation writes the finalized
    WAV file; the concrete transport and vendor are not part of this
    contract.
    """

    @abstractmethod
    def synthesize(
        self,
        text: str,
        out_path: Path,
        *,
        turn_id: str | None = None,
        device_id: str | None = None,
    ) -> TTSResult:
        """Synthesize ``text`` into the WAV file at ``out_path``.

        ``turn_id``/``device_id`` are optional, keyword-only context for
        structured stage logging (ТЗ §33) — implementations that don't log
        may ignore them.
        """
