from __future__ import annotations

import asyncio
import uuid
import wave
from pathlib import Path

from .store import VoiceJobStore
from .worker import VoiceJobWorker


def test_worker_runs_durable_queue_and_marks_ready(tmp_path):
    async def scenario():
        store = VoiceJobStore(tmp_path / "jobs.sqlite", tmp_path / "archive")
        store.initialize()
        _, row = store.claim_upload("mic-a", str(uuid.uuid4()))
        part = Path(row["upload_path"])
        part.write_bytes(b"\x00\x00" * 40)
        size, digest = store.hash_file(part)
        row = store.complete_upload(
            "mic-a", row["request_id"], stage_path=part, size=size, digest=digest
        )

        async def process(job, _report_progress):
            result = Path(job["audio_path"]).with_name("reply.wav")
            with wave.open(str(result), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(b"\x00\x00" * 40)
            return result

        worker = VoiceJobWorker(store, process)
        await worker.start()
        await worker.queue.join()
        assert store.get(row["turn_id"])["status"] == "ready"
        await worker.close()

    asyncio.run(scenario())


def test_worker_persists_pipeline_progress_for_status_polling(tmp_path):
    async def scenario():
        store = VoiceJobStore(tmp_path / "jobs.sqlite", tmp_path / "archive")
        store.initialize()
        _, row = store.claim_upload("mic-a", str(uuid.uuid4()))
        part = Path(row["upload_path"])
        part.write_bytes(b"\x00\x00" * 40)
        size, digest = store.hash_file(part)
        row = store.complete_upload(
            "mic-a", row["request_id"], stage_path=part, size=size, digest=digest
        )
        observed = []

        async def process(job, report_progress):
            report_progress("transcribing")
            observed.append(store.get(job["turn_id"])["status"])
            report_progress("thinking")
            observed.append(store.get(job["turn_id"])["status"])
            report_progress("synthesizing")
            observed.append(store.get(job["turn_id"])["status"])
            result = Path(job["audio_path"]).with_name("reply.wav")
            with wave.open(str(result), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(b"\x00\x00" * 40)
            return result

        worker = VoiceJobWorker(store, process)
        await worker.start()
        await worker.queue.join()
        assert observed == ["transcribing", "thinking", "synthesizing"]
        assert store.get(row["turn_id"])["status"] == "ready"
        await worker.close()

    asyncio.run(scenario())


def test_worker_does_not_replay_running_job_after_restart(tmp_path):
    async def scenario():
        store = VoiceJobStore(tmp_path / "jobs.sqlite", tmp_path / "archive")
        store.initialize()
        _, row = store.claim_upload("mic-a", str(uuid.uuid4()))
        part = Path(row["upload_path"])
        part.write_bytes(b"\x00\x00" * 40)
        size, digest = store.hash_file(part)
        row = store.complete_upload(
            "mic-a", row["request_id"], stage_path=part, size=size, digest=digest
        )
        assert store.mark_running(row["turn_id"])
        called = False

        async def process(_job, _report_progress):
            nonlocal called
            called = True
            return tmp_path / "reply.wav"

        worker = VoiceJobWorker(store, process)
        await worker.start()
        assert store.get(row["turn_id"])["status"] == "interrupted"
        assert not called
        await worker.close()

    asyncio.run(scenario())
