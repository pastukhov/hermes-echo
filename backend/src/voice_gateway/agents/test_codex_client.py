from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from .base import AgentClientError, AgentRequest
from .codex_client import CodexAgentClient


def test_lost_submit_response_retries_same_id_then_polls_to_completion() -> None:
    async def scenario() -> None:
        attempts = 0
        get_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts, get_count
            assert request.headers["Authorization"] == "Bearer adapter-secret"
            if request.method == "POST" and request.url.path.endswith("/turns"):
                attempts += 1
                body = json.loads(request.content)
                assert body["request_id"] == "stable-id"
                if attempts == 1:
                    raise httpx.ConnectError("response was lost", request=request)
                return httpx.Response(202, json={"status": "queued"})
            if request.method == "GET":
                get_count += 1
                if get_count == 1:
                    return httpx.Response(200, json={"status": "running"})
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "reply": {
                            "reply": "Здравствуйте",
                            "note": None,
                            "thread_id": "thread-1",
                            "model": "gpt-test",
                        },
                    },
                )
            raise AssertionError(request.url)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        agent = CodexAgentClient(
            "http://127.0.0.1:8765", "adapter-secret", client=http,
            poll_interval_seconds=0.001,
        )
        try:
            result = await agent.complete(AgentRequest("stable-id", "mic-a", "Привет"))
            assert result.reply == "Здравствуйте"
            assert result.thread_id == "thread-1"
            assert attempts == 2
        finally:
            await http.aclose()

    asyncio.run(scenario())


def test_deadline_cancels_agent_turn() -> None:
    async def scenario() -> None:
        cancelled = False

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal cancelled
            if request.method == "POST" and request.url.path.endswith("/cancel"):
                cancelled = True
                return httpx.Response(200, json={"status": "cancelled"})
            if request.method == "GET":
                return httpx.Response(200, json={"status": "running"})
            return httpx.Response(202, json={"status": "running"})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        agent = CodexAgentClient(
            "http://localhost:8765", "secret", client=http,
            deadline_seconds=0.005, poll_interval_seconds=0.002,
        )
        try:
            with pytest.raises(AgentClientError) as error:
                await agent.complete(AgentRequest("r1", "mic-a", "wait"))
            assert error.value.code == "agent_timeout"
            assert cancelled
        finally:
            await http.aclose()

    asyncio.run(scenario())


def test_client_rejects_non_loopback_endpoint_and_invalid_agent_payload() -> None:
    with pytest.raises(ValueError, match="loopback"):
        CodexAgentClient("http://192.168.1.10:8765", "secret")
    with pytest.raises(ValueError, match="loopback"):
        CodexAgentClient("https://127.0.0.1:8765", "secret")


def test_error_response_does_not_echo_remote_detail_text():
    response = httpx.Response(
        503,
        json={"detail": {"code": "agent_unavailable", "message": "Bearer secret transcript"}},
    )
    with pytest.raises(AgentClientError) as error:
        CodexAgentClient._raise_response(response)
    assert error.value.code == "agent_unavailable"
    assert "secret" not in str(error.value)
    assert "transcript" not in str(error.value)
