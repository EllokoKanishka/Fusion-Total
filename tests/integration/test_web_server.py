from __future__ import annotations

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

from fusion_reader_v2.config import create_settings
from fusion_reader_v2.web import server as web_server
from tests.helpers import managed_test_app


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

    def _start(self, root: Path, *, remote: bool = False):
        app_context = managed_test_app(root=root / "app")
        app = app_context.__enter__()
        server = web_server.create_http_server(app, self._settings(root, remote=remote))
        thread = threading.Thread(target=server.serve_forever, name="fusion-test-http")
        thread.start()
        self.addCleanup(app_context.__exit__, None, None, None)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3.0)
        self.addCleanup(server.shutdown)
        return server

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
            for key in ("version", "commit", "pid", "uptime_seconds", "state_schema", "cache", "jobs", "providers", "ports", "warnings", "degradations"):
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


if __name__ == "__main__":
    unittest.main()
