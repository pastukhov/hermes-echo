"""NoteStore abstraction and its MVP filesystem/Obsidian realization.

ТЗ section 25: Hermes must never write arbitrary files itself (ТЗ section
51.8); the only sanctioned way to persist a voice-turn note is through the
``NoteStore`` interface below. ``FilesystemObsidianNoteStore`` is the MVP
implementation that writes plain ``.md`` files into an Obsidian vault
(``OBSIDIAN_VAULT_PATH`` / ``OBSIDIAN_INBOX``, ТЗ sections 25 and 36).

Note format (ТЗ section 26): YAML frontmatter (``created``, ``source``,
``turn_id``, ``tags``) + an H1 title + the Hermes-authored content. The
filename is ``YYYY-MM-DD HH-MM-SS - <sanitized-title>.md``. The write is
atomic: temporary file -> fsync -> rename, so a reader never observes a
partially written note.
"""
from __future__ import annotations

import datetime
import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

import yaml

__all__ = [
    "NoteSpec",
    "NoteStore",
    "NoteStoreError",
    "NoteStoreConfigError",
    "NoteStoreConfig",
    "FilesystemObsidianNoteStore",
    "sanitize_title",
    "create_filesystem_note_store",
]


class NoteStoreError(Exception):
    """A NoteStore implementation failed to persist/read a note.

    Vendor/implementation-neutral: callers (the note pipeline stage) catch
    exactly this type regardless of which concrete NoteStore is wired in,
    and classify the turn as ``note_write_failed`` without needing to know
    filesystem/Obsidian specifics.
    """


class NoteStoreConfigError(ValueError):
    """Invalid NoteStore configuration (missing/malformed env values)."""


@dataclass(frozen=True)
class NoteSpec:
    """Everything a caller needs to describe one note (ТЗ section 26).

    Deliberately plain data — no filesystem/Obsidian concept leaks into
    this type, so callers (the voice-turn pipeline) can depend on
    ``NoteStore.save_note`` without importing any concrete NoteStore
    implementation.
    """

    title: str
    content: str
    created: Optional[datetime.datetime] = None
    source: Optional[str] = None
    tags: List[str] = field(default_factory=list)


class NoteStore(ABC):
    """Vendor-neutral contract for persisting one Hermes-authored note.

    Hermes/the pipeline must never write ``.md`` (or any other) files
    directly (ТЗ section 51.8) — this is the single sanctioned entry point
    (ТЗ section 51.9).
    """

    @abstractmethod
    def save_note(self, note: NoteSpec, turn_id: str) -> str:
        """Persist ``note`` for ``turn_id`` and return its note_path.

        The returned value is an implementation-defined but always usable
        identifier for the persisted note (for the filesystem realization:
        an absolute path); it must not require the caller to know how the
        concrete NoteStore is organized internally.
        """

    @abstractmethod
    def list_notes(self) -> List[str]:
        """Return the note_path of every note currently persisted."""

    @abstractmethod
    def get_note(self, note_path: str) -> Dict:
        """Read back a previously saved note by its note_path."""


#: Characters forbidden (or reserved) in filenames across POSIX/Windows
#: filesystems, plus the path separators themselves (ТЗ section 26 filename
#: must land as a single path component, never traverse directories).
_FS_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')
#: ASCII control characters (including NUL) are never valid in a filename.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
#: Collapse any run of whitespace (after illegal chars are stripped) to a
#: single space, so e.g. "My   Title" and "My\nTitle" both sanitize sanely.
_WHITESPACE_RUN = re.compile(r"\s+")


def sanitize_title(title: str) -> str:
    """Sanitize an arbitrary title for safe use as (part of) a filename.

    Strips path separators and FS-reserved characters (``/\\:*?"<>|``),
    control characters, collapses whitespace, and trims leading/trailing
    dots/spaces (both invalid trailing characters on Windows and a path-
    traversal vector when repeated, e.g. ``".."``). Never returns an empty
    string — an all-illegal title falls back to ``"untitled"``.
    """
    sanitized = _CONTROL_CHARS.sub("", title)
    sanitized = _FS_ILLEGAL_CHARS.sub("", sanitized)
    sanitized = _WHITESPACE_RUN.sub(" ", sanitized).strip()
    # Trailing dots/spaces are invalid on Windows and a leading run of dots
    # is a path-traversal artifact ("..", "...") even though separators are
    # already gone; strip both ends defensively.
    sanitized = sanitized.strip(". ")
    return sanitized or "untitled"


