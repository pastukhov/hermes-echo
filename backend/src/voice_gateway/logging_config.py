"""Structured (JSON) logging for the voice-gateway backend (ТЗ §33).

This module provides stdlib-``logging``-only JSON logging — no ``structlog``
or other new runtime dependency (``backend/requirements.txt`` stays pinned
exactly as-is).

Logger hierarchy note: this codebase's existing loggers are NOT all under
one consistent dotted namespace — ``middleware.py`` uses the explicit name
``"voice_gateway.middleware"``, while ``stt/client.py``, ``hermes/client.py``
and ``tts/openai_compatible.py`` use ``logging.getLogger(__name__)``, which
resolves to their full dotted module path (e.g.
``"backend.src.voice_gateway.stt.client"``) because of how this package is
imported. Those two naming schemes do not share a common non-root ancestor
logger, so :func:`configure_logging` attaches its handler to the ROOT
logger (``logging.getLogger()``) instead of a ``"voice_gateway"`` namespace
logger — this is the only way to guarantee every existing and future stage
logger is covered by the same ``JsonFormatter``, regardless of which of the
two naming conventions it follows. ``LOG_LEVEL`` is likewise applied to the
root logger.

Public surface:

* :class:`JsonFormatter` — renders each :class:`logging.LogRecord` as one
  JSON object per line.
* :func:`configure_logging` — attaches the JSON handler to the root logger
  exactly once (idempotent — safe to call every time ``create_app()`` runs,
  e.g. once per test).
* :func:`transcript_logging_enabled` — reads ``LOG_TRANSCRIPT`` fresh from
  the environment on every call (never cached at import time), so tests can
  monkeypatch ``os.environ`` per-test.
* :func:`log_stage_event` — emits one structured pipeline-stage log record
  (archive / stt / hermes / tts / notes) carrying the minimal field set
  (ТЗ §33): ``turn_id``, ``device_id``, ``stage``, ``duration_ms``,
  ``status``, ``error``.

Secrets (``STT_API_KEY`` / ``HERMES_API_KEY`` / ``TTS_API_KEY``) and raw
audio bytes must never be passed to any logging call in this codebase.
Transcript/reply text must never be passed to :func:`log_stage_event` —
callers gate any transcript-bearing log call behind
:func:`transcript_logging_enabled` and log it at ``DEBUG`` through a
separate, explicit ``logger.debug(...)`` call (ТЗ §33).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

__all__ = [
    "JsonFormatter",
    "configure_logging",
    "transcript_logging_enabled",
    "log_stage_event",
]

#: Extra structured fields JsonFormatter promotes from the LogRecord, when
#: present and non-None. Fields absent/None are omitted entirely — never
#: emitted as ``null`` (ТЗ §33: minimal, clean field set per record).
_EXTRA_FIELDS = ("turn_id", "device_id", "stage", "duration_ms", "status", "error")

#: Marker attribute set on the root logger once :func:`configure_logging`
#: has attached its handler, so a second/Nth call (e.g. ``create_app()``
#: called again in tests) never attaches a duplicate handler.
_CONFIGURED_ATTR = "_voice_gateway_json_logging_configured"


class JsonFormatter(logging.Formatter):
    """Render one :class:`logging.LogRecord` as a single JSON object.

    Always includes ``timestamp`` (ISO-8601 UTC), ``level``, ``logger``,
    ``message``. Additionally includes any of :data:`_EXTRA_FIELDS` present
    (and non-``None``) on the record — these arrive via the ``extra=``
    kwarg on a logging call (see :func:`log_stage_event`).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            # ``getMessage()`` applies %-args, so any secret/PII smuggled in
            # via args (not just record.msg) would surface here too — the
            # discipline is to never pass such values to a logging call at
            # all, not to rely on the formatter to strip them out.
            "message": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """Attach JSON logging to the root logger (idempotent).

    Reads ``LOG_LEVEL`` from the environment (default ``"INFO"``) and sets
    it on the root logger every call (cheap, harmless to repeat). The
    ``StreamHandler(stdout)`` + :class:`JsonFormatter` pair is attached only
    once — subsequent calls (e.g. ``create_app()`` invoked multiple times
    in a test session) are no-ops for the handler, so log lines are never
    duplicated.
    """
    root = logging.getLogger()

    level_name = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO
    root.setLevel(level)

    if getattr(root, _CONFIGURED_ATTR, False):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    setattr(root, _CONFIGURED_ATTR, True)


def transcript_logging_enabled() -> bool:
    """Whether transcript/reply text may be logged (DEBUG-only, ТЗ §33).

    Reads ``LOG_TRANSCRIPT`` fresh from the environment on every call —
    never cached at import time, so tests can monkeypatch ``os.environ``
    per-test and see the change immediately. ``"true"``/``"1"``
    (case-insensitive) are truthy; anything else, including unset, is
    ``False``.
    """
    return (os.environ.get("LOG_TRANSCRIPT") or "").strip().lower() in ("true", "1")


def log_stage_event(
    logger: logging.Logger,
    stage: str,
    *,
    turn_id: str,
    device_id: str | None,
    duration_ms: int,
    status: str,
    error: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Emit one structured pipeline-stage log record.

    Carries the minimal field set (ТЗ §33) as ``extra`` keys so
    :class:`JsonFormatter` picks them up: ``turn_id``, ``device_id``,
    ``stage``, ``duration_ms``, ``status``, and ``error`` (only when
    non-``None`` — omitted entirely on success, never emitted as
    ``"error": null``). The message text is a fixed literal — never built
    by string-formatting secrets or transcript text into it.
    """
    extra = {
        "turn_id": turn_id,
        "device_id": device_id,
        "stage": stage,
        "duration_ms": duration_ms,
        "status": status,
    }
    if error is not None:
        extra["error"] = error
    logger.log(level, "stage_event", extra=extra)
