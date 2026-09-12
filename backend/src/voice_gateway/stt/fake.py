from pathlib import Path

from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt.base import STTProvider


class FakeSTT(STTProvider):
    """Deterministic STT stand-in for tests.

    Returns the pre-configured Transcript for any Path. Performs no
    filesystem, I/O or network activity and never validates the WAV.
    """

    def __init__(self, transcript: Transcript) -> None:
        self._transcript = transcript

    def transcribe(self, wav: Path) -> Transcript:
        return self._transcript
