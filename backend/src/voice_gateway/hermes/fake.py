"""Deterministic Hermes stand-in for tests.

Returns a pre-configured RAW response string and enforces the single-call
rule (ТЗ §21–24): the fake counts one turn as one sequence of ``complete``
calls, so a second call WITHIN the same turn raises. The guard is per turn,
not per instance — one fake legitimately serves many turns (e.g. the
multi-turn health test), and every turn must still see exactly one call.
"""
from __future__ import annotations

from backend.src.voice_gateway.hermes.base import HermesClient


class FakeHermes(HermesClient):
    def __init__(self, raw: str) -> None:
        self._raw = raw
        self.calls = 0
        self.last_transcript: str | None = None
        self._turn = 0

    async def complete(self, transcript: str) -> str:
        self.calls += 1
        if self.calls > self._turn + 1:
            # More than one call inside the current turn.
            raise AssertionError("Hermes called more than once in one turn")
        self.last_transcript = transcript
        return self._raw

    def end_turn(self) -> None:
        """Mark the current turn as finished (advances the call budget)."""
        self._turn = self.calls
