"""Tests for Hermes config loading from environment (ТЗ section 21)."""
from pathlib import Path

import pytest

from backend.src.voice_gateway.config import (
    DEFAULT_HERMES_MODEL,
    DEFAULT_HERMES_TIMEOUT,
    HERMES_PROMPT_PATH,
    HermesConfig,
    HermesConfigError,
    load_hermes_prompt,
)


class TestHermesConfigFromEnv:
    def test_all_values_from_env(self):
        config = HermesConfig.from_env(
            {
                "HERMES_BASE_URL": "https://hermes.internal/",
                "HERMES_API_KEY": "sekret",
                "HERMES_MODEL": "hermes-voice",
                "HERMES_TIMEOUT": "90.5",
            }
        )
        assert config.base_url == "https://hermes.internal/"
        assert config.api_key == "sekret"
        assert config.model == "hermes-voice"
        assert config.timeout == 90.5

    def test_chat_completions_url_normalizes_trailing_slash(self):
        config = HermesConfig.from_env({"HERMES_BASE_URL": "http://192.168.1.10:8000/"})
        assert config.chat_completions_url == (
            "http://192.168.1.10:8000/chat/completions"
        )

    def test_defaults_for_model_and_timeout(self):
        config = HermesConfig.from_env({"HERMES_BASE_URL": "http://h"})
        assert config.model == DEFAULT_HERMES_MODEL
        assert config.timeout == DEFAULT_HERMES_TIMEOUT
        assert config.api_key == ""

    def test_missing_base_url_raises(self):
        with pytest.raises(HermesConfigError, match="HERMES_BASE_URL"):
            HermesConfig.from_env({})

    def test_whitespace_only_base_url_raises(self):
        with pytest.raises(HermesConfigError, match="HERMES_BASE_URL"):
            HermesConfig.from_env({"HERMES_BASE_URL": "   "})

    def test_invalid_timeout_raises(self):
        env = {"HERMES_BASE_URL": "http://h", "HERMES_TIMEOUT": "not-a-number"}
        with pytest.raises(HermesConfigError, match="HERMES_TIMEOUT"):
            HermesConfig.from_env(env)

    def test_non_positive_timeout_raises(self):
        for bad in ("0", "-5"):
            env = {"HERMES_BASE_URL": "http://h", "HERMES_TIMEOUT": bad}
            with pytest.raises(HermesConfigError, match="positive"):
                HermesConfig.from_env(env)

    def test_from_env_is_frozen(self):
        config = HermesConfig.from_env({"HERMES_BASE_URL": "http://h"})
        with pytest.raises(AttributeError):
            config.model = "other"  # pyright: ignore[reportAttributeAccessIssue]


class TestLoadHermesPrompt:
    def test_repo_prompt_file_is_non_empty(self):
        """The system prompt file required by ТЗ section 23 exists in repo."""
        assert HERMES_PROMPT_PATH.is_file()
        text = load_hermes_prompt()
        assert "reply" in text

    def test_missing_prompt_file_raises(self, tmp_path: Path):
        missing = tmp_path / "nope.md"
        with pytest.raises(HermesConfigError, match="cannot read"):
            load_hermes_prompt(missing)

    def test_empty_prompt_file_raises(self, tmp_path: Path):
        empty = tmp_path / "empty.md"
        empty.write_text("   \n", encoding="utf-8")
        with pytest.raises(HermesConfigError, match="empty"):
            load_hermes_prompt(empty)
