from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fusion_reader_v2.config import create_settings
from fusion_reader_v2.pdf_to_docx import ConversionResult, JobStatus
from fusion_reader_v2.web import server as web_server
from tests.helpers import SyntheticWavTTSProvider, managed_test_app


class WebServerIntegrationTests(unittest.TestCase):
    def _settings(self, root: Path, *, remote: bool = False):
        settings = create_settings(
            repository_root=Path.cwd(),
            environ={
                "HOME": str(root / "home"),
                "FUSION_READER_RUNTIME_ROOT": str(root / "runtime"),
                "FUSION_READER_LIBRARY_ROOT": str(root / "library"),
                "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
            },
        )
        settings = replace(settings, ports=replace(settings.ports, api=0))
        if remote:
            settings = replace(
                settings,
                security=replace(settings.security, allow_remote=True, api_token="test-token"),
            )
        return settings

    def _start(self, root: Path, *, remote: bool = False, synthetic_tts: bool = False):
        tts = SyntheticWavTTSProvider() if synthetic_tts else None
        app_context = managed_test_app(root=root / "app", tts=tts)
        app = app_context.__enter__()
        server = web_server.create_http_server(app, self._settings(root, remote=remote))
        thread = threading.Thread(target=server.serve_forever, name="fusion-test-http")
        thread.start()
        self.addCleanup(app_context.__exit__, None, None, None)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3.0)
        self.addCleanup(server.shutdown)
        return server

    def _request(self, base: str, path: str, payload: dict | None = None, *, method: str | None = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base + path,
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method or ("POST" if data is not None else "GET"),
        )
        with urllib.request.urlopen(request, timeout=5.0) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            return response.status, json.loads(raw) if "application/json" in content_type and raw else raw

    def test_importing_compatibility_server_has_no_runtime_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            env = dict(os.environ)
            env.update(
                {
                    "HOME": str(root / "home"),
                    "FUSION_READER_RUNTIME_ROOT": str(runtime),
                    "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import scripts.fusion_reader_v2_server; "
                    "import fusion_reader_v2.web.server as web; "
                    "assert web.APP is None",
                ],
                cwd=Path.cwd(),
                env=env,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(runtime.exists())

    def test_static_liveness_and_readiness_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            base = f"http://127.0.0.1:{server.server_address[1]}"
            for path, marker in (
                ("/", b"Fusion Reader v2"),
                ("/static/styles.css", b"--accent"),
                ("/static/app.js", b"readCurrent"),
                ("/health/live", b'"status": "live"'),
                ("/health/ready", b'"reader_ready": true'),
            ):
                with self.subTest(path=path), urllib.request.urlopen(base + path, timeout=3.0) as response:
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertIn(marker, body)
                    self.assertTrue(response.headers.get("X-Request-ID"))
                    self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")

    def test_remote_mode_requires_token_for_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), remote=True)
            url = f"http://127.0.0.1:{server.server_address[1]}/api/load"
            body = json.dumps({"doc_id": "d", "title": "T", "text": "Texto"}).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(request, timeout=3.0)
            self.assertEqual(denied.exception.code, 401)

            request.add_header("Authorization", "Bearer test-token")
            with urllib.request.urlopen(request, timeout=3.0) as response:
                payload = json.loads(response.read())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["doc_id"], "d")

    def test_two_servers_keep_application_state_and_jobs_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._start(root / "first")
            second = self._start(root / "second")
            self.assertIsNot(first.context, second.context)
            self.assertIsNot(first.context.import_jobs, second.context.import_jobs)
            self.assertIsNone(web_server.APP)

            def load(server, doc_id: str) -> None:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/load",
                    data=json.dumps({"doc_id": doc_id, "title": doc_id, "text": "Texto"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3.0) as response:
                    self.assertEqual(response.status, 200)

            load(first, "first-doc")
            load(second, "second-doc")
            self.assertEqual(first.context.app.status()["doc_id"], "first-doc")
            self.assertEqual(second.context.app.status()["doc_id"], "second-doc")

    def test_status_exposes_bounded_operational_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            url = f"http://127.0.0.1:{server.server_address[1]}/api/status"
            with urllib.request.urlopen(url, timeout=3.0) as response:
                payload = json.loads(response.read())
            for key in (
                "version",
                "commit",
                "pid",
                "uptime_seconds",
                "state_schema",
                "cache",
                "jobs",
                "providers",
                "ports",
                "warnings",
                "degradations",
            ):
                self.assertIn(key, payload)
            self.assertEqual(payload["ports"]["tts_gpu"], 7853)
            self.assertEqual(payload["ports"]["tts_cpu"], 7851)

    def test_malformed_json_and_content_type_return_stable_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            url = f"http://127.0.0.1:{server.server_address[1]}/api/load"
            cases = (
                (b"{", "application/json", "invalid_json"),
                (b"plain", "text/plain", "application_json_required"),
                (b"[]", "application/json", "json_object_required"),
            )
            for body, content_type, error in cases:
                with self.subTest(error=error):
                    request = urllib.request.Request(
                        url,
                        data=body,
                        headers={"Content-Type": content_type},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        urllib.request.urlopen(request, timeout=3.0)
                    payload = json.loads(raised.exception.read())
                    self.assertEqual(payload["error"], error)
                    self.assertTrue(payload["request_id"])

    def test_daily_api_route_matrix_and_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), synthetic_tts=True)
            base = f"http://127.0.0.1:{server.server_address[1]}"

            for path in (
                "/health",
                "/api/status",
                "/api/build",
                "/api/library",
                "/api/voice/voices",
                "/api/voices",
                "/api/voice/metrics",
                "/api/voice/metrics/summary",
                "/api/voice/metrics/documents",
                "/api/voice/metrics/chunks?doc_id=matrix&limit=5",
                "/api/prepare/status",
                "/api/audio-export/status",
                "/api/references",
                "/api/notes?current_only=1&chunk_index=0",
                "/api/dialogue/status",
            ):
                with self.subTest(path=path):
                    self.assertEqual(self._request(base, path)[0], 200)

            for path in ("/", "/health", "/api/status", "/static/app.js", "/static/missing.js"):
                request = urllib.request.Request(base + path, method="HEAD")
                expected = 404 if path.endswith("missing.js") else 200
                try:
                    with urllib.request.urlopen(request, timeout=5.0) as response:
                        self.assertEqual(response.status, expected)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, expected)

            text = "\n\n".join(f"Bloque {index}. " + ("texto " * 120) for index in range(1, 6))
            status, loaded = self._request(
                base,
                "/api/load",
                {"doc_id": "matrix", "title": "Matrix", "text": text},
            )
            self.assertEqual(status, 200)
            self.assertTrue(loaded["ok"])

            for path, payload in (
                ("/api/next", {}),
                ("/api/previous", {}),
                ("/api/jump", {"index": 2}),
                ("/api/reasoning/mode", {"mode": "thinking"}),
                ("/api/laboratory/mode", {"mode": "free"}),
                ("/api/profile", {"mode": "academica"}),
                ("/api/veil", {"mode": "lucy"}),
                ("/api/voice", {"voice": "female_03.wav"}),
                ("/api/dialogue/reset", {}),
                ("/api/laboratory/reset", {}),
                ("/api/chat/reset", {}),
            ):
                with self.subTest(path=path):
                    self.assertEqual(self._request(base, path, payload)[0], 200)

            _, read = self._request(base, "/api/read", {"play": False})
            self.assertTrue(read["ok"])
            self.assertTrue(read["audio_url"])
            self.assertEqual(self._request(base, read["audio_url"])[0], 200)
            request = urllib.request.Request(base + read["audio_url"], method="HEAD")
            with urllib.request.urlopen(request, timeout=5.0) as response:
                self.assertEqual(response.status, 200)

            self.assertEqual(self._request(base, "/api/voice/test", {"text": "Voz", "play": False})[0], 200)
            self.assertEqual(self._request(base, "/api/chat", {"message": "Resume el bloque"})[0], 200)
            self.assertEqual(self._request(base, "/api/dialogue/turn", {"text": "Resume"})[0], 200)

            _, note = self._request(base, "/api/notes/create", {"text": "Nota matrix", "chunk_index": 0})
            note_id = note["note"]["note_id"]
            doc_id = note["note"]["doc_id"]
            self._request(base, "/api/notes/update", {"note_id": note_id, "doc_id": doc_id, "text": "Editada"})
            self._request(base, "/api/notes/rename", {"note_id": note_id, "doc_id": doc_id, "label": "Clave"})
            self._request(base, "/api/notes/delete", {"note_id": note_id, "doc_id": doc_id})

            self._request(
                base,
                "/api/load",
                {"doc_id": "ref", "title": "Referencia", "text": "Texto de referencia", "role": "reference"},
            )
            self._request(base, "/api/reference/promote", {"doc_id": "ref"})
            self._request(base, "/api/reference/remove", {"doc_id": "matrix"})

            encoded = base64.b64encode(b"Texto importado").decode("ascii")
            self.assertEqual(
                self._request(
                    base,
                    "/api/import",
                    {"filename": "importado.txt", "mime": "text/plain", "data_b64": encoded},
                )[0],
                200,
            )

            self._request(base, "/api/load", {"doc_id": "export", "title": "Export", "text": text})
            self._request(base, "/api/prepare/start", {"start": "cursor"})
            self._request(base, "/api/prepare/cancel", {})
            _, export = self._request(base, "/api/audio-export", {"mode": "current"})
            job_id = export["job_id"]
            for _ in range(100):
                _, export_status = self._request(base, f"/api/audio-export/status/{job_id}")
                if export_status["state"] in {"done", "error", "cancelled"}:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(export_status["state"], "done")
            self.assertEqual(self._request(base, f"/api/audio-export/download/{job_id}")[0], 200)

            second = self._request(base, "/api/audio-export", {"mode": "full"})[1]
            self._request(base, f"/api/audio-export/cancel/{second['job_id']}", {})

            raw_request = urllib.request.Request(
                base + "/api/import-file?filename=raw.txt&mime=text/plain",
                data=b"Carga directa",
                headers={"Content-Type": "text/plain"},
                method="POST",
            )
            with urllib.request.urlopen(raw_request, timeout=5.0) as response:
                self.assertEqual(response.status, 200)

            async_request = urllib.request.Request(
                base + "/api/import-file/start?filename=async.txt&mime=text/plain",
                data=b"Carga asincrona",
                headers={"Content-Type": "text/plain"},
                method="POST",
            )
            with urllib.request.urlopen(async_request, timeout=5.0) as response:
                async_job = json.loads(response.read())
            for _ in range(100):
                try:
                    _, import_status = self._request(base, f"/api/import-status?id={async_job['job_id']}")
                except urllib.error.HTTPError:
                    continue
                if import_status["status"] in {"done", "error"}:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(import_status["status"], "done")

            pdf_job = JobStatus(job_id="pdf-status", filename="matrix.docx")
            server.context.pdf_jobs.add(pdf_job.job_id, pdf_job)
            self.assertEqual(self._request(base, "/api/tools/pdf-to-docx/status/pdf-status")[0], 200)
            self._request(base, "/api/tools/pdf-to-docx/cancel/pdf-status", {})

            docx = server.context.pdf_root / "download.docx"
            docx.parent.mkdir(parents=True, exist_ok=True)
            docx.write_bytes(b"PK synthetic docx")
            item = web_server.register_pdf_to_docx_download(
                server.context,
                docx,
                docx.name,
                ConversionResult(True, output_path=str(docx)),
            )
            self.assertEqual(self._request(base, f"/api/tools/pdf-to-docx/download/{item['id']}")[0], 200)

            self.assertEqual(self._request(base, "/api/document/clear", {})[0], 200)

    def test_pdf_multipart_upload_uses_background_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp))
            base = f"http://127.0.0.1:{server.server_address[1]}"
            boundary = "fusion-test-boundary"
            body = (
                (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="file"; filename="sample.pdf"\r\n'
                    "Content-Type: application/pdf\r\n\r\n"
                ).encode()
                + b"%PDF-1.4 synthetic\n"
                + f"\r\n--{boundary}--\r\n".encode()
            )

            def convert(_input: Path, output: Path, job: JobStatus):
                output.write_bytes(b"PK synthetic docx")
                job.state = "running"
                return ConversionResult(True, output_path=str(output), pages=1)

            request = urllib.request.Request(
                base + "/api/tools/pdf-to-docx",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with patch.object(web_server, "convert_pdf_to_docx", side_effect=convert):
                with urllib.request.urlopen(request, timeout=5.0) as response:
                    created = json.loads(response.read())
                for _ in range(100):
                    _, status = self._request(base, f"/api/tools/pdf-to-docx/status/{created['job_id']}")
                    if status["state"] in {"done", "error"}:
                        break
                    threading.Event().wait(0.01)
            self.assertEqual(status["state"], "done")

    def test_http_missing_resource_and_validation_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start(Path(tmp), synthetic_tts=True)
            base = f"http://127.0.0.1:{server.server_address[1]}"

            for path in (
                "/missing",
                "/static/missing.js",
                "/api/import-status?id=missing",
                "/api/tools/pdf-to-docx/status/missing",
                "/api/tools/pdf-to-docx/download/missing",
                "/api/audio-export/status/missing",
                "/api/audio-export/download/missing",
                "/audio/missing.wav",
            ):
                with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError):
                    self._request(base, path)

            for path in ("/missing", "/static/missing.js", "/audio/missing.wav"):
                request = urllib.request.Request(base + path, method="HEAD")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=5.0)
                self.assertEqual(raised.exception.code, 404)

            error_cases = (
                ("/api/load", {}, "missing_text_or_book_id"),
                ("/api/import", {}, "missing_file_data"),
                ("/api/import", {"filename": "bad.txt", "data_b64": "%%%"}, "invalid_base64"),
                ("/api/jump", {"index": "bad"}, "invalid_request"),
                ("/api/not-a-route", {}, "not_found"),
            )
            for path, payload, code in error_cases:
                with self.subTest(path=path, code=code), self.assertRaises(urllib.error.HTTPError) as raised:
                    self._request(base, path, payload)
                body = json.loads(raised.exception.read())
                self.assertEqual(body["error"], code)

            library = server.context.settings.paths.library
            library.mkdir(parents=True, exist_ok=True)
            (library / "book.txt").write_text("Texto de biblioteca", encoding="utf-8")
            self.assertEqual(self._request(base, "/api/load", {"book_id": "book.txt"})[0], 200)
            self.assertEqual(
                self._request(base, "/api/load", {"book_id": "book.txt", "role": "reference"})[0],
                200,
            )
            self.assertEqual(self._request(base, "/api/load", {"path": "book.txt"})[0], 200)
            self.assertEqual(
                self._request(base, "/api/load", {"path": "book.txt", "role": "reference"})[0],
                200,
            )
            encoded = base64.b64encode(b"Referencia importada").decode("ascii")
            self.assertEqual(
                self._request(
                    base,
                    "/api/import",
                    {"filename": "ref.txt", "data_b64": encoded, "role": "reference"},
                )[0],
                200,
            )

            missing_docx = server.context.pdf_root / "missing.docx"
            item = web_server.register_pdf_to_docx_download(
                server.context,
                missing_docx,
                missing_docx.name,
                ConversionResult(True, output_path=str(missing_docx)),
            )
            with self.assertRaises(urllib.error.HTTPError):
                self._request(base, f"/api/tools/pdf-to-docx/download/{item['id']}")

            boundary = "wrong-suffix"
            body = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="sample.txt"\r\n'
                "Content-Type: text/plain\r\n\r\ntext\r\n"
                f"--{boundary}--\r\n"
            ).encode()
            request = urllib.request.Request(
                base + "/api/tools/pdf-to-docx",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=5.0)
            self.assertEqual(raised.exception.code, 400)

            audio_request = urllib.request.Request(
                base + "/api/dialogue/turn?filename=turn.wav&chunk_index=0",
                data=b"RIFF synthetic",
                headers={"Content-Type": "audio/wav"},
                method="POST",
            )
            with urllib.request.urlopen(audio_request, timeout=5.0) as response:
                self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
