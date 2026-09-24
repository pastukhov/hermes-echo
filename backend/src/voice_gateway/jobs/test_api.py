from __future__ import annotations

import asyncio
import uuid
import wave
from pathlib import Path

import httpx
from fastapi import FastAPI

from .api import install_voice_job_routes
from .store import VoiceJobStore
from .worker import VoiceJobWorker


def test_duplicate_upload_is_idempotent_and_owner_is_enforced(tmp_path):
    async def scenario():
        root = tmp_path / "archive"
        store = VoiceJobStore(tmp_path / "jobs.sqlite", root)

        async def process(job, _report_progress):
            output = Path(job["audio_path"]).with_name("reply.wav")
            with wave.open(str(output), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(b"\x00\x00" * 50)
            return output

        worker = VoiceJobWorker(store, process)
        await worker.start()
        app = FastAPI()
        install_voice_job_routes(
            app, store, worker, {"mic-a": "device-secret", "mic-b": "other-secret"}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            request_id = str(uuid.uuid4())
            headers = {
                "X-Protocol-Version": "2",
                "X-Request-Id": request_id,
                "X-Device-Id": "mic-a",
                "Authorization": "Bearer device-secret",
            }
            audio = b"\x00\x00" * 100
            first = await client.post("/api/v2/voice/turns", content=audio, headers=headers)
            duplicate = await client.post("/api/v2/voice/turns", content=audio, headers=headers)
            assert first.status_code == duplicate.status_code == 202
            assert first.json()["turn_id"] == duplicate.json()["turn_id"]
            other_body = await client.post(
                "/api/v2/voice/turns", content=audio,
                headers={**headers, "X-Device-Id": "mic-b"},
            )
            assert other_body.status_code == 401
            foreign = await client.get(
                f"/api/v2/voice/turns/{first.json()['turn_id']}",
                headers={
                    **headers,
                    "X-Device-Id": "mic-b",
                    "Authorization": "Bearer other-secret",
                },
            )
            assert foreign.status_code == 404
            await worker.queue.join()
            result = await client.get(
                f"/api/v2/voice/turns/{first.json()['turn_id']}/audio", headers=headers
            )
            assert result.status_code == 200
            assert result.headers["content-type"] == "audio/wav"
        await worker.close()

    asyncio.run(scenario())


def test_v2_authentication_happens_before_request_body_is_consumed(tmp_path):
    async def scenario():
        store = VoiceJobStore(tmp_path / "jobs.sqlite", tmp_path / "archive")
        worker = VoiceJobWorker(store, lambda _job, _report_progress: None)
        app = FastAPI()
        install_voice_job_routes(app, store, worker, {"mic-a": "secret"})
        received = False

        async def body():
            nonlocal received
            received = True
            yield b"\x00\x00"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v2/voice/turns",
                content=body(),
                headers={
                    "X-Protocol-Version": "2",
                    "X-Request-Id": str(uuid.uuid4()),
                    "X-Device-Id": "mic-a",
                    "Authorization": "Bearer wrong",
                },
            )
        assert response.status_code == 401
        assert not received

    asyncio.run(scenario())


def test_v2_rejects_audio_over_limit(tmp_path):
    async def scenario():
        store = VoiceJobStore(tmp_path / "jobs.sqlite", tmp_path / "archive", max_bytes=8)
        worker = VoiceJobWorker(store, lambda _job, _report_progress: None)
        await worker.start()
        app = FastAPI()
        install_voice_job_routes(app, store, worker, {"mic-a": "secret"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v2/voice/turns",
                content=b"\x00" * 10,
                headers={
                    "X-Protocol-Version": "2",
                    "X-Request-Id": str(uuid.uuid4()),
                    "X-Device-Id": "mic-a",
                    "Authorization": "Bearer secret",
                },
            )
        assert response.status_code == 413
        await worker.close()

    asyncio.run(scenario())
