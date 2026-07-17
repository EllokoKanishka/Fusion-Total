from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from fusion_reader_v2.output_validation import OutputValidationError, stream_file, validate_output_file


class Handler:
    def __init__(self, *, broken: bool = False) -> None:
        self.headers: list[tuple[str, str]] = []
        self.wfile = BrokenWriter() if broken else io.BytesIO()

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def end_headers(self) -> None:
        pass


class BrokenWriter:
    def write(self, _data: bytes) -> None:
        raise BrokenPipeError


class OutputValidationTests(unittest.TestCase):
    def test_inside_outside_missing_type_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "downloads"
            root.mkdir()
            inside = root / "audio.wav"
            inside.write_bytes(b"wave")
            self.assertEqual(validate_output_file(inside, root, suffix=".wav"), inside.resolve())
            outside = Path(tmp) / "outside.wav"
            outside.write_bytes(b"x")
            for path, suffix in ((outside, ".wav"), (inside, ".docx"), (root / "missing.wav", ".wav")):
                with self.assertRaises(OutputValidationError):
                    validate_output_file(path, root, suffix=suffix)
            link = root / "link.wav"
            link.symlink_to(outside)
            with self.assertRaisesRegex(OutputValidationError, "symlink"):
                validate_output_file(link, root, suffix=".wav")

    def test_streams_chunks_and_handles_disconnect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.wav"
            path.write_bytes(b"0123456789")
            handler = Handler()
            self.assertTrue(stream_file(handler, path, content_type="audio/wav", filename="audio.wav", chunk_size=3))
            self.assertEqual(handler.wfile.getvalue(), b"0123456789")
            self.assertIn(("Content-Length", "10"), handler.headers)
            self.assertFalse(stream_file(Handler(broken=True), path, content_type="audio/wav", filename="x.wav"))


if __name__ == "__main__":
    unittest.main()
