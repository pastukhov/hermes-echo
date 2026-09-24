"""Hermes stage one voiceturn (ТЗ sections 21–24).

The stage owns exactly ONE Hermes call per turn:

1. ``HermesClient.complete(transcript)`` — the single transport call;
2. ``parse_hermes_response(raw)`` — parses and performs the single repair
   attempt internally (ТЗ §24 "one call + one repair").

It never re-invokes the client, so a turn can never make more than one
Hermes round-trip. Failures are surfaced as :class:`HermesStageError`
carrying the bounded terminal status (ТЗ §32) and — for an invalid
response — the ORIGINAL raw text for archiving.

The raw response text of the in-flight turn is exposed through
:data:`last_raw_response`, a task-local :class:`contextvars.ContextVar`,
so a shared stage instance serving concurrent turns cannot race on an
instance attribute. The turn pipeline reads it to archive
``hermes-response.json`` (ТЗ §19).
"""
from __future__ import annotations

from contextvars import ContextVar

from backend.common.error_codes import ErrorCode
from backend.src.voice_gateway.hermes.base import (
    HermesClient,
    HermesClientError,
)
from backend.src.voice_gateway.hermes.validation import (
    HermesValidationError,
    parse_hermes_response,
)
from backend.src.voice_gateway.models.hermes_response import HermesResponse

#: Raw response text of the current turn (set by :meth:`HermesStage.run`).
#: Task-local: each request runs in its own task, so concurrent turns on a
#: shared :class:`HermesStage` never observe each other's raw payload.
last_raw_response: ContextVar[str | None] = ContextVar(
    "hermes_last_raw_response", default=None
)


class HermesStageError(Exception):
    """Terminal Hermes-stage failure for a turn.

    ``raw`` carries the ORIGINAL Hermes response text when the failure is an
    invalid/repair-failed payload (so the pipeline can archive it), or
    ``None`` when the failure was at the transport level (there was no
    response body to keep). ``status`` is the bounded :class:`ErrorCode`
    value persisted to ``metadata.json``; ``error`` is a short, key-free
    message (ТЗ §33 — never contains the API key or request/response bodies).
    """

    def __init__(self, raw: str | None, *, status: str, error: str) -> None:
        super().__init__(error)
        self.raw = raw
        self.status = status
        self.error = error


class HermesStage:
    """Runs the single Hermes call + validation for one turn."""

    def __init__(self, client: HermesClient) -> None:
        self._client = client

    async def run(self, transcript: str) -> HermesResponse:
        """Call Hermes exactly once and return the validated response.

        Raises :class:`HermesStageError` on a transport failure
        (``hermes_failed``) or an unrepairable payload
        (``hermes_invalid_response``). On a successful call the raw text is
        also stored in :data:`last_raw_response` for archival.
        """
        try:
            # The single Hermes transport call for this turn.
            raw = await self._client.complete(transcript)
        except HermesClientError as exc:
            # Transport-level failure: no response body to archive.
            raise HermesStageError(
                None,
                status=ErrorCode.HERMES_FAILED.value,
                error=str(exc),
            ) from exc

        # Expose the raw response text for turn archival (ТЗ §19).
        last_raw_response.set(raw)

        try:
            # ``parse_hermes_response`` performs the single repair attempt
            # internally; it is called exactly once.
            return parse_hermes_response(raw)
        except HermesValidationError as exc:
            # ``exc.raw`` carries the ORIGINAL response text, for archiving.
            raise HermesStageError(
                exc.raw,
                status=ErrorCode.HERMES_INVALID_RESPONSE.value,
                error=str(exc),
            ) from exc

