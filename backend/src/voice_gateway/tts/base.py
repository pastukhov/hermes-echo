from abc import ABC, abstractmethod
from pathlib import Path

from backend.src.voice_gateway.models import TTSResult


class TTSProvider(ABC):
    """Vendor-neutral text-to-speech contract.

    ``out_path`` is the path where the implementation writes the finalized
    WAV file; the concrete transport and vendor are not part of this
    contract.
    """

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> TTSResult:
        """Synthesize ``text`` into the WAV file at ``out_path``."""
