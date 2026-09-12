"""Tests for the OpenAI-compatible Hermes client (ТЗ sections 16, 21).

Uses ``httpx.MockTransport`` — no real network. Covers request shape,
auth header, and all failure paths that must surface as
``HermesClientError`` (pipeline maps to ``hermes_failed``, ТЗ section 32).
"""
import asyncio
import json

import httpx
import pytest

from backend.src.voice_gateway.config import HermesConfig
from backend.src.voice_gateway.hermes import (
    HermesClient,
    HermesClientError,
    OpenAICompatibleHermesClient,
)

PROMPT = "You are the voice assistant."


def _config(**overrides) -> HermesConfig:
    base = {
        "base_url": "https://hermes.internal",
        "api_key": "sekret",
        "model": "hermes-voice",
        "timeout": 120.0,
    }
    base.update(overrides)
    return HermesConfig(**base)


def _make_client(
    transport: httpx.MockTransport,
    config: HermesConfig | None = None,
) -> OpenAICompatibleHermesClient:
    injected = httpx.AsyncClient(transport=transport)
    return OpenAICompatibleHermesClient(
        config or _config(),
        system_prompt=PROMPT,
        client=injected,
    )


def _ok_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def _run(coro):
    return asyncio.run(coro)


class TestSuccessfulRequest:
    def test_returns_raw_content(self):
        raw = json.dumps({"reply": "OK", "note": {"create": False}})
        transport = httpx.MockTransport(lambda req: _ok_response(raw))
        client = _make_client(transport)
        assert _run(client.complete("привет")) == raw

    def test_request_shape(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return _ok_response('{"reply": "OK"}')

        client = _make_client(httpx.MockTransport(handler))
        _run(client.complete("запиши заметку"))

        assert seen["url"] == "https://hermes.internal/chat/completions"
        assert seen["headers"]["authorization"] == "Bearer sekret"
        assert seen["headers"]["content-type"] == "application/json"
        assert seen["body"]["model"] == "hermes-voice"
        assert seen["body"]["messages"][0] == {
            "role": "system",
            "content": PROMPT,
        }
        assert seen["body"]["messages"][1] == {
            "role": "user",
            "content": "запиши заметку",
        }

    def test_no_api_key_means_no_auth_header(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            return _ok_response('{"reply": "OK"}')

        client = _make_client(
            httpx.MockTransport(handler), config=_config(api_key="")
        )
        _run(client.complete("привет"))
        assert "authorization" not in seen["headers"]


class TestFailurePaths:
    def test_http_500_raises(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
        client = _make_client(transport)
        with pytest.raises(HermesClientError, match="500"):
            _run(client.complete("привет"))

    def test_non_json_body_raises(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text="nope"))
        client = _make_client(transport)
        with pytest.raises(HermesClientError, match="non-JSON"):
            _run(client.complete("привет"))

    def test_missing_content_key_raises(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"choices": []})
        )
        client = _make_client(transport)
        with pytest.raises(HermesClientError, match="content"):
            _run(client.complete("привет"))

    def test_empty_content_raises(self):
        transport = httpx.MockTransport(lambda req: _ok_response("   "))
        client = _make_client(transport)
        with pytest.raises(HermesClientError, match="empty content"):
            _run(client.complete("привет"))

    def test_timeout_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(HermesClientError, match="failed"):
            _run(client.complete("привет"))

    def test_connection_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(HermesClientError, match="failed"):
            _run(client.complete("привет"))

    def test_empty_transcript_raises_without_http(self):
        called = []

        def handler(request: httpx.Request) -> httpx.Response:
            called.append(1)
            return _ok_response('{"reply": "OK"}')

        client = _make_client(httpx.MockTransport(handler))
        for bad in ("", "   "):
            with pytest.raises(HermesClientError, match="empty transcript"):
                _run(client.complete(bad))
        assert called == []


class TestLifecycleAndContract:
    def test_satisfies_hermes_client_contract(self):
        client = _make_client(httpx.MockTransport(lambda req: _ok_response("{}")))
        assert isinstance(client, HermesClient)

    def test_empty_system_prompt_rejected(self):
        with pytest.raises(ValueError):
            OpenAICompatibleHermesClient(_config(), system_prompt="  ")

    def test_injected_client_not_closed_by_aclose(self):
        injected = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: _ok_response("{}"))
        )
        client = OpenAICompatibleHermesClient(
            _config(), system_prompt=PROMPT, client=injected
        )
        _run(client.aclose())
        assert injected.is_closed is False

    def test_owned_client_is_closed(self):
        client = OpenAICompatibleHermesClient(
            _config(), system_prompt=PROMPT
        )
        _run(client.aclose())
        assert client._client.is_closed is True
        # idempotent
        _run(client.aclose())

    def test_async_context_manager(self):
        async def scenario() -> None:
            async with OpenAICompatibleHermesClient(
                _config(), system_prompt=PROMPT
            ) as client:
                assert isinstance(client, HermesClient)
            assert client._client.is_closed is True

        _run(scenario())
