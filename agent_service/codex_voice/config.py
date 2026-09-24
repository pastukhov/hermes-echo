"""Configuration for the host-side Codex runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    cwd: str
    model: str | None = None
    turn_timeout_seconds: float = 120.0

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None) -> RuntimeConfig:
        values = os.environ if source is None else source
        raw_cwd = values.get("CODEX_VOICE_CWD", os.getcwd()).strip()
        cwd = Path(raw_cwd).expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError("CODEX_VOICE_CWD must name an existing directory")

        raw_model = values.get("CODEX_VOICE_MODEL", "").strip()
        raw_timeout = values.get("CODEX_VOICE_TURN_TIMEOUT", "120").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("CODEX_VOICE_TURN_TIMEOUT must be a number") from exc
        if not 0 < timeout <= 120:
            raise ValueError("CODEX_VOICE_TURN_TIMEOUT must be between 0 and 120 seconds")

        return cls(
            cwd=str(cwd),
            model=raw_model or None,
            turn_timeout_seconds=timeout,
        )
