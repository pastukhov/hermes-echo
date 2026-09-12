"""Structured-output validation for Hermes responses (ТЗ sections 22, 24).

The raw text returned by ``HermesClient.complete`` is parsed into a
``HermesResponse``. On invalid input exactly ONE repair pass is attempted
(ТЗ section 24 forbids unbounded retry):

1. extract a ``<json> ... </json>`` fenced block, or
2. strip a leading markdown fence (```` ```json ... ````) and/or a trailing
   comma,

then validate again. If the repaired payload is still invalid the raw text
is re-raised inside :class:`HermesValidationError` so the caller (the turn
pipeline) can persist it to the archive and fall back to a safe voice
reply without creating a note.
"""
from __future__ import annotations

import json
import re
from typing import Any

from backend.src.voice_gateway.models.hermes_response import HermesResponse


class HermesValidationError(ValueError):
    """Hermes returned a payload that does not match the ТЗ section 22 contract.

    ``raw`` carries the ORIGINAL response text (not the repaired attempt)
    for archiving.
    """

    def __init__(self, raw: str, detail: str) -> None:
        super().__init__(detail)
        self.raw = raw
        self.detail = detail


_JSON_BLOCK_RE = re.compile(r"<json>\s*(.*?)\s*</json>", re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_trailing_commas(text: str) -> str:
    return _TRAILING_COMMA_RE.sub(r"\1", text)


def repair_payload(raw: str) -> str:
    """Best-effort single repair pass. Returns repaired text.

    Only mechanical damage is repaired (fences, ``<json>`` markers, trailing
    commas) — never content. If no repair applies the original text is
    returned unchanged so the caller sees it as-is.
    """
    text = raw.strip()
    repaired = None

    block = _JSON_BLOCK_RE.search(text)
    if block is not None:
        repaired = block.group(1)

    fence = _FENCE_RE.match(text)
    if fence is not None:
        repaired = fence.group(1)

    if repaired is not None:
        candidate = repaired
    elif _TRAILING_COMMA_RE.search(text):
        candidate = _strip_trailing_commas(text)
    else:
        return text  # nothing mechanical to repair

    try:
        json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return text
    return candidate


def parse_hermes_response(raw: str) -> HermesResponse:
    """Validate raw Hermes text into a :class:`HermesResponse`.

    Raises :class:`HermesValidationError` when the payload is not valid JSON
    or fails the Pydantic schema, even after the single repair attempt.
    """
    candidates = [raw]
    repaired = repair_payload(raw)
    if repaired != raw:
        candidates.append(repaired)

    last_error: str = "unknown validation error"
    for candidate in candidates:
        try:
            payload: Any = json.loads(candidate)
            return HermesResponse.model_validate(payload)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            last_error = str(exc)

    raise HermesValidationError(raw, last_error)
