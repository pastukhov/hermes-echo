"""TTS stage configuration (ТЗ sections 28, 29, 31, 33).

Deliberately separate from :mod:`voice_gateway.config` (no shared Settings
module — ТЗ section 51: keep each stage's config self-contained so
concurrent work on sibling stages does not collide).

Environment variables (secrets stay out of the repo — ТЗ section 33):

    TTS_BASE_URL   required, the FULL OpenAI-compatible TTS endpoint URL
                   (unlike STT/Hermes, no suffix is appended by this
                   adapter — see ТЗ section 33)
    TTS_API_KEY    optional; sent as ``Authorization: Bearer ***`` when set
    TTS_MODEL      required, model name
    TTS_VOICE      voice name
    TTS_TIMEOUT    seconds; default 60 (ТЗ section 31)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

#: Default TTS timeout, seconds (ТЗ section 31: recommended TTS: 60 s).
DEFAULT_TTS_TIMEOUT = 60.0


@dataclass(frozen=True)
class TTSConfig:
    """Immutable OpenAI-compatible TTS endpoint settings (ТЗ section 33).

    ``base_url`` is the full TTS endpoint URL — the adapter posts to it
    directly and never appends ``/v1/audio/speech`` or any other suffix.
    """

    base_url: str
    model: str
    voice: str = ""
    api_key: str = ""
    timeout: float = DEFAULT_TTS_TIMEOUT

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TTSConfig":
        """Build config from an explicit environment mapping.

        Unlike the other stage configs, this does NOT fall back to
        ``os.environ`` implicitly — callers (production wiring or tests)
        must pass the mapping explicitly (ТЗ section 33: no hidden env
        access in production code paths for this stage).
        """
        source: Mapping[str, str] = env if env is not None else {}
        base_url = source.get("TTS_BASE_URL", "").strip()
        if not base_url:
            raise ValueError("TTS_BASE_URL is required")
        model = source.get("TTS_MODEL", "").strip()
        if not model:
            raise ValueError("TTS_MODEL is required")
        voice = source.get("TTS_VOICE", "")
        api_key = source.get("TTS_API_KEY", "")
        raw_timeout = source.get("TTS_TIMEOUT") or DEFAULT_TTS_TIMEOUT
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("TTS_TIMEOUT must be a number") from exc
        if timeout <= 0:
            raise ValueError("TTS_TIMEOUT must be positive")
        return cls(
            base_url=base_url,
            model=model,
            voice=voice,
            api_key=api_key,
            timeout=timeout,
        )
