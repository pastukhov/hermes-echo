from pathlib import Path

from pydantic import BaseModel


class TTSResult(BaseModel):
    """Vendor-neutral TTS result: written WAV path and its actual rate.

    ``sample_rate`` is the real rate (Hz) read from the WAV header of the
    implementation's output file, never a configured default.
    """

    wav_path: Path
    sample_rate: int
