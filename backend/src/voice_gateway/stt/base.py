from abc import ABC, abstractmethod
from pathlib import Path

from backend.src.voice_gateway.models import Transcript


class STTProvider(ABC):
    """Vendor-neutral speech-to-text contract.

    Input is a Path to an already finalized WAV file; the concrete
    transport and vendor are not part of this contract.
    """

    @abstractmethod
    def transcribe(self, wav: Path) -> Transcript:
        """Transcribe the WAV file at ``wav`` into a Transcript."""
