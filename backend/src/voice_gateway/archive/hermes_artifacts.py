"""Atomic writers for the Hermes-stage turn artifacts (ТЗ section 18).

The turn directory layout (ТЗ section 18::

    archive/YYYY/MM/DD/<turn-id>/
        ├── input.wav
        ├── transcript.txt
        ├── hermes-request.json
        ├── hermes-response.json
        ├── reply.txt
        ├── reply.wav
        └── metadata.json

) is produced incrementally by the pipeline. This module owns the Hermes
half: every writer receives the already-resolved turn directory
(``ArchiveStore.turn_dir(...)``) and writes exactly ONE file in it, so
parallel turns in different directories can never touch each other's data
— no cross-turn overlap, no shared mutable state.

Guarantees
----------
* Every write is atomic (temp file in the same directory + flush +
  fsync + ``os.replace``), inherited from :mod:`atomic`: a file is either
  fully present with complete UTF-8 content or left as its previous
  complete version — never half-written.
* ``hermes-response.json`` stores what Hermes ACTUALLY returned. If the
  call failed before a response arrived, this module never invents a JSON
  response — the pipeline simply does not call :func:`save_hermes_response`,
  and the applicable artifacts (request, transcript, metadata) are the
  ones saved.
* For ``hermes_failed`` / ``hermes_invalid_response`` the pipeline must
  ALWAYS write ``metadata.json``: :func:`build_hermes_failure_metadata`
  produces the payload and :func:`save_hermes_failure_metadata` writes it
  through the single centralized atomic metadata path. Only safe fields
  are recorded — no secrets, no stack traces (ТЗ section 19).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.src.voice_gateway.archive.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_metadata,
)

#: Фиксированные имена файлов Hermes-стадии (ТЗ section 18).
HERMES_REQUEST_FILENAME = "hermes-request.json"
HERMES_RESPONSE_FILENAME = "hermes-response.json"
REPLY_FILENAME = "reply.txt"


def save_hermes_request(turn_dir: str | Path, payload: dict[str, Any]) -> Path:
    """Atomically write ``hermes-request.json`` into ``turn_dir``.

    ``payload`` is the request as sent to Hermes (model + messages). The
    caller is responsible for sanitizing it — credentials (``api_key`` /
    ``Authorization`` header) must never be passed in. Written as valid
    UTF-8 JSON (``ensure_ascii=False``) so Cyrillic transcripts stay
    readable.
    """
    target = Path(turn_dir) / HERMES_REQUEST_FILENAME
    return atomic_write_json(target, payload)


def save_hermes_response(turn_dir: str | Path, raw_response: str) -> Path:
    """Atomically write ``hermes-response.json`` into ``turn_dir``.

    Stores the ACTUAL raw response text from Hermes, never a fabricated
    one: if the text parses as a JSON object it is written as that
    object (pretty, UTF-8); anything else is preserved verbatim under a
    ``"raw_response"`` key. No ``reply`` / ``note`` fields are invented.
    """
    parsed: Any = None
    try:
        parsed = json.loads(raw_response)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    payload: Any = parsed if isinstance(parsed, dict) else {"raw_response": raw_response}
    target = Path(turn_dir) / HERMES_RESPONSE_FILENAME
    return atomic_write_json(target, payload)


def save_reply(turn_dir: str | Path, reply: str) -> Path:
    """Atomically write ``reply.txt`` (the Hermes ``reply`` field, UTF-8)."""
    target = Path(turn_dir) / REPLY_FILENAME
    return atomic_write_bytes(target, reply.encode("utf-8"))


def _safe_error_text(error: BaseException | str | None, limit: int = 500) -> str | None:
    """Return a short, safe human-readable error cause — or ``None``.

    Uses ``str(error)`` only — never ``repr`` of the exception object. Two
    safety rules keep the archive clean (ТЗ section 19):

    * **No stack traces.** If the message happens to embed a formatted
      traceback (``Traceback (most recent call last):`` ...), only the final
      non-empty line — the ``ExceptionClass: message`` line — is kept.
    * **Bounded length.** The result is truncated so a long failure message
      cannot bloat the archive.

    Secrets are excluded by construction: the API key is a transport
    header and is never part of the request payload, the response, or the
    exception message, so it cannot reach this function.
    """
    if error is None:
        return None
    text = str(error).strip()
    if not text:
        return None
    if "Traceback (most recent call last):" in text:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        text = lines[-1].strip() if lines else ""
    if not text:
        return None
    return text[:limit]


def build_hermes_failure_metadata(
    *,
    turn_id: str,
    status: str,
    error_type: str,
    error: BaseException | str | None = None,
    device_id: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    transcript: str | None = None,
) -> dict[str, Any]:
    """Build the safe ``metadata.json`` payload for a failed Hermes stage.

    ``status`` is the ТЗ section 32 machine code (``hermes_failed`` for a
    transport-level :class:`HermesClientError`, ``hermes_invalid_response``
    when the response still failed validation after the single repair
    pass). ``error_type`` is the exception class name for diagnostics.
    Only safe fields are emitted: turn identifier, timing, device id, the
    known transcript (already part of this turn's archive), the status and
    a truncated plain-text error cause. NO secrets, NO stack traces.
    """
    metadata: dict[str, Any] = {
        "turn_id": str(turn_id),
        "status": status,
        "error_type": str(error_type),
    }
    if device_id is not None:
        metadata["device_id"] = device_id
    if started_at is not None:
        metadata["started_at"] = started_at
    if finished_at is not None:
        metadata["finished_at"] = finished_at
    if transcript is not None:
        metadata["transcript"] = transcript
    error_text = _safe_error_text(error)
    if error_text is not None:
        metadata["error"] = error_text
    return metadata


def save_hermes_failure_metadata(turn_dir: str | Path, metadata: dict[str, Any]) -> Path:
    """Atomically write the turn's ``metadata.json`` for a Hermes failure.

    Thin wrapper over the single centralized atomic metadata writer so the
    "metadata.json is ALWAYS saved on hermes_failed / hermes_invalid_response"
    guarantee (ТЗ section 19) lives in exactly one tested place.
    """
    return atomic_write_metadata(metadata, Path(turn_dir))
