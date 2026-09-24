"""Unit tests for the Hermes stage (ТЗ sections 21–24).

Covers the single-call policy directly, without the FastAPI layer:

* a valid payload returns a :class:`HermesResponse`;
* a transport-level :class:`HermesClientError` maps to
  ``HermesStageError(status="hermes_failed", raw=None)``;
* an unrepairable payload maps to
  ``HermesStageError(status="hermes_invalid_response", raw=<original>)``;
* the client is invoked **exactly once** on every path — the repair pass
  lives inside :func:`parse_hermes_response`, never as a second client call.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.common.error_codes import ErrorCode
from backend.src.voice_gateway.hermes.base import HermesClient, HermesClientError
from backend.src.voice_gateway.hermes.stage import HermesStage, HermesStageError
from backend.src.voice_gateway.models.hermes_response import HermesResponse

_VALID_RAW = '{"reply": "готово", "note": {"create": false, "title": "", ' \
             '"content": "", "tags": []}}'


class _CountingHermes(HermesClient):
    """Returns a fixed raw payload and counts every ``complete`` call."""

    def __init__(self, raw: str | None = _VALID_RAW,
                 error: Exception | None = None) -> None:
        self._raw = raw
        self._error = error
        self.calls = 0

    async def complete(self, transcript: str) -> str:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("Hermes called more than once in one turn")
        if self._error is not None:
            raise self._error
        assert self._raw is not None
        return self._raw


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_stage_returns_hermes_response_on_valid_payload() -> None:
    client = _CountingHermes()
    stage = HermesStage(client)

    response = _run(stage.run("привет"))

    assert isinstance(response, HermesResponse)
    assert response.reply == "готово"
    assert response.note.create is False
    assert client.calls == 1


def test_stage_maps_transport_error_to_hermes_failed() -> None:
    client = _CountingHermes(error=HermesClientError("connection reset"))
    stage = HermesStage(client)

    with pytest.raises(HermesStageError) as excinfo:
        _run(stage.run("запрос"))

    assert excinfo.value.status == ErrorCode.HERMES_FAILED.value
    assert excinfo.value.raw is None  # no response body to archive
    assert "connection reset" in excinfo.value.error
    assert client.calls == 1  # no retry


def test_stage_maps_unrepairable_payload_to_invalid_response() -> None:
    garbage = "not json at all"
    client = _CountingHermes(raw=garbage)
    stage = HermesStage(client)

    with pytest.raises(HermesStageError) as excinfo:
        _run(stage.run("вопрос"))

    assert excinfo.value.status == ErrorCode.HERMES_INVALID_RESPONSE.value
    # The ORIGINAL response text is preserved for archiving (the repair
    # pass already happened inside parse_hermes_response).
    assert excinfo.value.raw == garbage
    assert excinfo.value.error
    assert client.calls == 1  # repair is not a second client call
