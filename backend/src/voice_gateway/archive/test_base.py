"""Tests for the ArchiveStore ABSTRACTION (ТЗ §16, §17) — state machine only.

The contract's lifecycle guards live in the ABC itself, so they are tested
here with a fake in-memory implementation: no filesystem involved. The
fake records every hook call so tests can assert exactly what the ABC
permits an implementation to see.
"""
import unittest
from datetime import date
from pathlib import Path
from uuid import uuid4

from backend.src.voice_gateway.archive import (
    ArchiveError,
    ArchiveStore,
    AudioFormat,
    INPUT_WAV_FILENAME,
    RAW_PCM_FILENAME,
)
from backend.src.voice_gateway.archive.base import ArchiveStore as _BaseStore
from backend.src.voice_gateway.archive.base import (
    ArchiveError as _BaseArchiveError,
)
from backend.src.voice_gateway.archive.base import AudioFormat as _BaseFormat


class FakeStore(ArchiveStore):
    """In-memory implementation: records hook calls, keeps no data on disk."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[str] = []
        self.data = bytearray()
        self.fail_open_with = None
        self.fail_write_with = None
        self.fail_finalize_with = None
        self.fail_close_with = None
        self.write_returns = None  # override bytes-written return value

    # Abstract surface (the only thing a real implementation supplies).

    @property
    def turn_dir(self) -> Path:
        return Path("/fake") / str(self.turn_id)

    @property
    def raw_pcm_path(self) -> Path:
        return self.turn_dir / RAW_PCM_FILENAME

    @property
    def input_wav_path(self) -> Path:
        return self.turn_dir / INPUT_WAV_FILENAME

    def _open(self) -> None:
        self.calls.append("open")
        if self.fail_open_with is not None:
            raise self.fail_open_with

    def _write(self, chunk: bytes) -> int:
        self.calls.append("write")
        if self.fail_write_with is not None:
            raise self.fail_write_with
        self.data.extend(chunk)
        if self.write_returns is not None:
            return self.write_returns
        return len(chunk)

    def _finalize(self) -> Path:
        self.calls.append("finalize")
        if self.fail_finalize_with is not None:
            raise self.fail_finalize_with
        return self.input_wav_path

    def _close(self) -> None:
        self.calls.append("close")
        if self.fail_close_with is not None:
            raise self.fail_close_with


class TestAudioFormat(unittest.TestCase):
    def test_defaults_match_atom_mic_contract(self):
        fmt = AudioFormat()
        self.assertEqual((fmt.sample_rate, fmt.bits_per_sample, fmt.channels),
                         (16000, 16, 1))

    def test_wav_derived_fields(self):
        fmt = AudioFormat(sample_rate=44100, bits_per_sample=16, channels=2)
        self.assertEqual(fmt.bytes_per_sample, 2)
        self.assertEqual(fmt.block_align, 4)
        self.assertEqual(fmt.byte_rate, 44100 * 4)

    def test_invalid_sample_rate(self):
        with self.assertRaises(ValueError):
            AudioFormat(sample_rate=0)
        with self.assertRaises(ValueError):
            AudioFormat(sample_rate=-1)

    def test_invalid_bits_per_sample(self):
        for bad in (7, 12, 100, 0, -16):
            with self.assertRaises(ValueError):
                AudioFormat(bits_per_sample=bad)

    def test_valid_bits_per_sample(self):
        for bits in (8, 16, 24, 32):
            AudioFormat(bits_per_sample=bits)

    def test_invalid_channels(self):
        with self.assertRaises(ValueError):
            AudioFormat(channels=0)
        with self.assertRaises(ValueError):
            AudioFormat(channels=-2)


class TestConstruction(unittest.TestCase):
    def test_accepts_uuid_and_str_turn_id(self):
        turn_id = uuid4()
        s1 = FakeStore(turn_id)
        s2 = FakeStore(str(turn_id))
        self.assertEqual(s1.turn_id, turn_id)
        self.assertEqual(s2.turn_id, turn_id)

    def test_rejects_non_uuid_turn_id(self):
        for bad in ("not-a-uuid", "", 123, None, ["x"]):
            with self.assertRaises(ValueError):
                FakeStore(bad)

    def test_rejects_bad_format(self):
        with self.assertRaises(ValueError):
            FakeStore(uuid4(), format=AudioFormat(sample_rate=0))

    def test_day_is_optional_and_stored(self):
        day = date(2026, 9, 7)
        s1 = FakeStore(uuid4())
        s2 = FakeStore(uuid4(), day=day)
        self.assertIsNone(s1.day)
        self.assertEqual(s2.day, day)

    def test_fresh_store_state(self):
        s = FakeStore(uuid4())
        self.assertEqual(s.bytes_written, 0)


class TestLifecycleHappyPath(unittest.TestCase):
    def test_full_lifecycle(self):
        turn_id = uuid4()
        s = FakeStore(turn_id)
        s.open()
        self.assertEqual(s.write(b"\x00" * 100), 100)
        self.assertEqual(s.write(b"\x01" * 50), 50)
        self.assertEqual(s.bytes_written, 150)
        wav = s.finalize()
        self.assertEqual(wav, Path("/fake") / str(turn_id) / "input.wav")
        s.close()
        self.assertEqual(s.calls, ["open", "write", "write", "finalize", "close"])

    def test_finalize_zero_bytes_is_allowed_by_contract(self):
        s = FakeStore(uuid4())
        s.open()
        self.assertEqual(s.bytes_written, 0)
        # The store must not refuse an empty stream: it still has to produce
        # a well-formed (empty) WAV; the pipeline decides whether that is
        # an audio_invalid error (ТЗ §13).
        s.finalize()

    def test_bytes_written_accumulates_across_chunks(self):
        s = FakeStore(uuid4())
        s.open()
        for i in range(5):
            s.write(bytes(10))
        self.assertEqual(s.bytes_written, 50)

    def test_write_accepts_bytearray_and_memoryview(self):
        s = FakeStore(uuid4())
        s.open()
        self.assertEqual(s.write(bytearray(b"abc")), 3)
        self.assertEqual(s.write(memoryview(b"defg")), 4)
        self.assertEqual(bytes(s.data), b"abcdefg")

    def test_context_manager_opens_and_closes(self):
        s = FakeStore(uuid4())
        with s as entered:
            self.assertIs(s, entered)
            self.assertEqual(s.calls, ["open"])
        self.assertEqual(s.calls, ["open", "close"])

    def test_context_manager_closes_on_exception(self):
        s = FakeStore(uuid4())
        with self.assertRaises(RuntimeError):
            with s:
                raise RuntimeError("stream died")
        self.assertEqual(s.calls, ["open", "close"])


class TestLifecycleErrors(unittest.TestCase):
    def test_open_twice(self):
        s = FakeStore(uuid4())
        s.open()
        with self.assertRaises(ArchiveError):
            s.open()
        self.assertEqual(s.calls, ["open"])

    def test_write_before_open(self):
        s = FakeStore(uuid4())
        with self.assertRaises(ArchiveError):
            s.write(b"abc")
        self.assertEqual(s.calls, [])

    def test_write_after_close(self):
        s = FakeStore(uuid4())
        s.open()
        s.close()
        with self.assertRaises(ArchiveError):
            s.write(b"abc")
        self.assertEqual(s.calls, ["open", "close"])

    def test_write_after_finalize(self):
        s = FakeStore(uuid4())
        s.open()
        s.finalize()
        with self.assertRaises(ArchiveError):
            s.write(b"abc")
        self.assertEqual(s.calls, ["open", "finalize"])

    def test_finalize_before_open(self):
        s = FakeStore(uuid4())
        with self.assertRaises(ArchiveError):
            s.finalize()
        self.assertEqual(s.calls, [])

    def test_finalize_twice(self):
        s = FakeStore(uuid4())
        s.open()
        s.finalize()
        with self.assertRaises(ArchiveError):
            s.finalize()
        self.assertEqual(s.calls, ["open", "finalize"])

    def test_open_after_close(self):
        s = FakeStore(uuid4())
        s.open()
        s.close()
        with self.assertRaises(ArchiveError):
            s.open()
        self.assertEqual(s.calls, ["open", "close"])

    def test_close_is_idempotent_and_never_raises(self):
        s = FakeStore(uuid4())
        s.close()  # never opened: still a safe no-op
        s.close()  # again: no-op, hook not called twice
        self.assertEqual(s.calls, ["close"])
        # ``closed`` is terminal (module docstring state machine): no reopen.
        with self.assertRaises(ArchiveError):
            s.open()

    def test_error_path_close_without_finalize_is_supported(self):
        # ТЗ §32: turn failed before EOF -> close() WITHOUT finalize();
        # the ABC must not force finalize on this path.
        s = FakeStore(uuid4())
        s.open()
        s.write(b"\x00" * 32)
        s.close()  # raw PCM must be left in place by the implementation
        self.assertEqual(s.calls, ["open", "write", "close"])


class TestErrorWrapping(unittest.TestCase):
    def test_oserror_in_open_wrapped_with_cause(self):
        s = FakeStore(uuid4())
        s.fail_open_with = OSError("read-only fs")
        with self.assertRaises(ArchiveError) as ctx:
            s.open()
        self.assertIsInstance(ctx.exception.__cause__, OSError)
        # A failed open leaves the store usable: open may be retried.
        s.fail_open_with = None
        s.open()
        self.assertEqual(s.calls, ["open", "open"])

    def test_oserror_in_write_wrapped_and_not_counted(self):
        s = FakeStore(uuid4())
        s.open()
        s.fail_write_with = OSError("disk full")
        with self.assertRaises(ArchiveError) as ctx:
            s.write(b"abc")
        self.assertIsInstance(ctx.exception.__cause__, OSError)
        self.assertEqual(s.bytes_written, 0)

    def test_oserror_in_finalize_wrapped_and_state_not_finalized(self):
        s = FakeStore(uuid4())
        s.open()
        s.fail_finalize_with = OSError("no space")
        with self.assertRaises(ArchiveError):
            s.finalize()
        self.assertEqual(s.calls, ["open", "finalize"])
        # Still usable after a failed finalize.
        s.fail_finalize_with = None
        s.finalize()
        self.assertEqual(s.calls, ["open", "finalize", "finalize"])

    def test_oserror_in_close_wrapped_and_close_can_retry(self):
        s = FakeStore(uuid4())
        s.open()
        s.fail_close_with = OSError("fs frozen")
        with self.assertRaises(ArchiveError):
            s.close()
        s.fail_close_with = None
        s.close()
        self.assertEqual(s.calls, ["open", "close", "close"])

    def test_native_archive_error_from_hook_not_double_wrapped(self):
        s = FakeStore(uuid4())
        inner = ArchiveError("boom from implementation")
        s.fail_write_with = inner
        s.open()
        with self.assertRaises(ArchiveError) as ctx:
            s.write(b"x")
        self.assertIs(ctx.exception, inner)

    def test_negative_write_size_rejected(self):
        s = FakeStore(uuid4())
        s.open()
        s.write_returns = -1
        with self.assertRaises(ArchiveError):
            s.write(b"abc")

    def test_error_is_the_single_exception_type(self):
        # The pipeline catches ONE exception type no matter the backend.
        self.assertIs(ArchiveError, _BaseArchiveError)
        self.assertTrue(issubclass(ArchiveError, Exception))


class TestPackageReexports(unittest.TestCase):
    def test_abstraction_and_supporting_types_exported(self):
        # The pipeline (app.py) imports the contract through the package.
        self.assertIs(_BaseStore, ArchiveStore)
        self.assertIs(_BaseFormat, AudioFormat)
        self.assertTrue(ArchiveStore is not None)

    def test_constants_match_tz_layout(self):
        self.assertEqual(RAW_PCM_FILENAME, "input.pcm")
        self.assertEqual(INPUT_WAV_FILENAME, "input.wav")


if __name__ == "__main__":
    unittest.main()
