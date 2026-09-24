"""Tests for the atomic Hermes artifact writers (ТЗ sections 18/19/32).

Covers:
* each artifact lands in the right turn dir and is valid UTF-8;
* hermes-response.json stores the ACTUAL raw response (JSON and non-JSON
  cases) and never invents ``reply`` / ``note`` fields;
* failure metadata contains turn id / status / error type + cause and
  excludes secrets and stack traces;
* atomicity: a failed write leaves the previous complete file intact;
* cross-turn isolation: two different turn dirs never share/overwrite
  each other's artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.src.voice_gateway.archive.hermes_artifacts import (
    HERMES_REQUEST_FILENAME,
    HERMES_RESPONSE_FILENAME,
    REPLY_FILENAME,
    build_hermes_failure_metadata,
    save_hermes_failure_metadata,
    save_hermes_request,
    save_hermes_response,
    save_reply,
)

TURN_A = "11111111-1111-1111-1111-111111111111"
TURN_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def turn_a(tmp_path: Path) -> Path:
    return tmp_path / "2026" / "09" / "13" / TURN_A


@pytest.fixture
def turn_b(tmp_path: Path) -> Path:
    return tmp_path / "2026" / "09" / "13" / TURN_B


def _read_json(path: Path) -> object:
    """Read a file and prove it is valid UTF-8 + valid JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


# --- request -----------------------------------------------------------------


def test_save_hermes_request_writes_valid_utf8_json(turn_a: Path) -> None:
    payload = {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "Ты ассистент."},
            {"role": "user", "content": "Запиши напоминание."},
        ],
    }
    path = save_hermes_request(turn_a, payload)

    assert path == turn_a / HERMES_REQUEST_FILENAME
    assert path.exists()
    assert _read_json(path) == payload


def test_save_hermes_request_cyrillic_stays_readable(turn_a: Path) -> None:
    save_hermes_request(turn_a, {"messages": [{"role": "user", "content": "привет"}]})
    text = (turn_a / HERMES_REQUEST_FILENAME).read_text(encoding="utf-8")
    assert "привет" in text  # ensure_ascii=False


# --- response ------------------------------------------------------------------


def test_save_hermes_response_valid_json_object(turn_a: Path) -> None:
    raw = '{"reply": "Сейчас сделаю.", "note": {"create": true, "title": "t"}}'
    path = save_hermes_response(turn_a, raw)

    assert path == turn_a / HERMES_RESPONSE_FILENAME
    assert _read_json(path) == json.loads(raw)


def test_save_hermes_response_non_json_never_invents_fields(turn_a: Path) -> None:
    raw = "извините, я не понял вашего запроса"
    save_hermes_response(turn_a, raw)

    stored = _read_json(turn_a / HERMES_RESPONSE_FILENAME)
    assert stored == {"raw_response": raw}
    assert "reply" not in stored
    assert "note" not in stored


def test_save_hermes_response_non_dict_json_kept_verbatim(turn_a: Path) -> None:
    # Valid JSON but not an object: preserved verbatim, no fields invented.
    save_hermes_response(turn_a, '["not", "an", "object"]')
    stored = _read_json(turn_a / HERMES_RESPONSE_FILENAME)
    assert stored == {"raw_response": '["not", "an", "object"]'}


# --- reply ---------------------------------------------------------------------


def test_save_reply_writes_utf8_text(turn_a: Path) -> None:
    path = save_reply(turn_a, "Готово, записал.\n")

    assert path == turn_a / REPLY_FILENAME
    assert path.read_text(encoding="utf-8") == "Готово, записал.\n"


# --- failure metadata ----------------------------------------------------------


def test_build_failure_metadata_hermes_failed(turn_a: Path) -> None:
    # Realistic: HermesClientError carries only the HTTP status / cause —
    # the API key is a transport header and never reaches the exception.
    err = RuntimeError("hermes returned HTTP 503")
    meta = build_hermes_failure_metadata(
        turn_id=TURN_A,
        status="hermes_failed",
        error_type="HermesClientError",
        error=err,
        device_id="atom-echo-01",
        started_at="2026-09-13T10:00:00+00:00",
        finished_at="2026-09-13T10:00:05+00:00",
        transcript="привет",
    )

    path = save_hermes_failure_metadata(turn_a, meta)
    assert path == turn_a / "metadata.json"
    stored = _read_json(path)

    assert stored["turn_id"] == TURN_A
    assert stored["status"] == "hermes_failed"
    assert stored["error_type"] == "HermesClientError"
    assert "HTTP 503" in stored["error"]
    assert stored["device_id"] == "atom-echo-01"
    assert stored["started_at"] == "2026-09-13T10:00:00+00:00"
    assert stored["finished_at"] == "2026-09-13T10:00:05+00:00"
    assert stored["transcript"] == "привет"


