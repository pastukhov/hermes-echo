"""Tests for the centralized atomic write of ``metadata.json`` (ТЗ §19).

Covers the full-or-no guarantee: the file is either fully written with
complete JSON or left as its previous complete version / absent — never a
half-written file, and no leaked temp files on failure.
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from uuid import uuid4

from backend.src.voice_gateway.archive import (
    METADATA_FILENAME,
    ArchiveStore,
    TurnMetadata,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_metadata,
)


def _tmpdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="archive-test-")


class TestAtomicWriteBytes(unittest.TestCase):
    def setUp(self):
        self._tmp = _tmpdir()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_bytes_and_creates_parents(self):
        target = self.dir / "a" / "b" / "out.bin"
        result = atomic_write_bytes(target, b"hello")
        self.assertEqual(result, target)
        self.assertEqual(target.read_bytes(), b"hello")

    def test_overwrite_replaces_content_completely(self):
        target = self.dir / "f.txt"
        atomic_write_bytes(target, b"original content")
        atomic_write_bytes(target, b"new")
        self.assertEqual(target.read_bytes(), b"new")

    def test_failure_keeps_previous_version_and_leaves_no_temp(self):
        target = self.dir / "f.txt"
        atomic_write_bytes(target, b"original")
        with mock.patch("os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_write_bytes(target, b"new data")
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(
            [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")],
            [],
        )

    def test_failure_with_no_existing_target_leaves_no_file(self):
        target = self.dir / "never-existed.json"
        with mock.patch("os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_write_bytes(target, b"data")
        self.assertFalse(target.exists())
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_dir_fsync_failure_after_rename_still_succeeds(self):
        # The rename is the point of no return: the file fsync (1st call)
        # succeeds, then the best-effort directory fsync (2nd call) fails —
        # the write must still succeed with the new content in place.
        target = self.dir / "f.txt"
        with mock.patch(
            "os.fsync", side_effect=[None, OSError("no dir fsync here")]
        ):
            atomic_write_bytes(target, b"data")
        self.assertEqual(target.read_bytes(), b"data")


class TestAtomicWriteJson(unittest.TestCase):
    def setUp(self):
        self._tmp = _tmpdir()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip_valid_json(self):
        payload = {"turn_id": str(uuid4()), "status": "success", "nested": [1, 2, 3]}
        target = self.dir / "metadata.json"
        result = atomic_write_json(target, payload)
        self.assertEqual(result, target)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), payload)

    def test_cyrillic_written_as_utf8_not_escaped(self):
        target = self.dir / "metadata.json"
        atomic_write_json(target, {"transcript": "привет мир"})
        raw = target.read_text(encoding="utf-8")
        self.assertIn("привет мир", raw)
        self.assertNotIn("\\u043f", raw)


class TestAtomicWriteMetadata(unittest.TestCase):
    def setUp(self):
        self._tmp = _tmpdir()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_metadata_json_into_turn_dir(self):
        turn_dir = self.dir / "atom-echo-01" / "turn-1"
        metadata = {
            "turn_id": str(uuid4()),
            "device_id": "atom-echo-01",
            "status": "success",
            "transcript": "запиши заметку",
        }
        path = atomic_write_metadata(metadata, turn_dir)
        self.assertEqual(path, turn_dir / METADATA_FILENAME)
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")), metadata
        )

    def test_rewrite_replaces_previous_complete_version(self):
        turn_dir = self.dir / "d" / "t"
        v1 = {"turn_id": str(uuid4()), "status": "stt_failed", "error": "boom"}
        v2 = {"turn_id": "x", "status": "success"}
        atomic_write_metadata(v1, turn_dir)
        atomic_write_metadata(v2, turn_dir)
        self.assertEqual(
            json.loads((turn_dir / METADATA_FILENAME).read_text(encoding="utf-8")),
            v2,
        )

    def test_failure_leaves_previous_metadata_intact(self):
        turn_dir = self.dir / "d" / "t"
        v1 = {"turn_id": str(uuid4()), "status": "recording"}
        atomic_write_metadata(v1, turn_dir)
        with mock.patch("os.fsync", side_effect=OSError("io error")):
            with self.assertRaises(OSError):
                atomic_write_metadata({"status": "corrupted?"}, turn_dir)
        # Reader sees the previous complete version, not a partial write.
        self.assertEqual(
            json.loads((turn_dir / METADATA_FILENAME).read_text(encoding="utf-8")),
            v1,
        )


class TestArchiveStore(unittest.TestCase):
    def setUp(self):
        self._tmp = _tmpdir()
        self.dir = Path(self._tmp.name)
        self.store = ArchiveStore(root=self.dir)

    def tearDown(self):
        self._tmp.cleanup()

    def test_turn_dir_layout_date_partitioned(self):
        # ТЗ §18: archive/YYYY/MM/DD/<turn-id>/
        turn_id = uuid4()
        self.assertEqual(
            self.store.turn_dir(turn_id, day=date(2026, 9, 7)),
            self.dir / "2026" / "09" / "07" / str(turn_id),
        )

    def test_turn_defaults_to_today_and_accepts_str_turn_id(self):
        turn_id = uuid4()
        d = date.today()
        self.assertEqual(
            self.store.turn_dir(str(turn_id)),
            self.dir / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}" / str(turn_id),
        )

    def test_turn_dir_rejects_non_uuid(self):
        with self.assertRaises((ValueError, TypeError)):
            self.store.turn_dir("not-a-uuid")

    def test_save_metadata_writes_expected_path(self):
        turn_id = uuid4()
        day = date.today()
        path = self.store.save_metadata(turn_id, {"turn_id": str(turn_id), "status": "success"}, day=day)
        self.assertEqual(
            path, self.dir / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" / str(turn_id) / METADATA_FILENAME
        )
        self.assertTrue(path.is_file())
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["status"], "success"
        )

    def test_save_metadata_roundtrip_through_turn_metadata_model(self):
        turn_id = uuid4()
        model = TurnMetadata(
            turn_id=turn_id,
            device_id="atom-echo-01",
            status="success",
            transcript="привет",
            audio_duration_ms=12340,
        )
        path = self.store.save_metadata(turn_id, model.model_dump(mode="json"))
        loaded = TurnMetadata.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(loaded, model)

    def test_failed_turn_metadata_still_saved(self):
        # ТЗ §19: on errors metadata is still saved with the failure status.
        turn_id = uuid4()
        model = TurnMetadata(
            turn_id=turn_id, device_id="atom-echo-01",
            status="stt_failed", error="STT service unavailable",
        )
        path = self.store.save_metadata(turn_id, model.model_dump(mode="json"))
        loaded = TurnMetadata.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(loaded, model)
        self.assertEqual(loaded.error_code(), "stt_failed")


if __name__ == "__main__":
    unittest.main()
