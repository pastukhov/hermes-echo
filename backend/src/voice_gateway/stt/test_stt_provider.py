from pathlib import Path

import pytest

from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt import STTProvider


class TestSTTProvider:
    def test_cannot_instantiate_abc(self):
        """STTProvider is an unfinished ABC and cannot be instantiated."""
        with pytest.raises(TypeError):
            STTProvider()

    def test_is_abc_with_single_abstract_transcribe(self):
        """The only abstract method is transcribe(self, wav: Path) -> Transcript."""
        assert STTProvider.__abstractmethods__ == frozenset({"transcribe"})


class ConcreteSTT(STTProvider):
    """Minimal concrete implementation used to verify substitution by type."""

    def __init__(self, transcript: Transcript) -> None:
        self._transcript = transcript

    def transcribe(self, wav: Path) -> Transcript:
        return self._transcript


class TestSTTProviderSubstitution:
    def test_downstream_code_can_depend_on_provider_type(self):
        """A concrete implementation is substitutable by the STTProvider type."""
        def consume(provider: STTProvider, wav: Path) -> Transcript:
            return provider.transcribe(wav)

        fake = ConcreteSTT(Transcript(text="Привет, мир.", language="ru"))
        assert isinstance(fake, STTProvider)
        result = consume(fake, Path("does/not/exist.wav"))
        assert result == Transcript(text="Привет, мир.", language="ru")
