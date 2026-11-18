from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fusion_reader_v2.reading_timings import ReadingTimings, align_word_times


def segments(*words):
    return [{"words": [{"word": word, "start": start, "end": end} for word, start, end in words]}]


class ReadingTimingTests(unittest.TestCase):
    def test_accents_original_offsets_repeated_words_and_no_fabricated_times(self) -> None:
        text = "🩺 La enseñanza de la filosofía."
        result = align_word_times(
            text,
            segments(
                ("La", 0.2, 0.4), ("ensenanza", 0.4, 1.3), ("de", 1.3, 1.5), ("la", 1.5, 1.7), ("filosofía", 1.7, 2.5)
            ),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"], 1)
        self.assertEqual(result["words"][0], {"start": 0.2, "end": 0.4, "start_offset": 3, "end_offset": 5})
        self.assertEqual(result["words"][3]["start"], 1.5)
        self.assertEqual(result["words"][1]["end_offset"], 15)
        partial = align_word_times(
            "La enseñanza rara de la filosofía",
            segments(
                ("La", 0.2, 0.4), ("enseñanza", 0.4, 1.3), ("de", 1.5, 1.7), ("la", 1.7, 1.8), ("filosofía", 1.8, 2.5)
            ),
        )
        self.assertTrue(partial["ok"])
        self.assertEqual(len(partial["words"]), 5)
        self.assertLess(partial["coverage"], 1)

    def test_bad_or_unrelated_timings_fail_closed(self) -> None:
        for data in (
            [],
            segments(("otra", 0, 1)),
            segments(("texto", float("nan"), 1)),
            segments(("texto", 2, 1)),
            segments(("texto", 0, 0)),
        ):
            with self.subTest(data=data):
                result = align_word_times("texto", data)
                self.assertFalse(result["ok"])
                self.assertNotIn("words", result)

    def test_network_request_is_deduplicated_cached_and_content_bound(self) -> None:
        requests = []
        reached = threading.Event()
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                requests.append(
                    (parse_qs(urlsplit(self.path).query), self.rfile.read(int(self.headers["Content-Length"])))
                )
                reached.set()
                release.wait(3)
                raw = json.dumps({"ok": True, "segments": segments(("La", 0.1, 0.3), ("enseñanza", 0.3, 1.1))}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        runner = threading.Thread(target=server.serve_forever)
        runner.start()
        workers = []

        def spawn(*, target, args, name):
            worker = threading.Thread(target=target, args=args, name=name)
            workers.append(worker)
            worker.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "audio.wav"
                audio.write_bytes(b"original audio")
                service = ReadingTimings(
                    root=root / "timings", stt_url=f"http://127.0.0.1:{server.server_port}", spawn=spawn
                )
                self.assertEqual(service.start(audio, "La enseñanza", "es")["state"], "pending")
                self.assertTrue(reached.wait(3))
                self.assertEqual(service.start(audio, "La enseñanza", "es")["state"], "pending")
                release.set()
                workers[-1].join(3)
                result = service.start(audio, "La enseñanza", "es")
                self.assertEqual(result["state"], "ready")
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0][0]["word_timestamps"], ["1"])
                self.assertEqual(requests[0][1], b"original audio")
                restored = ReadingTimings(root=root / "timings", stt_url=service.url.geturl(), spawn=spawn)
                self.assertTrue(restored.start(audio, "La enseñanza", "es")["cached"])
                audio.write_bytes(b"replaced audio")
                self.assertEqual(restored.start(audio, "La enseñanza", "es")["state"], "pending")
                workers[-1].join(3)
                self.assertEqual(len(requests), 2)
                service.request_shutdown()
                self.assertEqual(service.start(audio, "La enseñanza", "es")["detail"], "closed")
                restored.request_shutdown()
        finally:
            release.set()
            for worker in workers:
                worker.join(3)
            server.shutdown()
            runner.join(3)
            server.server_close()

    def test_shutdown_cancels_in_flight_local_recognition_and_joins_worker(self) -> None:
        reached = threading.Event()
        cancelled = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path.startswith("/cancel/"):
                    cancelled.set()
                else:
                    reached.set()
                    cancelled.wait(3)
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        runner = threading.Thread(target=server.serve_forever)
        runner.start()
        workers = []

        def spawn(*, target, args, name):
            worker = threading.Thread(target=target, args=args, name=name)
            workers.append(worker)
            worker.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                audio = Path(tmp) / "audio.wav"
                audio.write_bytes(b"audio")
                service = ReadingTimings(
                    root=Path(tmp) / "timings", stt_url=f"http://127.0.0.1:{server.server_port}", spawn=spawn
                )
                service.start(audio, "texto", "es")
                self.assertTrue(reached.wait(3))
                service.request_shutdown()
                workers[0].join(3)
                self.assertFalse(workers[0].is_alive())
                self.assertTrue(cancelled.is_set())
        finally:
            cancelled.set()
            for worker in workers:
                worker.join(3)
            server.shutdown()
            runner.join(3)
            server.server_close()

    def test_never_sends_reader_audio_to_remote_provider(self) -> None:
        service = ReadingTimings(root=Path("unused"), stt_url="https://external.example", spawn=lambda **_: None)
        self.assertEqual(
            service.start(Path("missing.wav"), "texto", "es")["detail"], "local_word_timing_provider_required"
        )
