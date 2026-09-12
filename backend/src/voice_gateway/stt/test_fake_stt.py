from pathlib import Path

from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt import STTProvider, FakeSTT


class TestFakeSTT:
    def test_is_a_stt_provider(self):
        """FakeSTT implements the STTProvider contract."""
        fake = FakeSTT(Transcript(text="Привет, мир.", language="ru"))
        assert isinstance(fake, STTProvider)

    def test_returns_configured_transcript_for_missing_path(self):
        """transcribe returns the configured values without requiring the path."""
        configured = Transcript(text="Привет, мир.", language="ru")
        fake = FakeSTT(configured)
        result = fake.transcribe(Path("/nonexistent/does/not/exist.wav"))
        assert result.text == "Привет, мир."
        assert result.language == "ru"
        assert result == configured

    def test_is_deterministic_across_calls(self):
        """Repeated calls return the same configured values."""
        configured = Transcript(text="Текст.", language="en")
        fake = FakeSTT(configured)
        first = fake.transcribe(Path("a.wav"))
        second = fake.transcribe(Path("b.wav"))
        assert first == second
        assert first == configured

    def test_does_not_require_file_on_disk(self):
        """The passed Path is never created or read: a missing path is fine."""
        fake = FakeSTT(Transcript(text="x", language="ru"))
        missing = Path(__file__).parent / "absent.wav"
        assert not missing.exists()
        result = fake.transcribe(missing)
        assert result.text == "x"
        assert not missing.exists()
