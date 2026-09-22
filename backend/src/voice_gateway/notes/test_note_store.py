"""Tests for the NoteStore abstraction and FilesystemObsidianNoteStore
(ТЗ sections 25, 26, 36, 51.8-51.9)."""
import datetime
import logging
import os

import pytest

from backend.common.error_codes import ErrorCode
from backend.src.voice_gateway.notes.note_store import (
    FilesystemObsidianNoteStore,
    NoteSpec,
    NoteStore,
    NoteStoreConfig,
    NoteStoreConfigError,
    NoteStoreError,
    create_filesystem_note_store,
    sanitize_title,
)


def _make_store(tmp_path):
    vault_path = tmp_path / "vault"
    inbox_path = vault_path / "Inbox"
    inbox_path.mkdir(parents=True)
    return FilesystemObsidianNoteStore(str(vault_path), str(inbox_path))


# ---------------------------------------------------------------------------
# Interface shape (acceptance criterion: save_note(NoteSpec, turn_id) ->
# note_path, not tied to a concrete FS implementation in calling code).
# ---------------------------------------------------------------------------

def test_notestore_is_abstract_with_expected_methods():
    assert NoteStore.__abstractmethods__ == frozenset(
        {"save_note", "list_notes", "get_note"}
    )


def test_filesystem_store_is_a_notestore(tmp_path):
    store = _make_store(tmp_path)
    assert isinstance(store, NoteStore)


# ---------------------------------------------------------------------------
# Basic save + read round trip, format (ТЗ section 26)
# ---------------------------------------------------------------------------

def test_save_and_read_round_trip(tmp_path):
    store = _make_store(tmp_path)
    created = datetime.datetime(2024, 6, 7, 13, 45, 15)
    note = NoteSpec(
        title="Идея голосового терминала",
        content="Содержимое, подготовленное Hermes.",
        created=created,
        source="atom-echo",
        tags=["voice", "idea"],
    )

    note_path = store.save_note(note, turn_id="42a")

    # note_path is a real, directly usable path — not a bare filename the
    # caller has to rejoin with an internal inbox_path it can't see.
    assert os.path.isabs(note_path)
    assert os.path.isfile(note_path)
    assert note_path.endswith(".md")
    assert note_path in store.list_notes()

    read_back = store.get_note(note_path)
    assert read_back["meta"]["created"] == "2024-06-07 13-45-15"
    assert read_back["meta"]["source"] == "atom-echo"
    assert read_back["meta"]["turn_id"] == "42a"
    assert read_back["meta"]["tags"] == ["voice", "idea"]
    assert read_back["title"] == "Идея голосового терминала"
    assert "Содержимое, подготовленное Hermes." in read_back["content"]

    # Filename shape from ТЗ section 26: "YYYY-MM-DD HH-MM-SS - <title>.md".
    filename = os.path.basename(note_path)
    assert filename.startswith("2024-06-07 13-45-15 - ")


def test_saved_file_has_yaml_frontmatter_then_h1_then_content(tmp_path):
    store = _make_store(tmp_path)
    note = NoteSpec(title="Заметка", content="Тело заметки.", source="atom-echo")
    note_path = store.save_note(note, turn_id="t1")

    text = open(note_path, encoding="utf-8").read()
    assert text.startswith("---\n")
    assert "\n---\n# Заметка\n" in text
    assert text.rstrip().endswith("Тело заметки.")


# ---------------------------------------------------------------------------
# Filename sanitization (ТЗ section 26 + review round 1 finding)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, forbidden",
    [
        ("My/Note", "/"),
        ("My\\Note", "\\"),
        ("Note:Title", ":"),
        ("Note*Title", "*"),
        ('Note"Title', '"'),
        ("Note<Title>", "<"),
        ("Note|Title", "|"),
        ("Note?Title", "?"),
    ],
)
def test_sanitize_title_strips_fs_illegal_chars(raw, forbidden):
    sanitized = sanitize_title(raw)
    assert forbidden not in sanitized


def test_sanitize_title_strips_control_chars_and_collapses_whitespace():
    sanitized = sanitize_title("My\nTitle\twith\x00control")
    assert "\n" not in sanitized
    assert "\t" not in sanitized
    assert "\x00" not in sanitized
    assert "  " not in sanitized


def test_sanitize_title_rejects_path_traversal_component():
    # Separators are stripped entirely, so what remains can never escape
    # the inbox directory as a path component.
    sanitized = sanitize_title("../../etc/passwd")
    assert "/" not in sanitized
    assert ".." not in sanitized.strip(". ")


def test_sanitize_title_empty_or_all_illegal_falls_back():
    assert sanitize_title("") == "untitled"
    assert sanitize_title("///") == "untitled"
    assert sanitize_title("   ") == "untitled"


def test_save_note_with_illegal_title_lands_directly_in_inbox(tmp_path):
    store = _make_store(tmp_path)
    note = NoteSpec(title="../../etc/passwd:*?", content="x")
    note_path = store.save_note(note, turn_id="t1")

    # Directly inside the inbox — no extra path components smuggled in.
    assert os.path.dirname(note_path) == store.inbox_path
    assert os.path.isfile(note_path)


# ---------------------------------------------------------------------------
# Atomic write (ТЗ section 26: temp file -> fsync -> rename)
# ---------------------------------------------------------------------------

def test_no_leftover_tmp_files_after_save(tmp_path):
    store = _make_store(tmp_path)
    store.save_note(NoteSpec(title="Заметка", content="тело"), turn_id="t1")

    leftovers = [f for f in os.listdir(store.inbox_path) if f.endswith(".tmp")]
    assert leftovers == []


