"""Provider-neutral request/reply contract for voice agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AgentRequest:
    request_id: str
    device_id: str
    transcript: str


@dataclass(frozen=True, slots=True)
class AgentReply:
    reply: str
    note: dict[str, Any] | None
    thread_id: str
    model: str | None
    provider: str


@dataclass(frozen=True, slots=True)
class AgentFailure:
    code: str
    message: str
    retryable: bool


class AgentClientError(Exception):
    """Safe adapter failure; never includes request or authorization data."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class AgentClient(Protocol):
    async def complete(self, request: AgentRequest) -> AgentReply: ...

    async def cancel(self, request_id: str) -> None: ...
