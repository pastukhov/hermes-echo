"""Production app provider wiring from environment configuration."""
from backend.src.voice_gateway.app import create_app
from backend.src.voice_gateway.hermes.client import OpenAICompatibleHermesClient
from backend.src.voice_gateway.stt.client import OpenAICompatibleSTT
from backend.src.voice_gateway.tts.openai_compatible import OpenAICompatibleTTS


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
