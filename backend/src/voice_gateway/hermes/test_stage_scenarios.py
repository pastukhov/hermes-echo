"""ТЗ §44 "Hermes" unit tests: the five required stage-level scenarios.

Each scenario below is a separate, independently named test (no shared
parametrization) exercising the real production code paths:

* :class:`HermesStage` (the single-call + single-repair contract, §21-24);
* :func:`parse_hermes_response` / :func:`repair_payload` (§22, §24);
* :class:`OpenAICompatibleHermesClient` for the timeout scenario, driven
  through ``httpx.MockTransport`` — no real sockets.

"Repair call count" is observed through the production ``repair_payload``
function itself: it returns the ORIGINAL text unchanged when no mechanical
fix applies (0 repairs performed/used) and a different candidate when one
does (1 repair performed/used, exactly the ТЗ §24 "one repair attempt"
bound). This mirrors exactly what :func:`parse_hermes_response` does
internally, without adding any test-only seam to production code.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.common.error_codes import ErrorCode
from backend.src.voice_gateway.config import HermesConfig
from backend.src.voice_gateway.hermes.base import HermesClient, HermesClientError
from backend.src.voice_gateway.hermes.client import OpenAICompatibleHermesClient
from backend.src.voice_gateway.hermes.stage import HermesStage, HermesStageError
from backend.src.voice_gateway.hermes.validation import repair_payload
from backend.src.voice_gateway.models.hermes_response import HermesResponse


class _ScriptedHermes(HermesClient):
    """Returns a fixed raw payload; counts every ``complete`` call."""

    def __init__(self, raw: str) -> None:
        self._raw = raw
        self.calls = 0
        self.last_transcript: str | None = None

    async def complete(self, transcript: str) -> str:
        self.calls += 1
        self.last_transcript = transcript
        return self._raw


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# 1. valid response
# ---------------------------------------------------------------------------

def test_valid_response_is_parsed_without_any_repair() -> None:
    """A valid §22 payload becomes a HermesResponse with fields preserved,
    and the client/repair machinery is invoked exactly the minimum amount:
    one Hermes call, zero repairs performed."""
    raw = (
        '{"reply": "Записал.", "note": {"create": true, "title": "Покупки", '
        '"content": "- USB-C кабель", "tags": ["voice"]}}'
    )
    # 0 repairs: repair_payload is a no-op on a clean payload.
    assert repair_payload(raw) == raw

    client = _ScriptedHermes(raw)
    stage = HermesStage(client)

    response = _run(stage.run("запиши заметку"))

    assert isinstance(response, HermesResponse)
    assert response.reply == "Записал."
    assert response.note.create is True
    assert response.note.title == "Покупки"
    assert response.note.tags == ["voice"]
    assert client.calls == 1  # single Hermes call, no repair round-trip


# ---------------------------------------------------------------------------
# 2. invalid JSON (isolated from repair — no mechanical fix applies)
# ---------------------------------------------------------------------------

def test_invalid_json_with_no_repairable_pattern_raises_typed_error() -> None:
    """Syntactically invalid JSON that no mechanical repair pattern touches
    (no fence, no <json> markers, no trailing comma) fails at the
    parse/validation boundary with the production typed exception. This
    isolates the "invalid JSON" fact from repair behavior, which is
    covered by the two scenarios below."""
    garbage = "просто текст без какого-либо JSON"
    # 0 repairs: nothing mechanical for repair_payload to latch onto.
    assert repair_payload(garbage) == garbage

    client = _ScriptedHermes(garbage)
    stage = HermesStage(client)

    with pytest.raises(HermesStageError) as excinfo:
        _run(stage.run("вопрос"))

    assert excinfo.value.status == ErrorCode.HERMES_INVALID_RESPONSE.value
    assert excinfo.value.raw == garbage  # original text preserved for archive
    assert client.calls == 1


# ---------------------------------------------------------------------------
# 3. repair success
# ---------------------------------------------------------------------------

def test_repair_success_recovers_valid_json_with_exactly_one_repair() -> None:
    """The first payload is invalid as-is (fenced), the single repair pass
    recovers valid JSON, and the repair operates on the ORIGINAL invalid
    text/context (ТЗ §24), never on a further-mutated candidate."""
    raw = '```json\n{"reply": "Починено."}\n```'

    repaired = repair_payload(raw)
    assert repaired != raw  # exactly one repair candidate is produced
    assert repaired == raw.replace("```json\n", "").replace("\n```", "")

    client = _ScriptedHermes(raw)
    stage = HermesStage(client)

    response = _run(stage.run("вопрос"))

    assert isinstance(response, HermesResponse)
    assert response.reply == "Починено."
    assert client.calls == 1  # repair happens inside the parser, not a 2nd call


# ---------------------------------------------------------------------------
# 4. repair failure
# ---------------------------------------------------------------------------

def test_repair_failure_falls_back_safely_after_one_repair_attempt() -> None:
    """A payload that IS mechanically repairable (fenced, becomes valid
    JSON once unfenced) but still fails schema validation after the single
    repair attempt: repair is attempted exactly once on the original
    invalid output, then production surfaces the bounded terminal failure
    — no further/unbounded retry, original raw preserved for the archive
    contract, status mapped to hermes_invalid_response."""
    raw = '```json\n{"reply": 123}\n```'  # unfences to valid JSON, wrong schema

    repaired = repair_payload(raw)
    assert repaired != raw  # exactly one repair candidate WAS attempted
    assert repaired == '{"reply": 123}'  # valid JSON, but schema still invalid

    client = _ScriptedHermes(raw)
    stage = HermesStage(client)

    with pytest.raises(HermesStageError) as excinfo:
        _run(stage.run("вопрос"))

    assert excinfo.value.status == ErrorCode.HERMES_INVALID_RESPONSE.value
    assert excinfo.value.raw == raw  # ORIGINAL text, not the repaired attempt
    assert excinfo.value.error
    assert client.calls == 1  # no further retry after the one repair attempt


# ---------------------------------------------------------------------------
# 5. timeout
# ---------------------------------------------------------------------------

def test_timeout_maps_to_hermes_failed_without_retry() -> None:
    """HERMES_TIMEOUT is applied to the real client's httpx transport and a
    transport-level timeout maps to hermes_failed at the stage boundary,
    with no unbounded retry (ТЗ §24/§31/§32). Driven end-to-end through
    the production ``OpenAICompatibleHermesClient`` + ``HermesStage`` with
    an ``httpx.MockTransport`` — no real network."""
    config = HermesConfig(
        base_url="https://hermes.internal",
        api_key="sekret",
        model="hermes-voice",
        timeout=7.5,
    )
    # The configured HERMES_TIMEOUT is what the production client applies
    # to httpx when it owns the transport (client=None path).
    unconfigured_probe = OpenAICompatibleHermesClient(config, system_prompt="p")
    try:
        assert unconfigured_probe._client.timeout == httpx.Timeout(7.5)
    finally:
        _run(unconfigured_probe.aclose())

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("read timed out", request=request)

    injected = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleHermesClient(config, system_prompt="p", client=injected)
    stage = HermesStage(client)

    with pytest.raises(HermesStageError) as excinfo:
        _run(stage.run("запрос"))

    assert excinfo.value.status == ErrorCode.HERMES_FAILED.value
    assert excinfo.value.raw is None  # transport failure: no response body
    assert calls["n"] == 1  # a timed-out turn is never retried
