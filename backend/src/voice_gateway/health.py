"""Health endpoints (ТЗ §35, docs/protocol.md «Служебные endpoints»).

Two checks, deliberately asymmetric in strictness:

* ``/health/live`` — the process is up. No config, no filesystem, no
  network: it must return 200 for as long as the server is alive.
* ``/health/ready`` — the gateway can serve turns: configuration is
  loaded and the archive is writable. Per ТЗ §35 readiness must NOT
  depend on the momentary availability of Hermes/STT/TTS, so neither
  a config check nor a probe ever performs a network call.

Note storage is ``writable/optional`` (ТЗ §25/§35): the vault is probed
and its state is reported in the ``notes`` check, but a missing or
read-only vault never flips readiness to 503 — notes are a best-effort
side effect of a successful turn, never a prerequisite.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

#: Environment variables that must be present for the configuration to be
#: considered loaded (ТЗ §36: the two required provider endpoint roots).
#: ``*_API_KEY`` are optional (auth-less endpoints are supported), so an
#: empty key never makes the config invalid.
_REQUIRED_ENV_VARS = ("STT_BASE_URL",)


@dataclass(frozen=True)
class ReadinessReport:
    """Outcome of :func:`check_ready`.

    ``status`` is ``"ok"`` when the gateway may serve traffic, else
    ``"not_ready"``. ``checks`` maps the check name to a short machine-
    readable state: ``"ok"``, ``"disabled"`` (notes not configured),
    ``"writable"``/``"not_writable"`` (notes), or ``"error:..."`` with a
    reason (never a transcript/title/turn_id — ТЗ §34 label hygiene).
    """

    status: str
    checks: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict:
        return {"status": self.status, "checks": dict(self.checks)}


def check_live() -> dict:
    """Лiveness (ТЗ §35): ``process alive`` — nothing else.

    No config access, no filesystem access, no external calls, so a
    hung/unavailable Hermes/STT/TTS can never turn liveness red.
    """
    return {"status": "ok", "service": "voice-gateway"}


def _probe_writable(directory: Path) -> str:
    """Probe an existing directory with a throwaway file.

    Returns ``"ok"`` when a file could be created, written, fsynced and
    removed in ``directory``; otherwise ``"error:<short reason>"``. The
    probe never creates missing directories — creating data directories
    is the app's job, not the probe's.
    """
    try:
        probe = tempfile.NamedTemporaryFile(
            dir=directory, prefix=".health-probe-", suffix=".tmp"
        )
    except OSError as exc:
        return f"error:{exc.strerror or exc}"
    try:
        probe.write(b"ok")
        probe.flush()
        os.fsync(probe.fileno())
        return "ok"
    except OSError as exc:
        return f"error:{exc.strerror or exc}"
    finally:
        probe.close()  # deletes the probe file


def check_ready(
    archive_root: str | os.PathLike | None,
    note_root: str | os.PathLike | None,
    env: Mapping[str, str],
) -> ReadinessReport:
    """Readiness (ТЗ §35): ``config загружен`` + ``archive writable``.

    * config — the required endpoint roots are present in ``env`` (no
      network call, so a dead STT/Hermes/TTS cannot fail this check);
    * archive — the turn archive is present and writable (the mandatory
      storage, ТЗ §18);
    * notes — the Obsidian vault state (ТЗ §25), reported but never
      blocking: writable/optional.

    The returned :class:`ReadinessReport` carries per-check detail for
    the 503 body so operators see exactly what is not ready.
    """
    checks: dict = {}

    provider = (env.get("VOICE_AGENT_PROVIDER") or "hermes").strip().lower()
    required = list(_REQUIRED_ENV_VARS)
    if provider == "hermes":
        required.append("HERMES_BASE_URL")
    elif provider == "codex":
        required.extend(("CODEX_AGENT_URL", "CODEX_AGENT_TOKEN"))
    else:
        checks["provider"] = "error:VOICE_AGENT_PROVIDER must be hermes or codex"
    missing = [name for name in required if not (env.get(name) or "").strip()]
    if provider not in {"hermes", "codex"}:
        missing.append("VOICE_AGENT_PROVIDER")
    checks["config"] = ("ok" if not missing
                        else "error:missing " + ",".join(missing))

    if archive_root is None:
        checks["archive"] = "error:archive root not configured"
    else:
        root = Path(archive_root)
        if not root.is_dir():
            checks["archive"] = f"error:{root} is not a directory"
        else:
            checks["archive"] = _probe_writable(root)

    if note_root is None or not str(note_root).strip():
        checks["notes"] = "disabled"
    else:
        vault = Path(note_root)
        if vault.is_dir():
            checks["notes"] = ("writable"
                               if _probe_writable(vault) == "ok"
                               else "not_writable")
        else:
            checks["notes"] = "not_writable"

    ready = checks["config"] == "ok" and checks["archive"] == "ok"
    return ReadinessReport("ok" if ready else "not_ready", checks)
