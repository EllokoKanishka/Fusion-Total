from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from fusion_reader_v2 import media
from fusion_reader_v2.dialogue import TranscriptSegment


class MediaHelperTests(unittest.TestCase):
    def test_probe_media_reports_dependency_and_input_failures(self) -> None:
        source = Path("conference.bin")
        with mock.patch.object(media.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ffprobe_not_available"):
                media.probe_media(source)

        cases = (
            (CompletedProcess([], 1, "", "bad"), "media_unreadable"),
            (CompletedProcess([], 0, "not-json", ""), "media_probe_invalid"),
            (
                CompletedProcess([], 0, json.dumps({"streams": [{"codec_type": "video"}]}), ""),
                "media_without_audio",
            ),
        )
        for process, error in cases:
            with self.subTest(error=error):
                with (
                    mock.patch.object(media.shutil, "which", return_value="/usr/bin/ffprobe"),
                    mock.patch.object(media, "run_owned", return_value=process),
                    self.assertRaisesRegex(ValueError, error),
                ):
                    media.probe_media(source)

    def test_probe_media_normalizes_invalid_and_valid_metadata(self) -> None:
        source = Path("conference.wav")
        payloads = (
            ({"streams": [{"codec_type": "audio"}], "format": {"duration": "bad"}}, 0.0, ""),
            (
                {
                    "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}],
                    "format": {"duration": "12.5", "format_name": "wav"},
                },
                12.5,
                "pcm_s16le",
            ),
        )
        for payload, duration, codec in payloads:
            with self.subTest(duration=duration):
                process = CompletedProcess([], 0, json.dumps(payload), "")
                with (
                    mock.patch.object(media.shutil, "which", return_value="/usr/bin/ffprobe"),
                    mock.patch.object(media, "run_owned", return_value=process),
                ):
                    result = media.probe_media(source)
                self.assertEqual(result.duration_seconds, duration)
                self.assertEqual(result.audio_codec, codec)

    def test_normalize_media_audio_validates_binary_process_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.wav"
            source.write_bytes(b"source")
            target = root / "normalized.flac"

            with mock.patch.object(media.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg_not_available"):
                    media.normalize_media_audio(source, target, timeout_seconds=1)

            for returncode, output, expected_error in ((1, b"audio", True), (0, b"", True), (0, b"audio", False)):
                with self.subTest(returncode=returncode, output=bool(output)):
                    target.unlink(missing_ok=True)

                    def run(*_args, **_kwargs):
                        if output:
                            target.write_bytes(output)
                        return CompletedProcess([], returncode, "", "")

                    with (
                        mock.patch.object(media.shutil, "which", return_value="/usr/bin/ffmpeg"),
                        mock.patch.object(media, "run_owned", side_effect=run),
                    ):
                        if expected_error:
                            with self.assertRaisesRegex(RuntimeError, "media_audio_conversion_failed"):
                                media.normalize_media_audio(source, target, timeout_seconds=1)
                        else:
                            media.normalize_media_audio(source, target, timeout_seconds=1)

    def test_transcript_paragraphs_handles_plain_blank_and_timed_segments(self) -> None:
        self.assertEqual(media.transcript_paragraphs((), ""), [])
        plain = media.transcript_paragraphs((), "Uno. Dos." * 200)
        self.assertGreater(len(plain), 1)

        segments = (
            TranscriptSegment(start=0, end=1, text="   "),
            TranscriptSegment(start=2, end=3, text="Primero."),
            TranscriptSegment(start=3, end=40, text="Segundo."),
            TranscriptSegment(start=41, end=42, text="Final."),
        )
        paragraphs = media.transcript_paragraphs(segments, "unused")
        self.assertEqual(paragraphs, [(2, "Primero. Segundo."), (41, "Final.")])

    def test_transcript_body_omits_document_heading_and_language_metadata(self) -> None:
        paragraphs = [(0.0, "Primero."), (65.0, "Segundo.")]
        body = media.transcript_body_text(paragraphs)
        document = media.transcript_document_text("Clase — Transcripción", "en", paragraphs)

        self.assertEqual(body, "[00:00:00] Primero.\n\n[00:01:05] Segundo.")
        self.assertNotIn("Clase", body)
        self.assertNotIn("Idioma detectado:", body)
        self.assertIn("Clase — Transcripción", document)
        self.assertIn("Idioma detectado: en", document)
        self.assertTrue(document.endswith(body))

    def test_plain_transcript_split_covers_long_words_and_sentence_packing(self) -> None:
        self.assertEqual(media._split_plain_transcript("   ", max_chars=5), [])
        self.assertEqual(media._split_plain_transcript("alpha beta gamma", max_chars=6), ["alpha", "beta", "gamma"])
        self.assertEqual(media._split_plain_transcript("Uno. Dos.", max_chars=5), ["Uno.", "Dos."])

    def test_empty_transcript_pdf_is_still_downloadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "empty.pdf"
            media.write_transcript_pdf(output, title="Vacío", subtitle="Sin voz", paragraphs=[])
            self.assertGreater(output.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
