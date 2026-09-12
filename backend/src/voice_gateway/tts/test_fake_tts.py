import wave
from pathlib import Path

import pytest

from backend.src.voice_gateway.models import TTSResult
from backend.src.voice_gateway.tts import TTSProvider, FakeTTS


@pytest.fixture()
def prepared_wav(tmp_path: Path) -> Path:
    """A valid 16 kHz mono silence WAV prepared for FakeTTS."""
    path = tmp_path / "prepared.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 800)
    return path


@pytest.fixture()
def prepared_wav_8k(tmp_path: Path) -> Path:
    """A valid 8 kHz mono silence WAV prepared for FakeTTS."""
    path = tmp_path / "prepared_8k.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 400)
    return path


class TestFakeTTS:
    def test_is_a_tts_provider(self, prepared_wav: Path):
        """FakeTTS implements the TTSProvider contract."""
        fake = FakeTTS(prepared_wav)
        assert isinstance(fake, TTSProvider)

    def test_copies_prepared_wav_to_out_path(self, prepared_wav: Path, tmp_path: Path):
        """synthesize writes the prepared WAV contents to out_path."""
        out_path = tmp_path / "out" / "tts.wav"
        result = FakeTTS(prepared_wav).synthesize("Привет, мир.", out_path)
        assert out_path.exists()
        assert out_path.read_bytes() == prepared_wav.read_bytes()
        assert result.wav_path == out_path
        with wave.open(str(out_path), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2

    def test_returns_sample_rate_from_wav_header(self, prepared_wav: Path, tmp_path: Path):
        """sample_rate is read from the WAV header, not a configured default."""
        out_path = tmp_path / "tts.wav"
        result = FakeTTS(prepared_wav).synthesize("Текст.", out_path)
        assert result.sample_rate == 16000

    def test_returns_header_rate_for_8k_wav(self, prepared_wav_8k: Path, tmp_path: Path):
        """The reported rate follows the prepared file, not a hardcoded value."""
        out_path = tmp_path / "tts_8k.wav"
        result = FakeTTS(prepared_wav_8k).synthesize("Текст.", out_path)
        assert result.sample_rate == 8000

    def test_is_deterministic_across_calls(self, prepared_wav: Path, tmp_path: Path):
        """Repeated calls return equal TTSResult values for the same out_path."""
        fake = FakeTTS(prepared_wav)
        out_path = tmp_path / "tts.wav"
        first = fake.synthesize("Текст.", out_path)
        second = fake.synthesize("Другой текст.", out_path)
        assert first == second
        assert first == TTSResult(wav_path=out_path, sample_rate=16000)

    def test_ignores_input_text(self, prepared_wav: Path, tmp_path: Path):
        """Any text produces the identical prepared WAV; the text is ignored."""
        fake = FakeTTS(prepared_wav)
        out_path = tmp_path / "tts.wav"
        fake.synthesize("Первый текст.", out_path)
        first_bytes = out_path.read_bytes()
        fake.synthesize("Второй совершенно другой текст.", out_path)
        assert out_path.read_bytes() == first_bytes
        assert out_path.read_bytes() == prepared_wav.read_bytes()