def test_write_failure_leaves_no_partial_final_file(tmp_path, monkeypatch):
    store = _make_store(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", _boom)

    with pytest.raises(NoteStoreError):
        store.save_note(NoteSpec(title="Заметка", content="тело"), turn_id="t1")

    # No half-written .md and no leaked .tmp file.
    entries = os.listdir(store.inbox_path)
    assert not any(f.endswith(".md") for f in entries)
    assert not any(f.endswith(".tmp") for f in entries)


# ---------------------------------------------------------------------------
# Duplicate title handling (explicit acceptance criterion)
# ---------------------------------------------------------------------------

def test_duplicate_title_and_timestamp_does_not_overwrite(tmp_path):
    store = _make_store(tmp_path)
    created = datetime.datetime(2024, 6, 7, 13, 45, 15)

    path1 = store.save_note(
        NoteSpec(title="Same Title", content="content one", created=created),
        turn_id="turn1",
    )
    path2 = store.save_note(
        NoteSpec(title="Same Title", content="content two", created=created),
        turn_id="turn2",
    )

    assert path1 != path2
    assert os.path.isfile(path1)
    assert os.path.isfile(path2)

    note1 = store.get_note(path1)
    note2 = store.get_note(path2)
    # First note's content must survive untouched (no clobbering).
    assert "content one" in note1["content"]
    assert "content two" in note2["content"]
    assert note1["meta"]["turn_id"] == "turn1"
    assert note2["meta"]["turn_id"] == "turn2"


def test_three_duplicates_all_survive(tmp_path):
    store = _make_store(tmp_path)
    created = datetime.datetime(2024, 1, 1, 0, 0, 0)
    paths = [
        store.save_note(
            NoteSpec(title="Dup", content=f"body {i}", created=created),
            turn_id=f"t{i}",
        )
        for i in range(3)
    ]
    assert len(set(paths)) == 3
    for i, path in enumerate(paths):
        assert f"body {i}" in store.get_note(path)["content"]


# ---------------------------------------------------------------------------
# NoteStoreConfig / env configuration (ТЗ sections 25, 36)
# ---------------------------------------------------------------------------

def test_config_from_env_requires_vault_path():
    with pytest.raises(NoteStoreConfigError, match="OBSIDIAN_VAULT_PATH"):
        NoteStoreConfig.from_env({})


def test_config_from_env_joins_inbox_under_vault():
    config = NoteStoreConfig.from_env(
        {"OBSIDIAN_VAULT_PATH": "/data/obsidian", "OBSIDIAN_INBOX": "Voice Inbox"}
    )
    assert config.vault_path == "/data/obsidian"
    assert config.inbox_path == os.path.join("/data/obsidian", "Voice Inbox")


def test_config_from_env_defaults_inbox_to_vault_root():
    config = NoteStoreConfig.from_env({"OBSIDIAN_VAULT_PATH": "/data/obsidian"})
    assert config.inbox_path == "/data/obsidian"


def test_create_filesystem_note_store_from_env(tmp_path):
    vault = tmp_path / "vault"
    env = {"OBSIDIAN_VAULT_PATH": str(vault), "OBSIDIAN_INBOX": "Voice Inbox"}
    store = create_filesystem_note_store(env)
    assert isinstance(store, FilesystemObsidianNoteStore)
    assert store.inbox_path == str(vault / "Voice Inbox")
    assert os.path.isdir(store.inbox_path)


# ---------------------------------------------------------------------------
# Structured stage logging (ТЗ §33): log_stage_event fires on success/failure
# ---------------------------------------------------------------------------

def test_save_note_success_emits_notes_stage_event(tmp_path, caplog):
    store = _make_store(tmp_path)
    with caplog.at_level(logging.INFO):
        store.save_note(
            NoteSpec(title="Заметка", content="тело"),
            turn_id="t-1",
            device_id="dev-1",
        )
    records = [r for r in caplog.records if getattr(r, "stage", None) == "notes"]
    assert len(records) == 1
    record = records[0]
    assert record.status == "success"
    assert record.turn_id == "t-1"
    assert record.device_id == "dev-1"
    assert isinstance(record.duration_ms, int)
    assert not hasattr(record, "error") or record.error is None


def test_save_note_failure_emits_notes_stage_event_with_error_code(tmp_path, monkeypatch, caplog):
    store = _make_store(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", _boom)

    with caplog.at_level(logging.INFO):
        with pytest.raises(NoteStoreError):
            store.save_note(
                NoteSpec(title="Заметка", content="тело"),
                turn_id="t-2",
                device_id="dev-2",
            )
    records = [r for r in caplog.records if getattr(r, "stage", None) == "notes"]
    assert len(records) == 1
    record = records[0]
    assert record.status == ErrorCode.NOTE_WRITE_FAILED.value
    assert record.error
    assert record.turn_id == "t-2"
    assert record.device_id == "dev-2"


def test_save_note_missing_device_id_defaults_to_none(tmp_path, caplog):
    """``device_id`` is not available at every call site — must be
    accepted as an optional keyword and simply logged as absent, never
    raise (ТЗ §33)."""
    store = _make_store(tmp_path)
    with caplog.at_level(logging.INFO):
        store.save_note(NoteSpec(title="Заметка", content="тело"), turn_id="t-3")
    records = [r for r in caplog.records if getattr(r, "stage", None) == "notes"]
    assert len(records) == 1
    assert records[0].device_id is None
    assert records[0].turn_id == "t-3"
