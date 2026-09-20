"""Tests for the FilesystemArchiveStore implementation (card t_eb697c7e).

Covers the concrete filesystem surface the ABC leaves to implementations:
the ``archive/YYYY/MM/DD/<turn-id>/`` layout (ТЗ §18), incremental raw PCM
streaming to ``input.pcm`` with per-write flushes (ТЗ §17.2), WAV
finalization to ``input.wav`` (ТЗ §17.3) and the raw-PCM-kept error path
(ТЗ §32). The state machine itself is tested in :mod:`test_base` against
a fake implementation; here it is exercised through the real file-backed
store to prove the hooks and the inherited guards compose.
"""
import unittest
import wave
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from backend.src.voice_gateway.archive import (
    ArchiveError,
    AudioFormat,
    FilesystemArchiveStore,
    INPUT_WAV_FILENAME,
    RAW_PCM_FILENAME,
)


def _store(root: Path, turn_id=None, *, format=None, day=None) -> FilesystemArchiveStore:
    return FilesystemArchiveStore(
        turn_id if turn_id is not None else uuid4(),
        root,
        format=format,
        day=day,
    )


class TestLayout(unittest.TestCase):
    """ТЗ §18: ``root/YYYY/MM/DD/<turn-id>/`` with the three path props."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_turn_dir_is_date_partitioned(self):
        turn_id = uuid4()
        store = _store(self.root, turn_id, day=date(2026, 9, 17))
        expected = self.root / "2026" / "09" / "17" / str(turn_id)
        self.assertEqual(store.turn_dir, expected)
        self.assertEqual(store.turn_dir.parent.name, "17")
        self.assertEqual(store.turn_dir.parent.parent.name, "09")
        self.assertEqual(store.turn_dir.parent.parent.parent.name, "2026")

    def test_raw_pcm_and_wav_paths(self):
        turn_id = uuid4()
        store = _store(self.root, turn_id, day=date(2026, 1, 2))
        self.assertEqual(store.raw_pcm_path, store.turn_dir / RAW_PCM_FILENAME)
        self.assertEqual(store.raw_pcm_path.name, "input.pcm")
        self.assertEqual(store.input_wav_path, store.turn_dir / INPUT_WAV_FILENAME)
        self.assertEqual(store.input_wav_path.name, "input.wav")

    def test_paths_do_not_require_open(self):
        # The pipeline needs the paths (for metadata/forensics) even when
        # the store was never opened.
        store = _store(self.root, day=date(2026, 1, 2))
        self.assertFalse(store.turn_dir.exists())
        self.assertIsInstance(store.turn_dir, Path)

    def test_explicit_day_pinned_in_paths(self):
        turn_id = uuid4()
        store = _store(self.root, turn_id, day=date(1999, 12, 31))
        parts = store.turn_dir.parts
        self.assertEqual(parts[-4:-1], ("1999", "12", "31"))
        self.assertEqual(parts[-1], str(turn_id))

    def test_default_day_is_current_utc_date(self):
        from datetime import datetime, timezone

        store = _store(self.root)
        self.assertEqual(store.day, datetime.now(timezone.utc).date())
        self.assertEqual(store.turn_dir.parts[-4], f"{store.day.year:04d}")


class TestStream(unittest.TestCase):
    """ТЗ §17.2: raw PCM hits disk incrementally, flushed as it arrives."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _opened(self):
        self.store = _store(self.root, day=date(2026, 9, 17))
        self.store.open()
        return self.store

    def test_open_creates_turn_directory(self):
        self._opened()
        self.assertTrue(self.store.turn_dir.is_dir())
        self.assertTrue(self.store.raw_pcm_path.is_file())

    def test_write_appends_and_flushes_to_disk(self):
        store = self._opened()
        first, second = b"\x00\x01" * 10, b"\x02\x03" * 10
        store.write(first)
        # No close(): the bytes must already be durable on disk.
        self.assertEqual(store.raw_pcm_path.read_bytes(), first)
        store.write(second)
        self.assertEqual(store.raw_pcm_path.read_bytes(), first + second)
        self.assertEqual(store.bytes_written, len(first) + len(second))

    def test_write_returns_chunk_length(self):
        store = self._opened()
        self.assertEqual(store.write(b"\x00" * 640), 640)

    def test_write_accepts_bytearray_and_memoryview(self):
        store = self._opened()
        store.write(bytearray(b"abc"))
        store.write(memoryview(b"de"))
        self.assertEqual(store.raw_pcm_path.read_bytes(), b"abcde")

    def test_context_manager_opens_and_closes(self):
        with _store(self.root, day=date(2026, 9, 17)) as store:
            store.write(b"\x01\x02" * 5)
        # After exit the raw file is flushed and kept (ТЗ §32 semantics).
        self.assertTrue(store.raw_pcm_path.is_file())
        self.assertEqual(store.raw_pcm_path.read_bytes(), b"\x01\x02" * 5)
        store.close()  # idempotent

    def test_close_without_finalize_keeps_raw_pcm(self):
        """Turn-failed path (ТЗ §32): raw PCM stays for forensics."""
        store = self._opened()
        store.write(b"\xff" * 100)
        store.close()
        self.assertTrue(store.raw_pcm_path.exists())
        self.assertEqual(store.raw_pcm_path.read_bytes(), b"\xff" * 100)
        self.assertFalse(store.input_wav_path.exists())

    def test_close_without_open_is_safe(self):
        store = _store(self.root, day=date(2026, 9, 17))
        store.close()
        store.close()  # idempotent


