from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.src.voice_gateway.models import Transcript


class TestTranscript:
    def test_create_with_text_and_language(self):
        """Transcript stores exact text and language values."""
        transcript = Transcript(text="Привет, мир.", language="ru")
        assert transcript.text == "Привет, мир."
        assert transcript.language == "ru"

    def test_serialization_roundtrip_preserves_values(self):
        """model_dump/model_validate round-trip preserves both values."""
        transcript = Transcript(text="Привет, мир.", language="ru")
        dumped = transcript.model_dump()
        assert dumped == {"text": "Привет, мир.", "language": "ru"}
        restored = Transcript.model_validate(dumped)
        assert restored == transcript

    def test_missing_text_is_validation_error(self):
        """Omitting the required text field raises a Pydantic validation error."""
        with pytest.raises(ValidationError):
            Transcript(language="ru")

    def test_missing_language_is_validation_error(self):
        """Omitting the required language field raises a Pydantic validation error."""
        with pytest.raises(ValidationError):
            Transcript(text="Привет, мир.")

    def test_non_string_values_are_rejected(self):
        """Non-string text or language values are rejected."""
        with pytest.raises(ValidationError):
            Transcript(text=123, language="ru")
        with pytest.raises(ValidationError):
            Transcript(text="Привет, мир.", language=123)
