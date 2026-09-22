"""Tests for :mod:`voice_gateway.logging_config` (ТЗ §33).

Covers the three units directly (``JsonFormatter``,
``transcript_logging_enabled``, ``log_stage_event``); the full-turn
integration scenarios (secret hygiene, transcript gating across a real
pipeline run) live in ``test_structured_logging.py``.
"""
from __future__ import annotations

import json
import logging

from backend.src.voice_gateway.logging_config import (
    JsonFormatter,
    log_stage_event,
    transcript_logging_enabled,
)


def _make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="voice_gateway.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="stage_event",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJsonFormatter:
    def test_round_trip_minimal_fields(self):
        record = _make_record()
        formatted = JsonFormatter().format(record)
        payload = json.loads(formatted)
        assert payload["level"] == "INFO"
        assert payload["logger"] == "voice_gateway.test"
        assert payload["message"] == "stage_event"
        assert "timestamp" in payload
        # ISO-8601 UTC: parseable and offset-aware.
        from datetime import datetime
        parsed = datetime.fromisoformat(payload["timestamp"])
        assert parsed.tzinfo is not None

    def test_round_trip_with_extra_fields(self):
        record = _make_record(
            turn_id="t-1",
            device_id="dev-1",
            stage="stt",
            duration_ms=42,
            status="success",
        )
        payload = json.loads(JsonFormatter().format(record))
        assert payload["turn_id"] == "t-1"
        assert payload["device_id"] == "dev-1"
        assert payload["stage"] == "stt"
        assert payload["duration_ms"] == 42
        assert payload["status"] == "success"
        assert "error" not in payload

    def test_error_field_present_when_set(self):
        record = _make_record(stage="stt", status="stt_failed", error="boom")
        payload = json.loads(JsonFormatter().format(record))
        assert payload["error"] == "boom"

    def test_none_extra_fields_omitted_not_null(self):
        record = _make_record(turn_id="t-1", device_id=None, stage="notes",
                              duration_ms=1, status="success", error=None)
        formatted = JsonFormatter().format(record)
        payload = json.loads(formatted)
        assert "device_id" not in payload
        assert "error" not in payload
        # Never literally emit "null" for an omitted field.
        assert "null" not in formatted

    def test_output_is_one_json_object_per_line(self):
        record = _make_record(stage="archive", status="success",
                              duration_ms=5, turn_id="t", device_id="d")
        formatted = JsonFormatter().format(record)
        assert "\n" not in formatted
        assert json.loads(formatted)  # valid JSON


class TestTranscriptLoggingEnabled:
    def test_default_unset_is_false(self, monkeypatch):
        monkeypatch.delenv("LOG_TRANSCRIPT", raising=False)
        assert transcript_logging_enabled() is False

    def test_true_variants_are_truthy(self, monkeypatch):
        for value in ("true", "True", "TRUE", "1"):
            monkeypatch.setenv("LOG_TRANSCRIPT", value)
            assert transcript_logging_enabled() is True

    def test_false_variants_are_falsy(self, monkeypatch):
        for value in ("false", "False", "0", "no", "", "garbage"):
            monkeypatch.setenv("LOG_TRANSCRIPT", value)
            assert transcript_logging_enabled() is False

    def test_reads_env_fresh_each_call_no_caching(self, monkeypatch):
        monkeypatch.setenv("LOG_TRANSCRIPT", "false")
        assert transcript_logging_enabled() is False
        monkeypatch.setenv("LOG_TRANSCRIPT", "true")
        assert transcript_logging_enabled() is True
        monkeypatch.setenv("LOG_TRANSCRIPT", "false")
        assert transcript_logging_enabled() is False


class TestLogStageEvent:
    def test_emits_expected_extra_keys_on_success(self, caplog):
        logger = logging.getLogger("voice_gateway.test.stage")
        with caplog.at_level(logging.INFO, logger=logger.name):
            log_stage_event(
                logger, "archive", turn_id="t-1", device_id="dev-1",
                duration_ms=10, status="success",
            )
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.turn_id == "t-1"
        assert record.device_id == "dev-1"
        assert record.stage == "archive"
        assert record.duration_ms == 10
        assert record.status == "success"
        assert not hasattr(record, "error") or record.error is None or getattr(record, "error", None) is None

    def test_emits_error_field_on_failure(self, caplog):
        logger = logging.getLogger("voice_gateway.test.stage2")
        with caplog.at_level(logging.INFO, logger=logger.name):
            log_stage_event(
                logger, "stt", turn_id="t-2", device_id="dev-2",
                duration_ms=5, status="stt_failed", error="timeout",
            )
        record = caplog.records[0]
        assert record.status == "stt_failed"
        assert record.error == "timeout"

    def test_respects_explicit_level(self, caplog):
        logger = logging.getLogger("voice_gateway.test.stage3")
        with caplog.at_level(logging.DEBUG, logger=logger.name):
            log_stage_event(
                logger, "tts", turn_id="t-3", device_id=None,
                duration_ms=1, status="success", level=logging.DEBUG,
            )
        assert caplog.records[0].levelno == logging.DEBUG

    def test_device_id_none_is_allowed(self, caplog):
        """notes/tts call sites may legitimately have no device_id yet."""
        logger = logging.getLogger("voice_gateway.test.stage4")
        with caplog.at_level(logging.INFO, logger=logger.name):
            log_stage_event(
                logger, "notes", turn_id="t-4", device_id=None,
                duration_ms=1, status="success",
            )
        record = caplog.records[0]
        assert record.device_id is None
