"""Tests for structured Hermes response validation (ТЗ sections 22, 24)."""
import json

import pytest

from backend.src.voice_gateway.hermes.validation import (
    HermesValidationError,
    parse_hermes_response,
)


def test_valid_minimal_reply_only():
    raw = json.dumps({"reply": "Привет!"})
    result = parse_hermes_response(raw)
    assert result.reply == "Привет!"
    assert result.note.create is False
    assert result.note.title == ""
    assert result.note.content == ""
    assert result.note.tags == []


def test_valid_full_note():
    raw = json.dumps(
        {
            "reply": "Записал.",
            "note": {
                "create": True,
                "title": "Покупки",
                "content": "- USB-C кабель\n- батарея",
                "tags": ["voice", "idea"],
            },
        }
    )
    result = parse_hermes_response(raw)
    assert result.note.create is True
    assert result.note.title == "Покупки"
    assert result.note.tags == ["voice", "idea"]


def test_missing_reply_is_invalid():
    with pytest.raises(HermesValidationError) as exc_info:
        parse_hermes_response(json.dumps({"note": {"create": False}}))
    # raw payload is preserved for archiving
    assert exc_info.value.raw.startswith('{"note"')


def test_plain_text_response_is_invalid():
    with pytest.raises(HermesValidationError):
        parse_hermes_response("Просто текстовый ответ без JSON.")


def test_empty_string_is_invalid():
    with pytest.raises(HermesValidationError):
        parse_hermes_response("")


def test_json_fence_is_repaired():
    raw = '```json\n{"reply": "OK"}\n```'
    assert parse_hermes_response(raw).reply == "OK"


def test_json_block_markers_are_repaired():
    raw = 'Понял. <json>\n{"reply": "Готово."}\n</json>'
    assert parse_hermes_response(raw).reply == "Готово."


def test_trailing_comma_is_repaired():
    raw = '{"reply": "OK", "note": {"create": false, "tags": [],},}'
    assert parse_hermes_response(raw).reply == "OK"


def test_unrepairable_garbage_keeps_original_raw():
    garbage = '```json\n{"reply": "OK"\n```'
    with pytest.raises(HermesValidationError) as exc_info:
        parse_hermes_response(garbage)
    assert exc_info.value.raw == garbage


def test_invalid_schema_is_invalid_even_with_valid_json():
    raw = json.dumps({"answer": "reply is wrong field"})
    with pytest.raises(HermesValidationError):
        parse_hermes_response(raw)


def test_cyrillic_roundtrip():
    raw = json.dumps(
        {"reply": "Записал: купить USB-C кабель для лаборатории."},
        ensure_ascii=False,
    )
    assert parse_hermes_response(raw).reply == (
        "Записал: купить USB-C кабель для лаборатории."
    )
