from __future__ import annotations

import asyncio

import httpx
import pytest

from codex_voice.app import create_app
from codex_voice.service import AgentRequest, AgentService
from test_sessions import FakeRuntime


def test_api_requires_bearer_and_returns_async_turn_contract(tmp_path) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        service = AgentService(tmp_path / "api.sqlite", runtime)
        await service.start()
        app = create_app(service, token="adapter-secret")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            body = {"request_id": "r1", "device_id": "mic-a", "transcript": "Привет"}
            unauthorized = await client.post("/v1/agent/turns", json=body)
            assert unauthorized.status_code == 401
            accepted = await client.post(
                "/v1/agent/turns",
                json=body,
                headers={"Authorization": "Bearer adapter-secret"},
            )
            assert accepted.status_code == 202
            assert accepted.json()["request_id"] == "r1"
            assert (await service.wait("r1")).status == "completed"
            completed = await client.get(
                "/v1/agent/turns/r1",
                headers={"Authorization": "Bearer adapter-secret"},
            )
            assert completed.json()["reply"]["reply"] == "reply"
            assert completed.json()["reply"]["provider"] == "codex"
            assert len(runtime.calls) == 1
        await service.close()

    asyncio.run(scenario())


def test_api_conflict_does_not_echo_transcript_or_token(tmp_path) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.block = True
        service = AgentService(tmp_path / "conflict.sqlite", runtime)
        await service.start()
        app = create_app(service, token="never-return-this")
        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": "Bearer never-return-this"}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/v1/agent/turns",
                json={"request_id": "r1", "device_id": "mic-a", "transcript": "secret phrase"},
                headers=headers,
            )
            await runtime.started.wait()
            conflict = await client.post(
                "/v1/agent/turns",
                json={"request_id": "r2", "device_id": "mic-a", "transcript": "other"},
                headers=headers,
            )
            assert first.status_code == 202
            assert conflict.status_code == 409
            assert "secret phrase" not in conflict.text
            assert "never-return-this" not in conflict.text
        await service.close()

    asyncio.run(scenario())
