"""Tests for TTS config loading from environment (ТЗ sections 31, 33).

No dependence on ``os.environ`` — every test passes an explicit mapping.
"""
import pytest

from backend.src.voice_gateway.tts.config import DEFAULT_TTS_TIMEOUT, TTSConfig


class TestTTSConfigFromEnv:
    def test_all_values_from_env(self):
        config = TTSConfig.from_env(
            {
                "TTS_BASE_URL": "https://tts.local/speech",
                "TTS_API_KEY": "sekret",
                "TTS_MODEL": "tts-1",
                "TTS_VOICE": "alloy",
                "TTS_TIMEOUT": "30.5",
            }
        )
        assert config.base_url == "https://tts.local/speech"
        assert config.api_key == "sekret"
        assert config.model == "tts-1"
        assert config.voice == "alloy"
        assert config.timeout == 30.5

    def test_defaults_for_timeout_and_api_key(self):
        config = TTSConfig.from_env(
            {
                "TTS_BASE_URL": "https://tts.local/speech",
                "TTS_MODEL": "m",
                "TTS_VOICE": "v",
            }
        )
        assert config.timeout == DEFAULT_TTS_TIMEOUT == 60.0
        assert config.api_key == ""

    def test_missing_env_defaults_to_empty_mapping(self):
        with pytest.raises(ValueError, match="TTS_BASE_URL"):
            TTSConfig.from_env(None)

    def test_missing_base_url_raises(self):
        with pytest.raises(ValueError, match="TTS_BASE_URL"):
            TTSConfig.from_env({"TTS_MODEL": "m", "TTS_VOICE": "v"})

    def test_whitespace_only_base_url_raises(self):
        with pytest.raises(ValueError, match="TTS_BASE_URL"):
            TTSConfig.from_env(
                {"TTS_BASE_URL": "   ", "TTS_MODEL": "m", "TTS_VOICE": "v"}
            )

    def test_missing_model_raises(self):
        with pytest.raises(ValueError, match="TTS_MODEL"):
            TTSConfig.from_env({"TTS_BASE_URL": "https://tts.local/speech"})

    def test_whitespace_only_model_raises(self):
        with pytest.raises(ValueError, match="TTS_MODEL"):
            TTSConfig.from_env(
                {"TTS_BASE_URL": "https://tts.local/speech", "TTS_MODEL": "   "}
            )

    def test_invalid_timeout_raises(self):
        env = {
            "TTS_BASE_URL": "https://tts.local/speech",
            "TTS_MODEL": "m",
            "TTS_TIMEOUT": "not-a-number",
        }
        with pytest.raises(ValueError, match="TTS_TIMEOUT"):
            TTSConfig.from_env(env)

    def test_zero_timeout_raises(self):
        env = {
            "TTS_BASE_URL": "https://tts.local/speech",
            "TTS_MODEL": "m",
            "TTS_TIMEOUT": "0",
        }
        with pytest.raises(ValueError, match="positive"):
            TTSConfig.from_env(env)

    def test_negative_timeout_raises(self):
        env = {
            "TTS_BASE_URL": "https://tts.local/speech",
            "TTS_MODEL": "m",
            "TTS_TIMEOUT": "-1",
        }
        with pytest.raises(ValueError, match="positive"):
            TTSConfig.from_env(env)

    def test_from_env_is_frozen(self):
        config = TTSConfig.from_env(
            {"TTS_BASE_URL": "https://tts.local/speech", "TTS_MODEL": "m"}
        )
        with pytest.raises(AttributeError):
            config.model = "other"  # pyright: ignore[reportAttributeAccessIssue]
