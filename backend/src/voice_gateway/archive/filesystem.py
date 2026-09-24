"""Filesystem implementation of the ArchiveStore contract (card t_eb697c7e).

Concrete storage backend for one voice turn's audio, laid out per ТЗ
section 18::

    <root>/YYYY/MM/DD/<turn-id>/
        ├── input.pcm   (raw stream, written incrementally while received)
        └── input.wav   (finalized WAV, 44-byte header, ТЗ §17.3)

The state machine, parameter validation and error wrapping are inherited
from the :class:`~backend.src.voice_gateway.archive.base.ArchiveStore`
ABC — this class only supplies the four storage hooks and the three path
properties.

Lifecycle semantics (inherited):

* ``open()`` creates the turn directory and the raw PCM write stream.
* ``write(chunk)`` appends the chunk as the stream arrives (ТЗ §17.2:
  data hits disk WHILE being received — nothing is buffered in RAM).
* ``finalize()`` wraps the accumulated raw PCM into a valid 44-byte-
  header ``input.wav`` (ТЗ §17.3) and returns the WAV path.
* ``close()`` without ``finalize()`` is the turn-failed path (ТЗ §32):
  it flushes and closes the raw stream but NEVER deletes the file — the
  raw PCM is kept in place as forensics.

Note on the raw file after a *successful* finalize
--------------------------------------------------
After a successful conversion, ``_finalize`` deletes ``input.pcm`` (per
the base module docstring): the raw file is only kept on the turn-failed
path (``close()`` without ``finalize()``, ТЗ §32 forensics). Any error
raised while building ``input.wav`` propagates before the delete runs,
so a failed finalize still leaves ``input.pcm`` on disk.
"""
from __future__ import annotations

import wave
from datetime import date, datetime, timezone
from io import BufferedWriter
from pathlib import Path
from uuid import UUID

from backend.src.voice_gateway.archive.base import (
    INPUT_WAV_FILENAME,
    RAW_PCM_FILENAME,
    ArchiveStore,
    AudioFormat,
)

#: Default archive root (ТЗ §18): ``archive/`` next to the repo contents,
#: the same default ``create_app()`` already resolves.
DEFAULT_ARCHIVE_ROOT = Path("archive")


