from pathlib import Path

import pytest

from backend.src.voice_gateway.models import TTSResult
from backend.src.voice_gateway.tts import TTSProvider


class TestTTSProvider:
    def test_cannot_instantiate_abc(self):
        """TTSProvider is an unfinished ABC and cannot be instantiated."""
        with pytest.raises(TypeError):
            TTSProvider()

    def test_is_abc_with_single_abstract_synthesize(self):
        """The only abstract method is synthesize(text, out_path) -> TTSResult."""
        assert TTSProvider.__abstractmethods__ == frozenset({"synthesize"})


class ConcreteTTS(TTSProvider):
    """Minimal concrete implementation used to verify substitution by type."""

    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate

    def synthesize(self, text: str, out_path: Path) -> TTSResult:
        return TTSResult(wav_path=out_path, sample_rate=self._sample_rate)


class TestTTSProviderSubstitution:
    def test_downstream_code_can_depend_on_provider_type(self):
        """A concrete implementation is substitutable by the TTSProvider type."""
        def consume(provider: TTSProvider, text: str, out_path: Path) -> TTSResult:
            return provider.synthesize(text, out_path)

        fake = ConcreteTTS(sample_rate=24000)
        assert isinstance(fake, TTSProvider)
        result = consume(fake, "Привет, мир.", Path("out/does-not-matter.wav"))
        assert result == TTSResult(wav_path=Path("out/does-not-matter.wav"), sample_rate=24000)
