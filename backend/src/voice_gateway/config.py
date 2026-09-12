"""Stage configuration (ТЗ sections 20, 21, 31).

Environment variables (secrets stay out of the repo — ТЗ section 51):

    HERMES_BASE_URL   required, OpenAI-compatible endpoint root
    HERMES_API_KEY    optional; sent as ``Authorization: *** when set
    HERMES_MODEL      model name
    HERMES_TIMEOUT    seconds; recommended default 120 (ТЗ section 31)

    STT_BASE_URL      required, OpenAI-compatible STT endpoint root
    STT_API_KEY       optional; sent as ``Authorization: *** when set
    STT_MODEL         model name
    STT_TIMEOUT       seconds; recommended default 60 (ТЗ section 31)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_HERMES_TIMEOUT = 120.0
DEFAULT_HERMES_MODEL = "hermes"

#: System prompt file location (ТЗ section 23).
HERMES_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "hermes_voice.md"
)

#: Default STT timeout, seconds (ТЗ section 31: recommended STT: 60 s).
DEFAULT_STT_TIMEOUT = 60.0
DEFAULT_STT_MODEL = "stt"


class HermesConfigError(ValueError):
    """Invalid Hermes configuration (missing or malformed environment values)."""


class STTConfigError(ValueError):
    """Invalid STT configuration (missing or malformed environment values)."""


@dataclass(frozen=True)
class HermesConfig:
    """Immutable Hermes endpoint settings."""

    base_url: str
    model: str = DEFAULT_HERMES_MODEL
    api_key: str = ""
    timeout: float = DEFAULT_HERMES_TIMEOUT

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "HermesConfig":
        """Build config from environment variables.

        ``env`` defaults to ``os.environ``; tests pass an explicit mapping.
        """
        source = os.environ if env is None else env
        base_url = source.get("HERMES_BASE_URL", "").strip()
        if not base_url:
            raise HermesConfigError("HERMES_BASE_URL is required")
        model = source.get("HERMES_MODEL", "").strip() or DEFAULT_HERMES_MODEL
        api_key = source.get("HERMES_API_KEY", "")
        raw_timeout = source.get("HERMES_TIMEOUT") or DEFAULT_HERMES_TIMEOUT
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise HermesConfigError("HERMES_TIMEOUT must be a number") from exc
        if timeout <= 0:
            raise HermesConfigError("HERMES_TIMEOUT must be positive")
        return cls(base_url=base_url, model=model, api_key=api_key, timeout=timeout)


@dataclass(frozen=True)
class STTConfig:
    """Immutable OpenAI-compatible STT endpoint settings (ТЗ section 20)."""

    base_url: str
    model: str = DEFAULT_STT_MODEL
    api_key: str = ""
    timeout: float = DEFAULT_STT_TIMEOUT

    @property
    def transcriptions_url(self) -> str:
        """OpenAI-compatible audio transcriptions endpoint (ТЗ section 20)."""
        return self.base_url.rstrip("/") + "/audio/transcriptions"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "STTConfig":
        """Build config from environment variables.

        ``env`` defaults to ``os.environ``; tests pass an explicit mapping.
        """
        source = os.environ if env is None else env
        base_url = source.get("STT_BASE_URL", "").strip()
        if not base_url:
            raise STTConfigError("STT_BASE_URL is required")
        model = source.get("STT_MODEL", "").strip() or DEFAULT_STT_MODEL
        api_key = source.get("STT_API_KEY", "")
        raw_timeout = source.get("STT_TIMEOUT") or DEFAULT_STT_TIMEOUT
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise STTConfigError("STT_TIMEOUT must be a number") from exc
        if timeout <= 0:
            raise STTConfigError("STT_TIMEOUT must be positive")
        return cls(base_url=base_url, model=model, api_key=api_key, timeout=timeout)


def load_hermes_prompt(path: str | Path | None = None) -> str:
    """Read the Hermes system prompt file (ТЗ section 23)."""
    prompt_path = Path(path) if path is not None else HERMES_PROMPT_PATH
    try:
        text = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HermesConfigError(
            f"cannot read Hermes prompt file {prompt_path}: {exc}"
        ) from exc
    if not text.strip():
        raise HermesConfigError(f"Hermes prompt file {prompt_path} is empty")
    return text
