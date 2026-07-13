from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fusion_reader_v2.output_reservation import reserve_output_path


class OutputReservationTests(unittest.TestCase):
    def test_reservation_is_exclusive_publishes_and_cleans_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = reserve_output_path(root, "result.wav", default_suffix=".wav")
            second = reserve_output_path(root, "result.wav", default_suffix=".wav")
            self.assertNotEqual(first.path, second.path)
            source = root / "partial.wav"
            source.write_bytes(b"audio")
            first.publish(source)
            first.cleanup()
            self.assertEqual(first.path.read_bytes(), b"audio")
            second.cleanup()
            self.assertFalse(second.path.exists())

    def test_existing_file_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "document.docx"
            existing.write_bytes(b"original")
            reservation = reserve_output_path(root, existing.name, default_suffix=".docx")
            self.assertNotEqual(reservation.path, existing)
            reservation.cleanup()
            self.assertEqual(existing.read_bytes(), b"original")

    def test_default_suffix_is_applied_before_reserving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reservation = reserve_output_path(tmp, "result", default_suffix=".wav")
            self.assertEqual(reservation.path.name, "result.wav")
            reservation.cleanup()


if __name__ == "__main__":
    unittest.main()
