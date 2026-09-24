"""Production app provider wiring from environment configuration."""
import asyncio

import httpx

from backend.src.voice_gateway.agents.base import AgentReply
from backend.src.voice_gateway.app import create_app
from backend.src.voice_gateway.hermes.client import OpenAICompatibleHermesClient
from backend.src.voice_gateway.stt.client import OpenAICompatibleSTT
from backend.src.voice_gateway.tts.openai_compatible import OpenAICompatibleTTS
from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt.fake import FakeSTT


def test_create_app_builds_configured_runtime_providers(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_BASE_URL", "http://127.0.0.1:8642/v1")
    monkeypatch.setenv("HERMES_API_KEY", "hermes-test-key")
    monkeypatch.setenv("HERMES_MODEL", "upstage/solar-pro4:free")
    monkeypatch.setenv("STT_BASE_URL", "https://stt.example/v1")
    monkeypatch.setenv("STT_API_KEY", "stt-test-key")
    monkeypatch.setenv("STT_MODEL", "gpt-4o-mini-transcribe")
    monkeypatch.setenv("TTS_BASE_URL", "https://tts.example/v1/audio/speech")
    monkeypatch.setenv("TTS_API_KEY", "tts-test-key")
    monkeypatch.setenv("TTS_MODEL", "gpt-4o-mini-tts")
    monkeypatch.setenv("TTS_VOICE", "alloy")

    app = create_app(archive_root=tmp_path / "archive")

    assert isinstance(app.state.stt_provider, OpenAICompatibleSTT)
    assert isinstance(app.state.hermes_client, OpenAICompatibleHermesClient)
    assert isinstance(app.state.tts_provider, OpenAICompatibleTTS)
    assert app.state.hermes_client._config.model == "upstage/solar-pro4:free"
    assert app.state.hermes_client._config.api_key == "hermes-test-key"
    assert app.state.tts_provider._config.model == "gpt-4o-mini-tts"


def test_create_app_keeps_optional_stages_disabled_without_config(monkeypatch, tmp_path):
    for name in (
        "HERMES_BASE_URL", "HERMES_API_KEY", "STT_BASE_URL", "STT_API_KEY",
        "TTS_BASE_URL", "TTS_API_KEY", "TTS_MODEL", "TTS_VOICE",
    ):
        monkeypatch.delenv(name, raising=False)

    app = create_app(archive_root=tmp_path / "archive")

    assert app.state.hermes_client is None
    assert app.state.tts_provider is None


def test_codex_is_explicit_provider_and_does_not_require_hermes(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_AGENT_PROVIDER", "codex")
    monkeypatch.setenv("CODEX_AGENT_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("CODEX_AGENT_TOKEN", "adapter-only-test-token")
    monkeypatch.setenv("STT_BASE_URL", "https://stt.example/v1")
    monkeypatch.delenv("HERMES_BASE_URL", raising=False)

    class FakeAgent:
        async def complete(self, request):
            return AgentReply("ok", None, "thread", "model", "codex")

    app = create_app(archive_root=tmp_path / "archive", agent=FakeAgent())
    assert app.state.agent_provider == "codex"
    assert app.state.agent_client is not None
    assert app.state.hermes_client is None


def test_codex_missing_adapter_config_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_AGENT_PROVIDER", "codex")
    monkeypatch.setenv("STT_BASE_URL", "https://stt.example/v1")
    monkeypatch.delenv("HERMES_BASE_URL", raising=False)
    monkeypatch.delenv("CODEX_AGENT_URL", raising=False)
    monkeypatch.delenv("CODEX_AGENT_TOKEN", raising=False)

    app = create_app(archive_root=tmp_path / "archive")
    assert app.state.agent_provider == "codex"
    assert app.state.agent_client is None


def test_codex_provider_receives_device_and_turn_ids_and_keeps_reply_shape(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("VOICE_AGENT_PROVIDER", "hermes")
    monkeypatch.delenv("HERMES_BASE_URL", raising=False)

    class FakeAgent:
        request = None

        async def complete(self, request):
            self.request = request
            return AgentReply("Готово", None, "thread-1", "model-1", "codex")

    agent = FakeAgent()
    app = create_app(
        archive_root=tmp_path / "archive",
        stt=FakeSTT(Transcript(text="тест", language="ru")),
        agent=agent,
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/voice/turn",
                content=b"\x00\x00" * 160,
                headers={"X-Device-Id": "mic-a"},
            )
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"

    asyncio.run(run())
    assert agent.request.device_id == "mic-a"
    assert agent.request.transcript == "тест"
    assert len(agent.request.request_id) == 36
