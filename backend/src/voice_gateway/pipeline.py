"""Shared file-based STT → agent → WAV pipeline for v2 worker jobs."""

from __future__ import annotations

import asyncio
import os
import json
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from backend.src.voice_gateway.agents.base import AgentClient, AgentRequest
from backend.src.voice_gateway.archive import atomic_write_bytes, atomic_write_json
from backend.src.voice_gateway.hermes.stage import HermesStage
from backend.src.voice_gateway.models.hermes_response import HermesResponse
from backend.src.voice_gateway.knowledge.store import KnowledgeStore, KnowledgeConflict
from backend.src.voice_gateway.stt.base import STTClientError, STTProvider
from backend.src.voice_gateway.models import Transcript
from backend.src.voice_gateway.stt.client import OpenAICompatibleSTT
from backend.src.voice_gateway.tts.base import TTSProvider, TTSProviderError
from backend.src.voice_gateway.tts.openai_compatible import OpenAICompatibleTTS


class VoicePipelineError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class VoicePipeline:
    def __init__(
        self,
        stt: STTProvider | None,
        agent: AgentClient | None,
        hermes: HermesStage | None,
        tts: TTSProvider | None,
        *,
        deadline_seconds: float = 180.0,
        knowledge: KnowledgeStore | None = None,
    ) -> None:
        self.knowledge = knowledge
        self.stt = stt
        self.agent = agent
        self.hermes = hermes
        self.tts = tts
        self.deadline_seconds = deadline_seconds

    async def run(
        self,
        job: dict[str, Any],
        report_progress: Callable[[str], None] | None = None,
    ) -> Path:
        if self.stt is None:
            raise VoicePipelineError("stt_failed")
        if self.agent is None and self.hermes is None:
            raise VoicePipelineError("agent_unavailable")
        turn_dir = Path(job["audio_path"]).parent
        input_wav = turn_dir / "input.wav"
        output_part = turn_dir / "reply.wav.part"
        output_wav = turn_dir / "reply.wav"
        try:
            async with asyncio.timeout(self.deadline_seconds):
                await asyncio.to_thread(self._pcm_to_wav, Path(job["audio_path"]), input_wav)
                if report_progress:
                    report_progress("transcribing")
                if (turn_dir / "transcript.txt").exists():
                    transcript = Transcript(text=(turn_dir / "transcript.txt").read_text(), language="ru")
                elif isinstance(self.stt, OpenAICompatibleSTT):
                    transcript = await self.stt.transcribe_async(input_wav)
                else:
                    transcript = await asyncio.to_thread(self.stt.transcribe, input_wav)
                if not transcript.text.strip():
                    raise VoicePipelineError("stt_failed")
                atomic_write_bytes(turn_dir / "transcript.txt", transcript.text.encode("utf-8"))
                source_id, context, receipt = None, None, None
                if self.knowledge is not None:
                    source_id = await asyncio.to_thread(self.knowledge.capture, job, transcript.text)
                    receipt = await asyncio.to_thread(self.knowledge.receipt, source_id)
                    context_path = turn_dir / "knowledge-context.json"
                    if context_path.exists():
                        context = json.loads(context_path.read_text())
                    else:
                        context = await asyncio.to_thread(self.knowledge.context, job["device_id"], source_id, transcript.text)
                        atomic_write_json(context_path, context)
                if report_progress:
                    report_progress("thinking")
                if receipt is not None:
                    response = HermesResponse(reply=receipt["reply"])
                    metadata = {"provider": "knowledge", "model": None}
                elif self.agent is not None:
                    reply = await self.agent.complete(
                        AgentRequest(job["request_id"], job["device_id"], transcript.text, context)
                    )
                    response = HermesResponse.model_validate(
                        {
                            "reply": reply.reply,
                            "note": reply.note or {
                                "create": False, "title": "", "content": "", "tags": []
                            },
                        }
                    )
                    metadata = {"provider": reply.provider, "model": reply.model}
                else:
                    response = await self.hermes.run(transcript.text)
                    metadata = {"provider": "hermes", "model": None}
                if self.knowledge is not None and receipt is None:
                    atomic_write_json(turn_dir / "knowledge-proposal.json", response.model_dump())
                    try:
                        receipt = await asyncio.to_thread(self.knowledge.publish, source_id, job["device_id"],
                                                          response.note, context, response.reply)
                        response.reply = receipt["reply"]
                    except KnowledgeConflict:
                        receipt = {"source_id": source_id, "status": "needs_review"}
                        response.reply = "Исходная запись сохранена. Обновление заметок требует проверки; существующие правки не перезаписаны."
                    atomic_write_json(turn_dir / "knowledge-result.json", receipt)
                atomic_write_bytes(turn_dir / "reply.txt", response.reply.encode("utf-8"))
                if self.tts is None:
                    raise VoicePipelineError("tts_failed")
                if report_progress:
                    report_progress("synthesizing")
                synthesis = asyncio.create_task(
                    asyncio.to_thread(self.tts.synthesize, response.reply, output_part)
                )
                try:
                    await asyncio.shield(synthesis)
                except asyncio.CancelledError:
                    synthesis.add_done_callback(lambda _: output_part.unlink(missing_ok=True))
                    raise
                self._validate_reply_wav(output_part)
                os.replace(output_part, output_wav)
                atomic_write_bytes(turn_dir / "transcript.txt", transcript.text.encode("utf-8"))
                atomic_write_bytes(turn_dir / "reply.txt", response.reply.encode("utf-8"))
                response_filename = (
                    "hermes-response.json" if metadata["provider"] == "hermes"
                    else "agent-response.json"
                )
                atomic_write_json(
                    turn_dir / response_filename,
                    {**metadata, "reply": response.reply, "note": response.note.model_dump()},
                )
                atomic_write_json(
                    turn_dir / "metadata.json",
                    {
                        "turn_id": job["turn_id"],
                        "device_id": job["device_id"],
                        "started_at": job["created_at"],
                        "finished_at": datetime.now(UTC).isoformat(),
                        "input_bytes": job["audio_bytes"],
                        "status": "success",
                        "transcript": transcript.text,
                        "reply": response.reply,
                        **metadata,
                    },
                )
                return output_wav
        except TimeoutError:
            output_part.unlink(missing_ok=True)
            raise VoicePipelineError("agent_timeout") from None
        except VoicePipelineError:
            output_part.unlink(missing_ok=True)
            raise
        except STTClientError:
            output_part.unlink(missing_ok=True)
            raise VoicePipelineError("stt_failed") from None
        except TTSProviderError:
            output_part.unlink(missing_ok=True)
            raise VoicePipelineError("tts_failed") from None
        except Exception as exc:
            output_part.unlink(missing_ok=True)
            if hasattr(exc, "status") and getattr(exc, "status") in {
                "hermes_failed", "hermes_invalid_response"
            }:
                raise VoicePipelineError(getattr(exc, "status")) from None
            code = getattr(exc, "code", None)
            if code in {
                "agent_auth_required", "agent_rate_limited", "agent_timeout",
                "agent_invalid_response", "permission_required", "interrupted",
                "agent_unavailable",
            }:
                raise VoicePipelineError(code) from None
            raise VoicePipelineError("agent_unavailable") from None

    @staticmethod
    def _pcm_to_wav(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            with source.open("rb") as pcm:
                while chunk := pcm.read(64 * 1024):
                    wav.writeframesraw(chunk)

    @staticmethod
    def _validate_reply_wav(path: Path) -> None:
        try:
            with wave.open(str(path), "rb") as wav:
                if (
                    wav.getnchannels() != 1
                    or wav.getsampwidth() != 2
                    or wav.getframerate() != 24000
                ):
                    raise VoicePipelineError("tts_failed")
                expected = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
                actual = wav.getnframes() and path.stat().st_size - 44
                if expected <= 0 or actual != expected:
                    raise VoicePipelineError("tts_failed")
        except (OSError, wave.Error, EOFError):
            raise VoicePipelineError("tts_failed") from None
