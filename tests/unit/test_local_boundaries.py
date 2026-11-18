from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from email.message import Message
import io
import tempfile
import unittest

from fusion_reader_v2.audio_export import unique_audio_download_target
from fusion_reader_v2.output_validation import stream_file
from fusion_reader_v2.web.server import Handler
from fusion_reader_v2.web.errors import APIError
from scripts import fusion_memory_mcp_server as memory
from scripts.repository_hygiene import audit


class LocalBoundariesTests(unittest.TestCase):
    def handler(self, host="127.0.0.1:8123", origin=None):
        handler = object.__new__(Handler)
        handler.headers = Message()
        handler.headers["Host"] = host
        if origin:
            handler.headers["Origin"] = origin
        handler.server = SimpleNamespace(server_address=("127.0.0.1", 8123))
        handler.command = "POST"
        return handler

    def test_foreign_host_origin_and_duplicate_host_rejected_before_body(self):
        for host, origin in [
            ("external.example:8123", None),
            ("127.0.0.1:8123", "https://foreign.example"),
            ("127.0.0.1:8123", "null"),
            ("localhost:8123", "http://localhost:9999"),
        ]:
            with self.assertRaises(APIError):
                self.handler(host, origin)._check_local_request()
        handler = self.handler()
        handler.headers["Host"] = "external.example"
        with self.assertRaises(APIError):
            handler._check_local_request()
        self.handler(origin="http://localhost:8123")._check_local_request()
        self.handler()._check_local_request()

    def test_real_http_rejects_foreign_origin_and_plain_json_before_dispatch(self):
        import json
        import threading
        import urllib.request
        import urllib.error
        from dataclasses import replace
        from fusion_reader_v2.config import create_settings
        from fusion_reader_v2.web.server import create_http_server
        from tests.helpers import managed_test_app

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = create_settings(environ={"HOME": folder}, repository_root=root)
            settings = replace(settings, ports=replace(settings.ports, api=0))
            with managed_test_app(root=root) as app:
                server = create_http_server(app, settings)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{server.server_address[1]}/api/runtime/client"
                    for headers, expected in [
                        ({"Content-Type": "application/json", "Origin": "https://foreign.example"}, 403),
                        ({"Content-Type": "text/plain"}, 400),
                        ({"Content-Type": "application/json; charset=utf-8"}, 200),
                    ]:
                        request = urllib.request.Request(url, data=b"{}", headers=headers)
                        try:
                            with urllib.request.urlopen(request, timeout=3) as response:
                                status = response.status
                                json.load(response)
                        except urllib.error.HTTPError as error:
                            status = error.code
                            json.load(error)
                        self.assertEqual(status, expected)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(3)

    def test_memory_symlink_to_sibling_prefix_is_neither_read_nor_searchable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "memory"
            root.mkdir()
            sibling = Path(folder) / "memory-other"
            sibling.mkdir()
            (sibling / "project_state.md").write_text("private-fixture-needle")
            (root / "project_state.md").symlink_to(sibling / "project_state.md")
            with patch.object(memory, "MEMORY_DIR", root):
                self.assertIsNone(memory.safe_memory_path("project_state.md"))
                self.assertNotIn("private-fixture-needle", memory.read_memory_file("project_state.md"))
                self.assertEqual(memory.search_memory("private-fixture-needle"), [])

    def test_dot_filename_stays_inside_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "downloads"
            target = unique_audio_download_target(".", root)
            self.assertEqual(target.parent.resolve(), root.resolve())

    def test_unicode_download_headers_are_ascii_with_utf8_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.wav"
            path.write_bytes(b"fixture")
            headers = {}
            handler = SimpleNamespace(
                send_response=Mock(),
                send_header=lambda key, value: headers.update({key: value}),
                end_headers=Mock(),
                wfile=io.BytesIO(),
            )
            stream_file(handler, path, content_type="audio/wav", filename="filosofía — 哲学.wav")
            headers["Content-Disposition"].encode("latin1")
            self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])

    def test_reused_branch_with_new_commit_is_not_a_candidate_and_audit_is_read_only(self):
        requested = []

        def request(url):
            requested.append(url)
            if "/pulls?" in url:
                return []
            if "/branches?" in url:
                return [{"name": "reused", "commit": {"sha": "new-head"}, "protected": False}]
            self.assertTrue(url.endswith("main...new-head"))
            return {"status": "ahead", "ahead_by": 1}

        result = audit(request, "fixture/project")
        self.assertEqual(result["deleted_count"], 0)
        self.assertFalse(result["branches"][0]["contained_candidate"])
