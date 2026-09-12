import shutil
import wave
from pathlib import Path

from backend.src.voice_gateway.models import TTSResult
from backend.src.voice_gateway.tts.base import TTSProvider


class FakeTTS(TTSProvider):
    """Deterministic TTS stand-in for tests.

    Copies the pre-prepared valid WAV to ``out_path`` and reads the sample
    rate from the WAV header. Performs no network activity, ignores the
    input text and never validates the WAV beyond header fields.
    """

    def __init__(self, prepared_wav: Path) -> None:
        self._prepared_wav = prepared_wav

    def synthesize(self, text: str, out_path: Path) -> TTSResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._prepared_wav, out_path)
        with wave.open(str(out_path), "rb") as wav:
            sample_rate = wav.getframerate()
        return TTSResult(wav_path=out_path, sample_rate=sample_rate)
