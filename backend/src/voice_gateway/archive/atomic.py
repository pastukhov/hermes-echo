"""Centralized atomic write of a voice turn's ``metadata.json``.

This module is the SINGLE place in the gateway that serializes and writes a
turn's ``metadata.json`` (ТЗ section 19). The ArchiveStore/pipeline (built by
the sibling cards) calls :func:`atomic_write_metadata` instead of writing the
file itself, so the durability guarantee lives in exactly one spot.

Durability / full-or-no guarantee
---------------------------------
``metadata.json`` is either **fully present with complete, valid JSON** or it
is **absent / left as its previous complete version** — never a half-written
file. This is achieved the classic POSIX way:

1. Open a brand-new temporary file **in the same directory** as the target
   (same filesystem, so the rename is atomic).
2. Write the full payload, ``flush()`` and ``fsync`` the file, so the bytes
   are on stable storage before the name is revealed.
3. ``os.replace`` the temp file onto the target — an atomic rename on POSIX;
   readers see either the old file or the new complete file, never a mix.
4. On any failure before the rename, unlink the temp file and leave the target
   untouched (the previous complete file, or nothing if it never existed).
5. Best-effort ``fsync`` of the directory so the rename itself is durable.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

#: File name that holds a turn's metadata, fixed by ТЗ section 19.
METADATA_FILENAME = "metadata.json"


def _fsync_directory(directory: Path) -> None:
    """``fsync`` a directory so a just-performed rename is durable.

    Best-effort: some filesystems do not support opening a directory for
    ``fsync``, so a failure here is swallowed — the data file is already
    durable, and this only affects whether the *rename* survives a crash.
    """
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def atomic_write_bytes(target: Path, data: bytes) -> Path:
    """Atomically write raw ``bytes`` to ``target`` (temp + fsync + rename).

    The parent directory is created if missing. On success the target contains
    exactly ``data``; on any failure the target is left unchanged.
    """
    target = Path(target)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    # Temp file in the SAME directory as the target so os.replace() is an
    # atomic rename on the same filesystem.
    fd, tmp_path = tempfile.mkstemp(
        dir=str(parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        # Never leak a half-written temp file, and never clobber the target.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _fsync_directory(parent)
    return target


def atomic_write_json(target: Path, payload: object) -> Path:
    """Atomically write ``payload`` as UTF-8 JSON to ``target``.

    ``ensure_ascii=False`` keeps Cyrillic transcripts/replies readable; the
    output is stable for a given payload (insertion order preserved, 2-space
    indent, trailing newline).
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return atomic_write_bytes(target, text.encode("utf-8"))


def atomic_write_metadata(metadata: dict, archive_path: Path) -> Path:
    """The one centralized atomic write of a turn's ``metadata.json``.

    Parameters
    ----------
    metadata:
        The turn's metadata as a plain ``dict`` (the exact payload that should
        end up in ``metadata.json``; see ТЗ section 19).
    archive_path:
        Path to the turn's archive directory — the directory that will contain
        ``metadata.json`` (e.g. ``archive/<device_id>/<turn-id>``).

    Returns the path of the written ``metadata.json``.

    Guarantees the file is either fully written with complete JSON, or absent
    / left as its previous complete version — never partially written.
    """
    target = Path(archive_path) / METADATA_FILENAME
    return atomic_write_json(target, metadata)