class FilesystemArchiveStore(ArchiveStore):
    """One turn's audio archive on the local filesystem (ТЗ §16–§18).

    Constructed per turn (one instance == one voice turn), exactly like
    the pipeline uses it::

        store = FilesystemArchiveStore(turn_id, root=archive_root,
                                       format=AudioFormat(...), day=day)
        store.open()
        for chunk in stream:
            store.write(chunk)
        wav = store.finalize()   # EOF (ТЗ §17.3)
        store.close()

    ``root`` is the archive root (``$ARCHIVE_ROOT`` in production, a tmp
    dir in tests). ``day`` pins the date partition: pass the UTC date
    captured once at turn start so every file of the turn lands in the
    same ``YYYY/MM/DD`` partition even if the turn straddles midnight.
    ``None`` (default) resolves to the current UTC date at ``open()``.
    """

    def __init__(
        self,
        turn_id: str | UUID,
        root: str | Path = DEFAULT_ARCHIVE_ROOT,
        *,
        format: AudioFormat | None = None,
        day: date | None = None,
    ) -> None:
        super().__init__(turn_id, format=format, day=day)
        self._root = Path(root)
        self._day = self._day or datetime.now(timezone.utc).date()
        self._pcm_file: IO[bytes] | None = None

    # ------------------------------------------------------------------
    # Path properties (ТЗ §18 layout) — concrete for the turn's files.
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        """Archive root this store writes under."""
        return self._root

    @property
    def turn_dir(self) -> Path:
        """Directory holding this turn's files (ТЗ §18 layout)."""
        return (
            self._root
            / f"{self._day.year:04d}"
            / f"{self._day.month:02d}"
            / f"{self._day.day:02d}"
            / str(self._turn_id)
        )

    @property
    def raw_pcm_path(self) -> Path:
        """Temporary raw PCM file: ``turn_dir/input.pcm`` (ТЗ §17.2)."""
        return self.turn_dir / RAW_PCM_FILENAME

    @property
    def input_wav_path(self) -> Path:
        """Finalized WAV: ``turn_dir/input.wav`` (ТЗ §17.3)."""
        return self.turn_dir / INPUT_WAV_FILENAME

    # ------------------------------------------------------------------
    # Storage hooks (the only surface the ABC leaves to implementations).
    # ------------------------------------------------------------------

    def _open(self) -> None:
        """Create the turn directory and the raw PCM write stream.

        The stream is flushed after every :meth:`_write` (see below) —
        ТЗ §17.2 wants the bytes on disk as they arrive, and a crash
        mid-stream must not lose already-received chunks. Chunks arrive
        in KB-sized HTTP fragments, so per-write flushes are cheap.
        """
        self.turn_dir.mkdir(parents=True, exist_ok=True)
        # "wb+" (read+write, truncate): _finalize() must read the raw PCM
        # back to wrap it into input.wav (ТЗ §17.3) — a write-only stream
        # raises io.UnsupportedOperation on the first read.
        self._pcm_file = open(self.raw_pcm_path, "wb+")

    def _write(self, chunk: bytes) -> int:
        """Append one raw PCM chunk to ``input.pcm`` (ТЗ §17.2).

        Flushes to disk immediately so the bytes are durable before the
        next chunk arrives (incremental, never RAM-buffered).
        """
        pcm = self._pcm_file
        if pcm is None:  # defensive: ABC guards the lifecycle
            raise OSError("input.pcm stream is not open")
        pcm.write(chunk)
        pcm.flush()
        return len(chunk)

    def _finalize(self) -> Path:
            """Flush raw stream wrap ``input.wav`` (ТЗ §17.3).

            The WAV is a correct 44-byte-header file for :attr:`format`
            (RIFF/WAVE + fmt + data, ``wave`` module canonical header
            writer). A zero-byte stream yields a valid empty WAV (header
            only) — whether an empty body is an error is the pipeline's
            policy (ТЗ §13), not the store's.

            The temporary raw PCM file (``input.pcm``) is DELETED after a
            successful conversion — only after ``input.wav`` has been
            fully written does this method remove the raw file. If wave
            writing raises partway through, the exception propagates
            before the delete runs, so ``input.pcm`` survives any failed
            finalize (matching the all-exceptions-are-internal_error
            handling in ``app.py`` and the ТЗ §32 forensics intent).
            """
            wav_path = self.input_wav_path
            pcm = self._pcm_file
            if pcm is None:  # defensive: ABC guards the lifecycle
                raise OSError("input.pcm stream is not open")
            self._pcm_file = None
            with pcm:  # closes the raw stream at the end (ТЗ §17.3 flush+close)
                pcm.flush()
                pcm.seek(0)  # writes left the cursor at EOF; rewind before reading
                with wave.open(str(wav_path), "wb") as wav_file:
                    wav_file.setnchannels(self._format.channels)
                    wav_file.setsampwidth(self._format.bytes_per_sample)
                    wav_file.setframerate(self._format.sample_rate)
                    while True:
                        chunk = pcm.read(1024 * 1024)
                        if not chunk:
                            break
                        wav_file.writeframes(chunk)
            self.raw_pcm_path.unlink()
            return wav_path

    def _close(self) -> None:
        """Flush and close the raw stream. MUST NOT delete the file (ТЗ §32).

        Idempotent in effect: called on the turn-failed path (forensics),
        after ``finalize()`` (already closed), or without ``open()``.
        """
        if self._pcm_file is None:
            return
        pcm = self._pcm_file
        self._pcm_file = None
        try:
            pcm.flush()
        finally:
            pcm.close()

