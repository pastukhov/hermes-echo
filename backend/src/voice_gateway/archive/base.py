"""Vendor-neutral ArchiveStore contract (ТЗ sections 16, 17, 18).

The gateway keeps every voice turn under a date-partitioned per-turn
directory (ТЗ section 18::

    archive/YYYY/MM/DD/<turn-id>/
        ├── input.pcm   (temporary raw stream, deleted after finalization)
        ├── input.wav   (finalized WAV, ТЗ §17.3)
        └── ...

). This module defines the ABSTRACTION only: the contract a voice turn's
audio archive must satisfy. The concrete filesystem implementation
(:class:`~backend.src.voice_gateway.archive.filesystem.
FilesystemArchiveStore`, card t_eb697c7e) lives in a sibling module; the
pipeline (``app.py``) talks to the archive exclusively through this
contract, so the backend is never bound to a specific storage backend
(ТЗ section 16: "Backend не должен быть привязан к конкретному ...
implementation").

Contract summary (the lifecycle a turn's audio goes through)
------------------------------------------------------------
1. **Construction** — a store is created for ONE turn: its ``turn_id``,
   the PCM ``AudioFormat`` (sample rate / bits per sample / channels) and
   the day-partition date. All parameters are validated up front; a bad
   turn id or format raises before any I/O.
2. **open()** — prepare the storage for streaming. Must create the turn
   directory (ТЗ §18) and open the raw PCM write stream. The store is a
   context manager: ``with store:`` calls ``open()`` on entry and
   ``close()`` on exit.
3. **write(chunk)** — append one chunk of raw PCM as the stream arrives
   (ТЗ §17.2: the data goes to disk WHILE being received, never buffered
   in RAM). Returns the number of bytes written.
4. **finalize()** — called at EOF (ТЗ §17.3): flush and close the raw
   stream, convert the raw PCM into a correct WAV file
   (``input.wav`` with a valid 44-byte header for the configured
   format), delete the raw PCM file, and return the WAV path.
5. **close()** — release resources. After a *failed* turn (disconnect /
   error before EOF) the pipeline calls ``close()`` WITHOUT
   ``finalize()``; the raw PCM file is kept as-is for forensics (ТЗ §32)
   and never deleted on this path.

State machine (enforced here, in the ABC — every implementation inherits
it, so it is testable with a fake implementation and no filesystem)
--------------------------------------------------------------------
::

    new ──open()──▶ open ──write()*──▶ open ──finalize()──▶ finalized ──close()──▶ closed
                              │
                              └──close()──▶ closed   (raw PCM kept)

* ``open()`` twice, ``write()`` before ``open()`` or after ``close()`` /
  ``finalize()``, ``finalize()`` before ``open()`` or twice — all raise
  :class:`ArchiveError`. ``close()`` is always safe (idempotent).
* ``finalize()`` with zero bytes written still produces a VALID empty WAV
  (44-byte header, ``data`` size 0). Whether an empty body is an error is
  the PIPELINE's policy (ТЗ §13: ``audio_invalid``), not the store's —
  the store only guarantees a well-formed file for whatever it was given.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

#: File name of the temporary raw PCM stream inside the turn directory
#: (ТЗ §17.2). Deleted by ``finalize()`` after a successful WAV
#: conversion; kept on the error path (ТЗ §32).
RAW_PCM_FILENAME = "input.pcm"

#: File name of the finalized input WAV inside the turn directory
#: (ТЗ §17.3, §18).
INPUT_WAV_FILENAME = "input.wav"


class ArchiveError(Exception):
    """Any archive failure: bad lifecycle use or a storage I/O error.

    Implementations MUST wrap low-level I/O failures (``OSError`` and
    friends) in this type so the pipeline can catch a single exception
    regardless of backend. The original exception is preserved as
    ``__cause__``.
    """


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """PCM parameters of a turn's raw audio (ТЗ §11: S16LE by default).

    Defaults match the ATOM's microphone contract (16 kHz, 16-bit,
    mono) — the same values ``app.py`` already uses as defaults.
    """

    sample_rate: int = 16000
    bits_per_sample: int = 16
    channels: int = 1

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate must be > 0, got {self.sample_rate}")
        if self.bits_per_sample not in (8, 16, 24, 32):
            raise ValueError(
                f"bits_per_sample must be one of 8/16/24/32, got {self.bits_per_sample}"
            )
        if self.channels <= 0:
            raise ValueError(f"channels must be > 0, got {self.channels}")

    @property
    def bytes_per_sample(self) -> int:
        return self.bits_per_sample // 8

    @property
    def block_align(self) -> int:
        """WAV ``block align``: bytes per frame across all channels."""
        return self.bytes_per_sample * self.channels

    @property
    def byte_rate(self) -> int:
        """WAV ``byte rate``: bytes per second across all channels."""
        return self.sample_rate * self.block_align


class ArchiveStore(ABC):
    """Contract for one voice turn's audio archive (ТЗ §16, §17).

    Lifecycle: construct → :meth:`open` → :meth:`write` (per stream
    chunk) → :meth:`finalize` (at EOF) → :meth:`close`. The state-machine
    guards in this class are concrete and final: implementations supply
    only the four underscored hooks and inherit the full contract
    semantics (validated construction, lifecycle enforcement, error
    wrapping).
    """

    # ------------------------------------------------------------------
    # Construction — concrete: parameter validation lives in the ABC so
    # every backend validates identically.
    # ------------------------------------------------------------------

    def __init__(
        self,
        turn_id: str | UUID,
        *,
        format: AudioFormat | None = None,
        day: date | None = None,
    ) -> None:
        try:
            self._turn_id = UUID(str(turn_id))
        except ValueError as exc:
            raise ValueError(f"turn_id must be a UUID, got {turn_id!r}") from exc
        self._format = format if format is not None else AudioFormat()
        self._day = day
        self._opened = False
        self._finalized = False
        self._closed = False
        self._bytes_written = 0

    # ------------------------------------------------------------------
    # Public properties (concrete).
    # ------------------------------------------------------------------

    @property
    def turn_id(self) -> UUID:
        """The turn this store archives (validated UUID)."""
        return self._turn_id

    @property
    def format(self) -> AudioFormat:
        """PCM format the raw stream is written in and WAV is finalized to."""
        return self._format

    @property
    def day(self) -> date | None:
        """Day partition (``YYYY/MM/DD``) the turn belongs to.

        ``None`` means "the implementation's current date" — pipelines
        that must keep the whole turn in one partition should capture the
        UTC date once and pass it explicitly.
        """
        return self._day

    @property
    def bytes_written(self) -> int:
        """Total raw PCM bytes accepted by :meth:`write` so far."""
        return self._bytes_written

    # ------------------------------------------------------------------
    # Lifecycle (concrete state machine + error wrapping).
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Prepare the store for streaming (creates the turn directory)."""
        if self._closed:
            raise ArchiveError(f"store for turn {self._turn_id} is closed")
        if self._opened:
            raise ArchiveError(f"store for turn {self._turn_id} is already open")
        try:
            self._open()
        except ArchiveError:
            raise
        except OSError as exc:
            raise ArchiveError(
                f"cannot open archive for turn {self._turn_id}: {exc}"
            ) from exc
        self._opened = True

    def write(self, chunk: bytes | bytearray | memoryview) -> int:
        """Append one raw PCM chunk received from the stream (ТЗ §17.2).

        Returns the number of bytes written. Raises :class:`ArchiveError`
        if the store is not open, is closed, or is already finalized.
        """
        if self._closed:
            raise ArchiveError(f"store for turn {self._turn_id} is closed")
        if self._finalized:
            raise ArchiveError(
                f"store for turn {self._turn_id} is finalized; write is no longer allowed"
            )
        if not self._opened:
            raise ArchiveError(f"store for turn {self._turn_id} is not open")
        data = bytes(chunk)
        try:
            written = self._write(data)
        except ArchiveError:
            raise
        except OSError as exc:
            raise ArchiveError(
                f"cannot write PCM for turn {self._turn_id}: {exc}"
            ) from exc
        if written < 0:
            raise ArchiveError(f"negative write size for turn {self._turn_id}")
        self._bytes_written += written
        return written

    def finalize(self) -> Path:
        """Convert the accumulated raw PCM to ``input.wav`` (ТЗ §17.3).

        Flushes and closes the raw stream, writes the WAV atomically,
        deletes the raw PCM file and returns the WAV path. Raises
        :class:`ArchiveError` if called before :meth:`open` or twice.
        """
        if self._closed:
            raise ArchiveError(f"store for turn {self._turn_id} is closed")
        if self._finalized:
            raise ArchiveError(
                f"store for turn {self._turn_id} is already finalized"
            )
        if not self._opened:
            raise ArchiveError(
                f"store for turn {self._turn_id} was never opened"
            )
        try:
            wav_path = self._finalize()
        except ArchiveError:
            raise
        except OSError as exc:
            raise ArchiveError(
                f"cannot finalize WAV for turn {self._turn_id}: {exc}"
            ) from exc
        self._finalized = True
        self._opened = False
        return Path(wav_path)

    def close(self) -> None:
        """Release resources; safe to call any time, multiple times.

        On the error path (turn failed before EOF) this is called WITHOUT
        :meth:`finalize` — implementations must leave the raw PCM file in
        place (forensics, ТЗ §32).
        """
        if self._closed:
            return
        try:
            self._close()
        except ArchiveError:
            raise
        except OSError as exc:
            raise ArchiveError(
                f"cannot close archive for turn {self._turn_id}: {exc}"
            ) from exc
        self._closed = True

    # Context manager: ``with store:`` = open on entry, close on exit.
    def __enter__(self) -> "ArchiveStore":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Abstract surface — every implementation must provide these.
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def turn_dir(self) -> Path:
        """Directory holding this turn's files (ТЗ §18 layout)."""

    @property
    @abstractmethod
    def raw_pcm_path(self) -> Path:
        """Path of the temporary raw PCM file (``turn_dir/input.pcm``)."""

    @property
    @abstractmethod
    def input_wav_path(self) -> Path:
        """Path of the finalized WAV (``turn_dir/input.wav``)."""

    @abstractmethod
    def _open(self) -> None:
        """Create the turn directory and open the raw PCM write stream.

        Called once, inside :meth:`open`. Must be idempotent-safe with
        respect to its own partial progress is NOT required — a failed
        open leaves the store unusable and :class:`ArchiveError`
        propagates.
        """

    @abstractmethod
    def _write(self, chunk: bytes) -> int:
        """Append ``chunk`` to the raw PCM stream; return bytes written."""

    @abstractmethod
    def _finalize(self) -> Path:
        """Flush+close the raw stream, write the WAV, delete the raw file.

        Must return the WAV path. The WAV must be a correct 44-byte-header
        file for :attr:`format` (ТЗ §17.3). A zero-byte stream must
        produce a valid empty WAV (see module docstring).
        """

    @abstractmethod
    def _close(self) -> None:
        """Flush and close the raw PCM stream. Must NOT delete the file."""