class FilesystemObsidianNoteStore(NoteStore):
    """MVP ``NoteStore``: plain ``.md`` files in an Obsidian vault/inbox."""

    def __init__(self, vault_path: str, inbox_path: Optional[str] = None):
        self.vault_path = vault_path
        self.inbox_path = inbox_path or vault_path
        os.makedirs(self.inbox_path, exist_ok=True)

    # -- writing ----------------------------------------------------------

    def save_note(self, note: NoteSpec, turn_id: str) -> str:
        created_time = note.created or datetime.datetime.now()
        created_str = created_time.strftime("%Y-%m-%d %H-%M-%S")
        sanitized_title = sanitize_title(note.title)
        base_filename = f"{created_str} - {sanitized_title}"

        note_meta = {
            "created": created_str,
            "source": note.source or "",
            "turn_id": turn_id or "",
            "tags": list(note.tags or []),
        }
        yaml_front = yaml.dump(note_meta, allow_unicode=True, sort_keys=False).strip()
        body = f"---\n{yaml_front}\n---\n# {note.title}\n{note.content}\n"

        try:
            return self._atomic_write(base_filename, body)
        except NoteStoreError:
            raise
        except OSError as exc:
            raise NoteStoreError(f"failed to write note: {exc}") from exc

    def _atomic_write(self, base_filename: str, body: str) -> str:
        """Write ``body`` atomically, picking a filename that never
        overwrites an existing note (ТЗ section 26 + duplicate-title
        handling).

        temp file -> fsync -> hard-link into place under a free filename.
        ``os.link`` fails with ``FileExistsError`` if the target already
        exists, so the "does this filename already exist" check and the
        act of claiming it are atomic — no TOCTOU race with a concurrent
        writer, unlike an ``os.path.exists`` check followed by
        ``os.replace`` (which would silently clobber).
        """
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.inbox_path, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())

            max_attempts = 1000
            for attempt in range(max_attempts):
                suffix = "" if attempt == 0 else f" ({attempt + 1})"
                filename = f"{base_filename}{suffix}.md"
                target = os.path.join(self.inbox_path, filename)
                try:
                    os.link(tmp_path, target)
                    return target
                except FileExistsError:
                    continue
            raise NoteStoreError(
                f"could not find a free filename for '{base_filename}' "
                f"after {max_attempts} attempts"
            )
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    # -- reading ------------------------------------------------------------

    def list_notes(self) -> List[str]:
        return sorted(
            os.path.join(self.inbox_path, f)
            for f in os.listdir(self.inbox_path)
            if f.endswith(".md")
        )

    def get_note(self, note_path: str) -> Dict:
        full_path = (
            note_path if os.path.isabs(note_path)
            else os.path.join(self.inbox_path, note_path)
        )
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as exc:
            raise NoteStoreError(f"failed to read note {note_path}: {exc}") from exc
        if not lines or not lines[0].startswith("---"):
            raise NoteStoreError(f"note {note_path} is missing YAML frontmatter")
        yaml_lines: List[str] = []
        i = 1
        while i < len(lines) and not lines[i].startswith("---"):
            yaml_lines.append(lines[i])
            i += 1
        meta = yaml.safe_load("".join(yaml_lines)) or {}
        title_line = (
            lines[i + 1].strip()
            if (i + 1) < len(lines) and lines[i + 1].startswith("#")
            else ""
        )
        content = "".join(lines[i + 2:]).strip() if (i + 2) < len(lines) else ""
        return {"meta": meta, "title": title_line.lstrip("#").strip(), "content": content}


@dataclass(frozen=True)
class NoteStoreConfig:
    """Immutable Obsidian NoteStore settings (ТЗ sections 25, 36).

    ``inbox`` is the subfolder NAME under the vault (``OBSIDIAN_INBOX``,
    e.g. ``"Voice Inbox"``), matching the vendored ``.env.example``
    convention — not an independent filesystem path.
    """

    vault_path: str
    inbox: str = ""

    @property
    def inbox_path(self) -> str:
        return os.path.join(self.vault_path, self.inbox) if self.inbox else self.vault_path

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "NoteStoreConfig":
        """Build config from environment variables.

        ``env`` defaults to ``os.environ``; tests pass an explicit mapping.
        """
        source = os.environ if env is None else env
        vault_path = (source.get("OBSIDIAN_VAULT_PATH") or "").strip()
        if not vault_path:
            raise NoteStoreConfigError("OBSIDIAN_VAULT_PATH is required")
        inbox = (source.get("OBSIDIAN_INBOX") or "").strip()
        return cls(vault_path=vault_path, inbox=inbox)


def create_filesystem_note_store(
    env: Optional[Mapping[str, str]] = None,
) -> FilesystemObsidianNoteStore:
    """Build the production ``FilesystemObsidianNoteStore`` from env vars."""
    config = NoteStoreConfig.from_env(env)
    return FilesystemObsidianNoteStore(config.vault_path, config.inbox_path)
