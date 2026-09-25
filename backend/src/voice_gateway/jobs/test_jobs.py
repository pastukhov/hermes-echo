from __future__ import annotations

import uuid
import hashlib
from pathlib import Path

import pytest

from .store import JobConflict, VoiceJobStore


def test_upload_request_is_deduplicated_by_owner_and_pcm_hash(tmp_path):
    jobs = VoiceJobStore(tmp_path / "jobs.sqlite", tmp_path / "archive")
    jobs.initialize()
    audio = b"\x00\x00" * 80
    first_id = str(uuid.uuid4())
    claimed, first = jobs.claim_upload("mic-a", first_id)
    assert claimed
    stage = Path(first["upload_path"])
    stage.write_bytes(audio)
    size, digest = jobs.hash_file(stage)
    completed = jobs.complete_upload(
        "mic-a", first_id, stage_path=stage, size=size, digest=digest
    )
    pcm_path = Path(completed["audio_path"])
    assert pcm_path.is_file()
    assert first["turn_id"]
    claimed_again, existing = jobs.claim_upload("mic-a", first_id)
    assert not claimed_again
    assert existing["turn_id"] == first["turn_id"]
    assert jobs.verify_duplicate("mic-a", first_id, digest) == first["turn_id"]
    with pytest.raises(JobConflict, match="idempotency_conflict"):
        jobs.verify_duplicate(
            "mic-a", first_id,
            hashlib.sha256(audio + b"\x01\x00").hexdigest(),
        )
    assert jobs.get_by_request("mic-b", first_id) is None


def test_incomplete_running_job_is_interrupted_not_replayed(tmp_path):
    jobs = VoiceJobStore(tmp_path / "jobs.sqlite", tmp_path / "archive")
    jobs.initialize()
    request_id = str(uuid.uuid4())
    _, row = jobs.claim_upload("mic-a", request_id)
    stage = Path(row["upload_path"])
    stage.write_bytes(b"\x00\x00" * 32)
    size, digest = jobs.hash_file(stage)
    row = jobs.complete_upload(
        "mic-a", request_id, stage_path=stage, size=size, digest=digest
    )
    assert jobs.mark_running(row["turn_id"])
    jobs.recover_after_restart()
    assert jobs.get(row["turn_id"])["status"] == "interrupted"
    assert jobs.queued() == []


def test_upload_limit_is_enforced_before_commit(tmp_path):
    jobs = VoiceJobStore(tmp_path / "jobs.sqlite", tmp_path / "archive", max_bytes=8)
    jobs.initialize()
    request_id = str(uuid.uuid4())
    _, row = jobs.claim_upload("mic-a", request_id)
    stage = Path(row["upload_path"])
    stage.write_bytes(b"\x00" * 10)
    size, digest = jobs.hash_file(stage)
    with pytest.raises(JobConflict, match="audio_too_large"):
        jobs.complete_upload("mic-a", request_id, stage_path=stage, size=size, digest=digest)


def test_abandoned_partial_upload_is_recoverable_with_same_request_id(tmp_path):
    jobs = VoiceJobStore(tmp_path / "jobs.sqlite", tmp_path / "archive")
    jobs.initialize()
    request_id = str(uuid.uuid4())
    claimed, first = jobs.claim_upload("mic-a", request_id)
    assert claimed
    Path(first["upload_path"]).write_bytes(b"partial")
    jobs.recover_uploads()
    assert jobs.get(first["turn_id"])["status"] == "upload_failed"
    assert not Path(first["upload_path"]).exists()
    claimed_again, retry = jobs.claim_upload("mic-a", request_id)
    assert claimed_again
    assert retry["turn_id"] == first["turn_id"]


def test_default_limit_accepts_ten_minutes_and_rejects_extra_sample(tmp_path):
    jobs = VoiceJobStore(tmp_path / 'jobs.sqlite', tmp_path / 'archive')
    jobs.initialize()
    size = 16000 * 2 * 600
    request_id = str(uuid.uuid4())
    _, row = jobs.claim_upload('mic', request_id)
    stage = Path(row['upload_path'])
    with stage.open('wb') as output:
        output.truncate(size)
    with pytest.raises(JobConflict, match='audio_too_large'):
        jobs.complete_upload('mic', request_id, stage_path=stage, size=size + 2, digest='hash')
    result = jobs.complete_upload('mic', request_id, stage_path=stage, size=size, digest='hash')
    assert result['audio_bytes'] == size