class TestFinalize(unittest.TestCase):
    """ТЗ §17.3: raw PCM wrapped into a valid 44-byte-header WAV."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.pcm = bytes(range(256)) * 32  # 8192 B, even, non-zero

    def tearDown(self):
        self._tmp.cleanup()

    def _read_wav(self, path: Path):
        # NB: intentionally not a `with` block — wave.open's context
        # manager closes the underlying file on exit, and callers need to
        # read frames from the returned handle afterward.
        return wave.open(str(path), "rb")

    def test_finalize_produces_valid_wav_with_matching_params(self):
        fmt = AudioFormat(sample_rate=16000, bits_per_sample=16, channels=1)
        store = _store(self.root, format=fmt, day=date(2026, 9, 17))
        store.open()
        store.write(self.pcm[:4000])
        store.write(self.pcm[4000:])
        wav_path = store.finalize()
        store.close()

        self.assertEqual(wav_path, store.input_wav_path)
        wav = self._read_wav(wav_path)
        self.assertEqual(wav.getnchannels(), 1)
        self.assertEqual(wav.getsampwidth(), 2)  # 16-bit
        self.assertEqual(wav.getframerate(), 16000)
        self.assertEqual(wav.getnframes(), len(self.pcm) // 2)
        frames = wav.readframes(wav.getnframes())
        self.assertEqual(frames, self.pcm)

    def test_wav_file_size_is_header_plus_data(self):
        store = _store(self.root, day=date(2026, 9, 17))
        store.open()
        store.write(self.pcm)
        wav_path = store.finalize()
        store.close()
        # 44-byte canonical header (РРИFF/WAVE fmt+data) + exact data.
        self.assertEqual(wav_path.stat().st_size, 44 + len(self.pcm))
        self.assertEqual(wav_path.read_bytes()[:4], b"RIFF")
        self.assertEqual(wav_path.read_bytes()[8:12], b"WAVE")

    def test_zero_byte_stream_yields_valid_empty_wav(self):
        store = _store(self.root, day=date(2026, 9, 17))
        store.open()
        wav_path = store.finalize()  # nothing was written
        store.close()
        wav = self._read_wav(wav_path)
        self.assertEqual(wav.getnframes(), 0)
        self.assertEqual(wav.readframes(0), b"")
        self.assertEqual(wav_path.stat().st_size, 44)

    def test_raw_pcm_deleted_after_finalize(self):
        # After t_cd836888: successful finalize deletes input.pcm, since
        # input.wav is now the durable artifact for a completed turn.
        store = _store(self.root, day=date(2026, 9, 17))
        store.open()
        store.write(self.pcm)
        wav_path = store.finalize()
        store.close()
        self.assertFalse(store.raw_pcm_path.exists())
        self.assertTrue(wav_path.exists())
        wav = self._read_wav(wav_path)
        self.assertEqual(wav.readframes(wav.getnframes()), self.pcm)

    def test_stereo_wav_roundtrip(self):
        fmt = AudioFormat(sample_rate=44100, bits_per_sample=16, channels=2)
        store = _store(self.root, format=fmt, day=date(2026, 9, 17))
        store.open()
        pcm = b"\x10\x00" * 100  # 200 frames of stereo
        store.write(pcm)
        wav_path = store.finalize()
        store.close()
        wav = self._read_wav(wav_path)
        self.assertEqual(wav.getnchannels(), 2)
        self.assertEqual(wav.getframerate(), 44100)
        self.assertEqual(wav.readframes(wav.getnframes()), pcm)


class TestLifecycleGuards(unittest.TestCase):
    """Inherited state machine, exercised through the real store."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = _store(self.root, day=date(2026, 9, 17))

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_before_open_raises(self):
        with self.assertRaises(ArchiveError):
            self.store.write(b"x")

    def test_open_twice_raises(self):
        self.store.open()
        with self.assertRaises(ArchiveError):
            self.store.open()

    def test_finalize_before_open_raises(self):
        with self.assertRaises(ArchiveError):
            self.store.finalize()

    def test_write_after_finalize_raises(self):
        self.store.open()
        self.store.finalize()
        with self.assertRaises(ArchiveError):
            self.store.write(b"x")

    def test_finalize_twice_raises(self):
        self.store.open()
        self.store.finalize()
        with self.assertRaises(ArchiveError):
            self.store.finalize()

    def test_write_after_close_raises(self):
        self.store.open()
        self.store.close()
        with self.assertRaises(ArchiveError):
            self.store.write(b"x")


