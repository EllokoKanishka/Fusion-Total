from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from fusion_reader_v2.documents import ImportedDocument
from fusion_reader_v2.pdf_to_docx import JobStatus
from fusion_reader_v2.web import jobs, server as web_server
from fusion_reader_v2.web.routes import reading
from tests.integration import test_web_server as support


class ImportLifecycleTests(unittest.TestCase):
    _settings = support.WebServerIntegrationTests._settings
    _start = support.WebServerIntegrationTests._start
    _request = support.WebServerIntegrationTests._request

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.server = self._start(self.root)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._request(self.base, "/api/load", {"doc_id": "initial", "text": "Texto inicial"})

    def raw_import(self, filename="old.txt", *, background=True, role="main"):
        path = "/api/import-file/start" if background else "/api/import-file"
        request = urllib.request.Request(
            f"{self.base}{path}?filename={filename}&mime=text/plain&role={role}",
            data=b"uploaded text",
            headers={"Content-Type": "text/plain"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def blocked_converter(self, module=jobs, name="import_document_path"):
        entered, release = threading.Event(), threading.Event()

        def convert(filename, *_args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("converter fixture was not released")
            callback = kwargs.get("progress")
            if callback:
                callback("extract", 1, 1, "Extracted")
            return ImportedDocument(Path(filename).stem, filename, "Texto convertido", "txt")

        mock = self.enterContext(patch.object(module, name, side_effect=convert))
        self.addCleanup(release.set)
        return entered, release, mock

    def wait_terminal(self, job_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            _, job = self._request(self.base, f"/api/import-status?id={job_id}")
            if job["status"] in {"done", "cancelled", "error"}:
                # Worker cleanup follows publication of terminal state.
                for thread in list(self.server.context._threads):
                    if thread.name == f"fusion-import-{job_id}":
                        thread.join(2)
                return job
            threading.Event().wait(0.01)
        self.fail("import did not become terminal")

    def assert_no_uploads(self):
        self.assertEqual(list(self.server.context.upload_root.glob("*")), [])

    def test_document_load_invalidates_slow_import(self):
        entered, release, _ = self.blocked_converter()
        _, job = self.raw_import()
        self.assertTrue(entered.wait(2))
        self._request(self.base, "/api/load", {"doc_id": "new", "text": "Nuevo documento"})
        release.set()
        finished = self.wait_terminal(job["job_id"])
        self.assertEqual(finished["status"], "cancelled")
        self.assertEqual(self.server.context.app.session.document.doc_id, "new")
        self.assertFalse((self.server.context.converted_root / "old.txt").exists())
        self.assert_no_uploads()

    def test_quick_text_clear_and_promotion_invalidate_slow_import(self):
        for action in ("quick", "clear", "promote"):
            with self.subTest(action=action):
                entered, release, _ = self.blocked_converter()
                _, job = self.raw_import()
                self.assertTrue(entered.wait(2))
                if action == "quick":
                    _, result = self._request(self.base, "/api/quick-text", {"text": "Texto efímero"})
                elif action == "clear":
                    _, result = self._request(self.base, "/api/document/clear", {})
                else:
                    self._request(self.base, "/api/load", {"doc_id": "ref", "text": "Consulta", "role": "reference"})
                    _, result = self._request(self.base, "/api/reference/promote", {"doc_id": "ref"})
                expected = result.get("doc_id")
                release.set()
                self.assertEqual(self.wait_terminal(job["job_id"])["status"], "cancelled")
                self.assertEqual(self.server.context.app.status().get("doc_id"), expected)
                self.assert_no_uploads()

    def test_newer_request_supersedes_old_before_new_finishes(self):
        entered, release, _ = self.blocked_converter()
        _, old = self.raw_import()
        self.assertTrue(entered.wait(2))
        # Hold the newer worker in its queue while the older conversion finishes.
        with patch.object(self.server.context, "start_thread"):
            _, newer = self.raw_import("newer.txt")
        release.set()
        self.assertEqual(self.wait_terminal(old["job_id"])["status"], "cancelled")
        self.assertEqual(self.server.context.app.session.document.doc_id, "initial")
        # Complete the accepted newer job using its own upload.
        uploads = list(self.server.context.upload_root.glob("*"))
        self.assertEqual(len(uploads), 1)
        jobs.import_job_worker(self.server.context, newer["job_id"], "newer.txt", uploads[0], "text/plain")
        self.assertEqual(self.wait_terminal(newer["job_id"])["status"], "done")
        self.assertEqual(self.server.context.app.session.document.doc_id, "newer")
        self.assert_no_uploads()

    def test_cancel_prevents_late_progress_and_publication(self):
        entered, release, _ = self.blocked_converter()
        _, job = self.raw_import()
        self.assertTrue(entered.wait(2))
        code, cancelled = self._request(self.base, f"/api/import-cancel/{job['job_id']}", {})
        self.assertEqual(code, 200)
        self.assertIn(cancelled["status"], {"canceling", "cancelled"})
        release.set()
        finished = self.wait_terminal(job["job_id"])
        self.assertEqual(finished["status"], "cancelled")
        self.assertIsNone(finished["result"])
        self.assertEqual(self.server.context.app.session.document.doc_id, "initial")
        self.assert_no_uploads()

    def test_queued_cancel_skips_conversion(self):
        path = self.root / "queued.upload"
        path.write_bytes(b"test")
        job = jobs.new_import_job(self.server.context, "queued.txt", "text/plain", path, 4)
        self.server.context.import_jobs.update(job["job_id"], lambda item: item.update(cancelled=True))
        with patch.object(jobs, "import_document_path") as convert:
            jobs.import_job_worker(self.server.context, job["job_id"], "queued.txt", path, "text/plain")
        convert.assert_not_called()
        self.assertEqual(jobs.get_import_job(self.server.context, job["job_id"])["status"], "cancelled")
        self.assertFalse(path.exists())

    def test_shutdown_prevents_late_publication(self):
        entered, release, _ = self.blocked_converter()
        _, job = self.raw_import()
        self.assertTrue(entered.wait(2))
        self.server.context.shutdown_jobs(timeout=0)
        release.set()
        self.assertEqual(self.wait_terminal(job["job_id"])["status"], "cancelled")
        self.assertEqual(self.server.context.app.session.document.doc_id, "initial")
        self.assert_no_uploads()

    def test_reference_imports_do_not_supersede_each_other(self):
        entered, release, _ = self.blocked_converter()
        _, first = self.raw_import("ref1.txt", role="reference")
        self.assertTrue(entered.wait(2))
        _, second = self.raw_import("ref2.txt", role="reference")
        release.set()
        for job in (first, second):
            self.assertEqual(self.wait_terminal(job["job_id"])["status"], "done")
        self.assertEqual(self.server.context.app.session.document.doc_id, "initial")
        self.assertEqual(
            {item["doc_id"] for item in self.server.context.app.list_reference_documents()["items"]}, {"ref1", "ref2"}
        )

    def test_registry_full_removes_upload_and_keeps_active_request(self):
        entered, release, _ = self.blocked_converter()
        self.server.context.import_jobs.max_items = 1
        _, active = self.raw_import()
        self.assertTrue(entered.wait(2))
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.raw_import("rejected.txt")
        self.assertEqual(raised.exception.code, 503)
        self.assertEqual(len(list(self.server.context.upload_root.glob("*"))), 1)
        release.set()
        self.assertEqual(self.wait_terminal(active["job_id"])["status"], "done")
        self.assert_no_uploads()

    def test_worker_start_failure_removes_upload_and_finishes_job(self):
        with patch.object(self.server.context, "start_thread", side_effect=RuntimeError("thread unavailable")):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.raw_import()
        self.assertEqual(raised.exception.code, 500)
        self.assert_no_uploads()
        self.assertEqual([job["status"] for job in self.server.context.import_jobs.snapshot().values()], ["error"])

    def test_failed_new_worker_does_not_supersede_running_import(self):
        entered, release, _ = self.blocked_converter()
        _, old = self.raw_import()
        self.assertTrue(entered.wait(2))
        with patch.object(self.server.context, "start_thread", side_effect=RuntimeError("thread unavailable")):
            with self.assertRaises(urllib.error.HTTPError):
                self.raw_import("failed.txt")
        release.set()
        self.assertEqual(self.wait_terminal(old["job_id"])["status"], "done")
        self.assertEqual(self.server.context.app.session.document.doc_id, "old")
        self.assert_no_uploads()

    def test_document_change_during_upload_invalidates_import(self):
        entered, release = threading.Event(), threading.Event()
        original = web_server.Handler._read_body_to_temp

        def read_body(handler, filename):
            entered.set()
            if not release.wait(5):
                raise AssertionError("upload fixture was not released")
            return original(handler, filename)

        outcome = []
        with patch.object(web_server.Handler, "_read_body_to_temp", read_body):
            thread = threading.Thread(target=lambda: outcome.append(self.raw_import()[1]))
            thread.start()
            self.addCleanup(thread.join, 3)
            self.addCleanup(release.set)
            self.assertTrue(entered.wait(2))
            self._request(self.base, "/api/load", {"doc_id": "new", "text": "Nuevo documento"})
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertEqual(self.wait_terminal(outcome[0]["job_id"])["status"], "cancelled")
        self.assertEqual(self.server.context.app.session.document.doc_id, "new")
        self.assert_no_uploads()

    def test_publication_and_direct_document_change_are_serialized(self):
        entered, release, changing, changed = (threading.Event() for _ in range(4))
        original = jobs._load_imported_document

        def publish(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("publication fixture was not released")
            return original(*args, **kwargs)

        def change_document():
            changing.set()
            self.server.context.app.load_text("new", "Nuevo", "Texto nuevo", prefetch=False)
            changed.set()

        with patch.object(jobs, "_load_imported_document", side_effect=publish):
            _, job = self.raw_import()
            self.assertTrue(entered.wait(2))
            thread = threading.Thread(target=change_document)
            thread.start()
            self.addCleanup(thread.join, 3)
            self.addCleanup(release.set)
            self.assertTrue(changing.wait(2))
            self.assertFalse(changed.wait(0.05), "document change must wait for publication")
            release.set()
            thread.join(3)
            self.assertTrue(changed.is_set())
            self.assertEqual(self.wait_terminal(job["job_id"])["status"], "done")
        self.assertEqual(self.server.context.app.session.document.doc_id, "new")
        self.assert_no_uploads()

    def test_thread_start_failure_does_not_leave_unstarted_owner(self):
        with patch(
            "fusion_reader_v2.web.context.threading.Thread.start", side_effect=RuntimeError("thread unavailable")
        ):
            with self.assertRaises(RuntimeError):
                self.server.context.start_thread(target=lambda: None, args=(), name="failed-import-owner")
        self.assertEqual(self.server.context._threads, set())

    def pdf_request(self):
        boundary = "test-import-boundary"
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.pdf"\r\n'
                "Content-Type: application/pdf\r\n\r\n"
            ).encode()
            + b"%PDF-test\r\n"
            + f"--{boundary}--\r\n".encode()
        )
        request = urllib.request.Request(
            self.base + "/api/tools/pdf-to-docx",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_pdf_full_registry_removes_input(self):
        self.server.context.pdf_jobs.max_items = 1
        self.server.context.pdf_jobs.add("active", JobStatus(job_id="active", filename="active.pdf"))
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.pdf_request()
        self.assertEqual(raised.exception.code, 503)
        self.assert_no_uploads()
        self.assertEqual(list(self.server.context.pdf_root.glob("*.pdf")), [])

    def test_pdf_start_failure_removes_input_and_finishes_job(self):
        with patch.object(self.server.context, "start_thread", side_effect=RuntimeError("thread unavailable")):
            with self.assertRaises(urllib.error.HTTPError):
                self.pdf_request()
        self.assert_no_uploads()
        self.assertEqual(list(self.server.context.pdf_root.glob("*.pdf")), [])
        self.assertEqual([job.state for job in self.server.context.pdf_jobs.snapshot().values()], ["error"])

    def test_invalid_get_parameters_receive_json_and_server_remains_usable(self):
        for path in ("/api/notes?chunk_index=oops", "/api/voice/metrics/chunks?limit=oops"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as raised:
                self._request(self.base, path)
            response = raised.exception
            payload = json.loads(response.read())
            self.assertEqual(response.code, 400)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["request_id"], response.headers["X-Request-ID"])
        self.assertEqual(self._request(self.base, "/api/notes")[0], 200)

    def test_head_failure_has_json_headers_and_no_body(self):
        with patch.object(self.server.context, "status", side_effect=ValueError("bad_status")):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self._request(self.base, "/api/status", method="HEAD")
        self.assertEqual(raised.exception.code, 400)
        self.assertIn("application/json", raised.exception.headers["Content-Type"])
        self.assertEqual(raised.exception.read(), b"")

    def test_unexpected_get_error_returns_sanitized_500(self):
        with patch.object(self.server.context.app, "list_notes", side_effect=RuntimeError("private-fixture-detail")):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self._request(self.base, "/api/notes")
        payload = json.loads(raised.exception.read())
        self.assertEqual(raised.exception.code, 500)
        self.assertEqual(payload["error"], "internal_server_error")
        self.assertNotIn("private-fixture-detail", str(payload))

    def _synchronous_import_race(self, *, encoded):
        module, name = (reading, "import_document_bytes") if encoded else (web_server, "import_document_path")
        entered, release, _ = self.blocked_converter(module, name)
        outcome = []

        def request():
            try:
                if encoded:
                    self._request(
                        self.base,
                        "/api/import",
                        {"filename": "old.txt", "data_b64": base64.b64encode(b"test").decode()},
                    )
                else:
                    self.raw_import(background=False)
                outcome.append(200)
            except urllib.error.HTTPError as exc:
                outcome.append(exc.code)

        thread = threading.Thread(target=request)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2))
        self._request(self.base, "/api/load", {"doc_id": "new", "text": "Documento nuevo"})
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, [409])
        self.assertEqual(self.server.context.app.session.document.doc_id, "new")
        self.assert_no_uploads()

    def test_synchronous_raw_import_cannot_replace_new_document(self):
        self._synchronous_import_race(encoded=False)

    def test_synchronous_base64_import_cannot_replace_new_document(self):
        self._synchronous_import_race(encoded=True)
