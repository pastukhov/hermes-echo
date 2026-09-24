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


class SecurityConfigError(ValueError):
    """Invalid security configuration (malformed VOICE_* environment values)."""


#: Defaults for per-client / per-route rate limiting (ТЗ security).
DEFAULT_RATE_LIMIT = 100
DEFAULT_RATE_PERIOD = 60


def _parse_rate_value(raw: str | None, name: str, default: int) -> int:
    """Parse a positive integer rate-limiting value, falling back to a default.

    ``raw`` is the raw environment string (or ``None``/empty to use the
    default). Raises :class:`SecurityConfigError` when the value is present
    but not a positive integer.
    """
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except (TypeError, ValueError) as exc:
        raise SecurityConfigError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise SecurityConfigError(f"{name} must be a positive integer")
    return value


class AgentConfigError(ValueError):
    """Invalid voice-agent provider configuration."""


@dataclass(frozen=True)
class AgentConfig:
    provider: str
    codex_url: str = ""
    codex_token: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AgentConfig":
        source = os.environ if env is None else env
        provider = source.get("VOICE_AGENT_PROVIDER", "hermes").strip().lower()
        if provider not in {"hermes", "codex"}:
            raise AgentConfigError("VOICE_AGENT_PROVIDER must be hermes or codex")
        if provider == "hermes":
            return cls(provider)
        url = source.get("CODEX_AGENT_URL", "").strip()
        token = source.get("CODEX_AGENT_TOKEN", "").strip()
        if not url:
            raise AgentConfigError("CODEX_AGENT_URL is required for Codex provider")
        if not token:
            raise AgentConfigError("CODEX_AGENT_TOKEN is required for Codex provider")
        return cls(provider, url, token)


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


@dataclass(frozen=True)
class SecurityConfig:
    """Immutable gateway security settings: device-token auth + rate limits.

    Auth fields mirror the device-token contract of ТЗ section 40 (sibling
    auth card t_ed297906): ``auth_enabled`` defaults to ``True`` on direct
    construction — building a :class:`SecurityConfig` by hand means "I want
    auth". The env-based factory decides the default from ``VOICE_API_KEY``
    / ``VOICE_AUTH_ENABLED`` instead (see :meth:`from_env`).

    Rate-limiting fields (task t_89295105): one sliding window per client
    AND one per route, both bounded by ``rate_limit`` requests per
    ``rate_period`` seconds.
    """

    api_key: str
    auth_enabled: bool = True
    rate_limit: int = DEFAULT_RATE_LIMIT
    rate_period: int = DEFAULT_RATE_PERIOD

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SecurityConfig":
        """Build config from environment variables.

        Auth rules (ТЗ section 40, docs/protocol.md):

        * ``VOICE_AUTH_ENABLED`` unset → auth is enabled iff
          ``VOICE_API_KEY`` is non-empty (token configurable; a gateway
          without a configured token does not require one).
        * explicit ``true`` + empty key → :class:`SecurityConfigError`
          (a gateway must never boot "enabled but toothless").
        * explicit ``false`` → the test-only disable mode, key ignored.

        Rate-limit rules (task t_89295105): ``VOICE_RATE_LIMIT`` /
        ``VOICE_RATE_PERIOD`` are optional positive integers with defaults
        (:data:`DEFAULT_RATE_LIMIT`, :data:`DEFAULT_RATE_PERIOD`).
        """
        source = os.environ if env is None else env
        api_key = source.get("VOICE_API_KEY", "").strip()
        raw_enabled = source.get("VOICE_AUTH_ENABLED", "").strip().lower()
        if raw_enabled == "":
            auth_enabled = bool(api_key)
        elif raw_enabled in ("1", "true", "yes", "on"):
            if not api_key:
                raise SecurityConfigError(
                    "VOICE_AUTH_ENABLED is true but VOICE_API_KEY is empty"
                )
            auth_enabled = True
        elif raw_enabled in ("0", "false", "no", "off"):
            auth_enabled = False
        else:
            raise SecurityConfigError(
                "VOICE_AUTH_ENABLED must be true or false, "
                f"got {raw_enabled!r}"
            )
        rate_limit = _parse_rate_value(
            source.get("VOICE_RATE_LIMIT"), "VOICE_RATE_LIMIT", DEFAULT_RATE_LIMIT
        )
        rate_period = _parse_rate_value(
            source.get("VOICE_RATE_PERIOD"), "VOICE_RATE_PERIOD", DEFAULT_RATE_PERIOD
        )
        return cls(
            api_key=api_key,
            auth_enabled=auth_enabled,
            rate_limit=rate_limit,
            rate_period=rate_period,
        )


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