class TestIOErrorWrapping(unittest.TestCase):
    """Implementations MUST wrap low-level I/O failures in ArchiveError."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_wraps_oserror(self):
        # Make the raw PCM path a DIRECTORY so open(..., "wb") fails with
        # IsADirectoryError (an OSError) — the store must wrap it.
        store = _store(self.root, day=date(2026, 9, 17))
        store.turn_dir.mkdir(parents=True, exist_ok=True)
        store.raw_pcm_path.mkdir()
        with self.assertRaises(ArchiveError) as ctx:
            store.open()
        self.assertIsInstance(ctx.exception.__cause__, OSError)

    def test_write_wraps_oserror(self):
        class _BrokenFile:
            def write(self, data):
                raise OSError("simulated disk failure")

            def flush(self):
                pass

        store = _store(self.root, day=date(2026, 9, 17))
        store.open()
        store._pcm_file = _BrokenFile()  # force a storage-level failure
        with self.assertRaises(ArchiveError) as ctx:
            store.write(b"chunk")
        self.assertIsInstance(ctx.exception.__cause__, OSError)
        # A failed write must not count as accepted.
        self.assertEqual(store.bytes_written, 0)

    def test_close_wraps_oserror(self):
        class _BrokenClose:
            def flush(self):
                raise OSError("simulated flush failure")

            def close(self):
                pass

        store = _store(self.root, day=date(2026, 9, 17))
        store.open()
        store._pcm_file = _BrokenClose()
        with self.assertRaises(ArchiveError) as ctx:
            store.close()
        self.assertIsInstance(ctx.exception.__cause__, OSError)


if __name__ == "__main__":
    unittest.main()
