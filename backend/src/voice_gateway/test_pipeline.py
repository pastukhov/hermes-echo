from __future__ import annotations

import asyncio
import uuid
import wave
from pathlib import Path

import pytest

from backend.src.voice_gateway.agents.base import AgentReply
from backend.src.voice_gateway.models import TTSResult, Transcript
from backend.src.voice_gateway.pipeline import VoicePipeline, VoicePipelineError


class FakeSTT:
    def transcribe(self, path):
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
        return Transcript(text="тестовая фраза", language="ru")


class FakeAgent:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return AgentReply("ответ", None, "thread-1", "model-1", "codex")


class FakeTTS:
    def __init__(self, rate=24000):
        self.rate = rate

    def synthesize(self, text, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.rate)
            wav.writeframes(b"\x00\x00" * 100)
        return TTSResult(wav_path=output, sample_rate=self.rate)


def test_pipeline_runs_agent_and_publishes_only_valid_24khz_wav(tmp_path):
    async def scenario():
        audio = tmp_path / "input.pcm"
        audio.write_bytes(b"\x00\x00" * 200)
        agent = FakeAgent()
        pipeline = VoicePipeline(FakeSTT(), agent, None, FakeTTS())
        stages = []
        output = await pipeline.run(
            {
                "audio_path": str(audio),
                "turn_id": str(uuid.uuid4()),
                "request_id": str(uuid.uuid4()),
                "device_id": "mic-a",
                "created_at": "2026-09-24T00:00:00+00:00",
                "audio_bytes": audio.stat().st_size,
            },
            report_progress=stages.append,
        )
        assert output.is_file()
        with wave.open(str(output), "rb") as wav:
            assert wav.getframerate() == 24000
        assert agent.requests[0].device_id == "mic-a"
        assert agent.requests[0].transcript == "тестовая фраза"
        assert stages == ["transcribing", "thinking", "synthesizing"]
        assert (tmp_path / "reply.txt").read_text() == "ответ"
        assert not (tmp_path / "reply.wav.part").exists()

    asyncio.run(scenario())


def test_pipeline_rejects_wrong_reply_sample_rate(tmp_path):
    async def scenario():
        audio = tmp_path / "input.pcm"
        audio.write_bytes(b"\x00\x00" * 20)
        pipeline = VoicePipeline(FakeSTT(), FakeAgent(), None, FakeTTS(rate=16000))
        with pytest.raises(VoicePipelineError) as error:
            await pipeline.run(
                {
                    "audio_path": str(audio), "turn_id": str(uuid.uuid4()),
                    "request_id": "r1", "device_id": "mic-a",
                    "created_at": "2026-09-24T00:00:00+00:00",
                    "audio_bytes": audio.stat().st_size,
                }
            )
        assert error.value.code == "tts_failed"
        assert not (tmp_path / "reply.wav").exists()

    asyncio.run(scenario())