def test_failure_metadata_excludes_stack_trace() -> None:
    # Defensive: even if a formatted traceback were embedded in the message,
    # only the final exception line survives.
    trace = (
        "Traceback (most recent call last):\n"
        '  File "/app/backend/src/voice_gateway/hermes/client.py", line 71, in complete\n'
        "    raise HermesClientError(...)\n"
        "HermesClientError: connection reset by peer\n"
    )
    meta = build_hermes_failure_metadata(
        turn_id=TURN_A,
        status="hermes_invalid_response",
        error_type="HermesValidationError",
        error=RuntimeError(trace),
    )

    blob = json.dumps(meta, ensure_ascii=False)
    assert "Traceback" not in blob
    assert 'File "/app' not in blob
    assert meta["status"] == "hermes_invalid_response"
    assert meta["error_type"] == "HermesValidationError"
    assert meta["error"] == "HermesClientError: connection reset by peer"
    assert len(meta["error"]) <= 500


def test_failure_metadata_without_error_has_no_error_key() -> None:
    meta = build_hermes_failure_metadata(
        turn_id=TURN_A, status="hermes_failed", error_type="HermesClientError"
    )
    assert "error" not in meta
    assert meta["turn_id"] == TURN_A


# --- atomicity ------------------------------------------------------------------


def test_failed_write_leaves_previous_file_intact(turn_a: Path) -> None:
    save_hermes_request(turn_a, {"model": "m1"})
    before = (turn_a / HERMES_REQUEST_FILENAME).read_text(encoding="utf-8")

    # A directory whose fsync/rename cannot succeed: make the target a
    # directory so os.replace() raises — simulating a crashed mid-write.
    (turn_a / HERMES_REQUEST_FILENAME).unlink()
    (turn_a / HERMES_REQUEST_FILENAME).mkdir()
    try:
        with pytest.raises(OSError):
            save_hermes_request(turn_a, {"model": "m2"})
    finally:
        (turn_a / HERMES_REQUEST_FILENAME).rmdir()

    # No half-written temp files leaked into the turn dir.
    leftovers = [p for p in turn_a.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    # And the writer is usable again: a fresh write replaces the dir-free
    # target cleanly (the old content is gone, not mixed).
    save_hermes_request(turn_a, {"model": "m3"})
    assert _read_json(turn_a / HERMES_REQUEST_FILENAME) == {"model": "m3"}
    assert before != (turn_a / HERMES_REQUEST_FILENAME).read_text(encoding="utf-8")


def test_rewrite_replaces_not_appends(turn_a: Path) -> None:
    save_reply(turn_a, "первый ответ")
    save_reply(turn_a, "второй")
    assert (turn_a / REPLY_FILENAME).read_text(encoding="utf-8") == "второй"


# --- cross-turn isolation --------------------------------------------------------


def test_parallel_turns_do_not_share_artifacts(tmp_path: Path) -> None:
    a = tmp_path / "2026" / "09" / "13" / TURN_A
    b = tmp_path / "2026" / "09" / "13" / TURN_B

    save_hermes_request(a, {"model": "m-a"})
    save_hermes_response(a, '{"reply": "ответ A"}')
    save_reply(a, "ответ A")
    save_hermes_failure_metadata(
        a,
        build_hermes_failure_metadata(
            turn_id=TURN_A, status="hermes_failed", error_type="HermesClientError"
        ),
    )

    save_hermes_request(b, {"model": "m-b"})
    save_hermes_response(b, 'извините, я не понял')
    save_reply(b, "ответ Б")
    save_hermes_failure_metadata(
        b,
        build_hermes_failure_metadata(
            turn_id=TURN_B, status="hermes_invalid_response", error_type="HermesValidationError"
        ),
    )

    # Each turn dir holds exactly its own data; no overlap between turns.
    assert _read_json(a / HERMES_REQUEST_FILENAME) == {"model": "m-a"}
    assert _read_json(b / HERMES_REQUEST_FILENAME) == {"model": "m-b"}
    assert _read_json(a / HERMES_RESPONSE_FILENAME)["reply"] == "ответ A"
    assert _read_json(b / HERMES_RESPONSE_FILENAME) == {"raw_response": "извините, я не понял"}
    assert (a / REPLY_FILENAME).read_text(encoding="utf-8") == "ответ A"
    assert (b / REPLY_FILENAME).read_text(encoding="utf-8") == "ответ Б"
    assert _read_json(a / "metadata.json")["turn_id"] == TURN_A
    assert _read_json(b / "metadata.json")["turn_id"] == TURN_B

    # No file in one turn dir is a hardlink/same inode as a file in the other.
    for name in (
        HERMES_REQUEST_FILENAME,
        HERMES_RESPONSE_FILENAME,
        REPLY_FILENAME,
        "metadata.json",
    ):
        assert (a / name).stat().st_ino != (b / name).stat().st_ino
