from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_cross_filesystem_publish_copies_fsyncs_and_replaces_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.docx"
            source.write_bytes(b"converted document")
            reservation = reserve_output_path(root, "result.docx", default_suffix=".docx")
            real_replace = os.replace
            calls = 0

            def simulated_replace(src, dst):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError(errno.EXDEV, "cross-device link")
                return real_replace(src, dst)

            with mock.patch("fusion_reader_v2.output_reservation.os.replace", side_effect=simulated_replace):
                published = reservation.publish(source)

            self.assertEqual(published, reservation.path)
            self.assertEqual(reservation.path.read_bytes(), b"converted document")
            self.assertFalse(source.exists())
            self.assertTrue(reservation.published)
            self.assertEqual(calls, 2)
            self.assertEqual(list(root.glob("*.publish")), [])

    def test_cross_filesystem_failure_preserves_cleanup_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.wav"
            source.write_bytes(b"audio")
            reservation = reserve_output_path(root, "result.wav", default_suffix=".wav")
            with mock.patch(
                "fusion_reader_v2.output_reservation.os.replace",
                side_effect=OSError(errno.EXDEV, "cross-device link"),
            ):
                with self.assertRaises(OSError):
                    reservation.publish(source)
            reservation.cleanup()
            self.assertFalse(reservation.path.exists())
            self.assertTrue(source.exists())
            self.assertEqual(list(root.glob("*.publish")), [])


if __name__ == "__main__":
    unittest.main()
