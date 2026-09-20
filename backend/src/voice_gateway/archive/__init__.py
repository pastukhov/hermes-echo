"""Turn-archive access: the one place that writes ``metadata.json``.

The gateway keeps every voice turn under a date-partitioned per-turn
directory (ТЗ section 18::

    archive/YYYY/MM/DD/<turn-id>/
        ├── input.wav
        ├── transcript.txt
        ├── hermes-request.json
        ├── hermes-response.json
        ├── reply.txt
        ├── reply.wav
        └── metadata.json

). This package owns the ``metadata.json`` half of that layout: the store
computes the turn directory and writes the metadata atomically. Sibling
components (audio staging, note persistence) live elsewhere; the pipeline
talks to the archive through :class:`MetadataArchiveStore` so the
atomic-write guarantee is applied in exactly one place.

The package also re-exports the :class:`ArchiveStore` ABSTRACTION (ТЗ
section 16) — the contract a turn's audio archive must satisfy — and its
supporting types (:class:`AudioFormat`, :class:`ArchiveError`). The
filesystem implementation of that abstraction arrives with card
t_eb697c7e in :mod:`voice_gateway.archive.filesystem`.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID

from backend.src.voice_gateway.archive.atomic import (
    METADATA_FILENAME,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_metadata,
)
from backend.src.voice_gateway.archive.base import (
    ArchiveError,
    ArchiveStore,
    AudioFormat,
    INPUT_WAV_FILENAME,
    RAW_PCM_FILENAME,
)
from backend.src.voice_gateway.archive.filesystem import FilesystemArchiveStore
from backend.src.voice_gateway.archive.metadata import TurnMetadata


class MetadataArchiveStore:
    """Filesystem layout for voice-turn archives.

    ``root`` is the archive root (default ``archive/`` next to the repo
    contents, overridable for tests). A turn's directory is
    ``root/<YYYY>/<MM>/<DD>/<turn_id>`` (ТЗ section 18) and the store's job
    is to write that turn's ``metadata.json`` atomically — nothing else.

    All writes go through the module-level :func:`atomic_write_metadata`,
    so the full-or-no guarantee (temp file + flush/fsync + rename) is
    centralized and cannot be bypassed by a caller writing the file itself.
    """

    def __init__(self, root: str | Path = "archive"):
        self.root = Path(root)

    def turn_dir(self, turn_id, day: date | None = None) -> Path:
        """Directory that holds one turn's files (ТЗ §18 layout)."""
        turn_id = UUID(str(turn_id))
        day = day or date.today()
        return self.root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" / str(turn_id)

    def save_metadata(self, turn_id, metadata: dict, day: date | None = None) -> Path:
        """Atomically (re)write the turn's ``metadata.json``.

        The file is either fully written with complete JSON or left as its
        previous complete version — never partially written. Returns the
        path of the written file.
        """
        archive_path = self.turn_dir(turn_id, day)
        return atomic_write_metadata(metadata, archive_path)


__all__ = [
    "INPUT_WAV_FILENAME",
    "METADATA_FILENAME",
    "RAW_PCM_FILENAME",
    "ArchiveError",
    "ArchiveStore",
    "AudioFormat",
    "FilesystemArchiveStore",
    "MetadataArchiveStore",
    "TurnMetadata",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_metadata",
]
