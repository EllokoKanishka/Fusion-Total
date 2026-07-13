from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from fusion_reader_v2 import tts


class Response:
    def __init__(self, raw: bytes, status: int = 200) -> None:
        self.raw = raw
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.raw


class AllTalkProviderMatrixTests(unittest.TestCase):
    def _provider(self, root: Path, url: str = "http://127.0.0.1:7851") -> tts.AllTalkProvider:
        provider = tts.AllTalkProvider(base_url=url, owner_file=root / "owner.json", timeout_seconds=1)
        provider.require_owner = False
        return provider

    def test_environment_helpers_base_contract_and_local_port(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "FUSION_READER_GPU_TTS_PORT": "bad",
                "LUCY_TTS_PORT": "bad",
                "FUSION_READER_CPU_TTS_PORT": "bad",
            },
        ):
            self.assertEqual(tts._configured_gpu_tts_port(), 7853)
            self.assertEqual(tts._configured_lucy_tts_port(), 7854)
            self.assertEqual(tts._configured_cpu_tts_port(), 7851)
        self.assertEqual(tts._historic_unassigned_tts_port(), 7852)
        self.assertFalse(tts._truthy("off"))
        self.assertTrue(tts._truthy(None))
        self.assertFalse(tts.TTSProvider().health()["ok"])
        self.assertEqual(tts.TTSProvider().voices(), [])
        self.assertFalse(tts.TTSProvider().synthesize("x").ok)
        with tempfile.TemporaryDirectory() as tmp:
            provider = self._provider(Path(tmp))
            self.assertEqual(provider._local_port(), 7851)
            self.assertIsNone(provider._local_port("https://example.test:443"))
            self.assertIsNone(provider._local_port("http://127.0.0.1:bad"))

    def test_listener_detection_and_owner_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = self._provider(root)
            outputs = (
                subprocess.CompletedProcess([], 0, stdout="not-a-pid\n", stderr=""),
                subprocess.CompletedProcess(
                    [], 0, stdout='LISTEN 0 1 127.0.0.1:7853 users:(("python",pid=4321,fd=1))', stderr=""
                ),
            )
            with mock.patch.object(tts, "run_owned", side_effect=outputs):
                self.assertEqual(provider._listening_pid(7853), 4321)
            with mock.patch.object(tts, "run_owned", side_effect=OSError("missing")):
                self.assertIsNone(provider._listening_pid(7853))
            with mock.patch.object(provider, "_cmdline_for_pid", return_value="python -m tts_server:app --port 7853"):
                self.assertTrue(provider._listener_matches_fusion_tts(1, 7853))
            with mock.patch.object(provider, "_cmdline_for_pid", side_effect=PermissionError("denied")):
                self.assertFalse(provider._listener_matches_fusion_tts(1, 7853))

            provider._rewrite_owner_pid({"owner": "fusion_reader_v2", "port": 7853}, 123)
            self.assertEqual(json.loads(provider.owner_file.read_text())["owner_pid"], 123)
            with mock.patch.object(tts.os, "replace", side_effect=PermissionError("denied")):
                provider._rewrite_owner_pid({}, 5)
            self.assertEqual(list(root.glob("*.tmp")), [])

            with mock.patch.object(provider, "_listening_pid", return_value=None):
                self.assertIsNone(provider._reconcile_owner_pid({}, 7853))
            with (
                mock.patch.object(provider, "_listening_pid", return_value=99),
                mock.patch.object(provider, "_listener_matches_fusion_tts", return_value=False),
            ):
                self.assertIsNone(provider._reconcile_owner_pid({}, 7853))
            with (
                mock.patch.object(provider, "_listening_pid", return_value=99),
                mock.patch.object(provider, "_listener_matches_fusion_tts", return_value=True),
                mock.patch.object(provider, "_rewrite_owner_pid") as rewrite,
            ):
                self.assertEqual(provider._reconcile_owner_pid({}, 7853), 99)
                rewrite.assert_called_once()

    def test_owner_guard_metadata_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = self._provider(root, "http://127.0.0.1:7853")
            provider.require_owner = True
            self.assertIn("doctora", provider._owner_guard("http://127.0.0.1:7854")[1])
            self.assertIn("historic", provider._owner_guard("http://127.0.0.1:7852")[1])
            self.assertIn("missing", provider._owner_guard()[1])

            provider.owner_file.write_text("bad", encoding="utf-8")
            self.assertIn("invalid", provider._owner_guard()[1])
            cases = (
                ({"owner": "other", "port": 7853, "owner_pid": 1}, "mismatch"),
                ({"owner": "fusion_reader_v2", "port": "bad", "owner_pid": 1}, "port_invalid"),
                ({"owner": "fusion_reader_v2", "port": 7851, "owner_pid": 1}, "port_mismatch"),
            )
            for payload, marker in cases:
                provider.owner_file.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIn(marker, provider._owner_guard()[1])

            base = {"owner": "fusion_reader_v2", "port": 7853}
            provider.owner_file.write_text(json.dumps(base), encoding="utf-8")
            with mock.patch.object(provider, "_reconcile_owner_pid", return_value=None):
                self.assertIn("pid_missing", provider._owner_guard()[1])
            with (
                mock.patch.object(provider, "_reconcile_owner_pid", return_value=77),
                mock.patch.object(provider, "_cmdline_for_pid", return_value="tts_server:app --port 7853"),
            ):
                self.assertTrue(provider._owner_guard()[0])

            payload = {**base, "owner_pid": 55}
            provider.owner_file.write_text(json.dumps(payload), encoding="utf-8")
            for error, marker in ((FileNotFoundError(), "stale"), (PermissionError("no"), "unreadable")):
                with (
                    mock.patch.object(provider, "_cmdline_for_pid", side_effect=error),
                    mock.patch.object(provider, "_reconcile_owner_pid", return_value=None),
                ):
                    self.assertIn(marker, provider._owner_guard()[1])
            with (
                mock.patch.object(provider, "_cmdline_for_pid", return_value="other"),
                mock.patch.object(provider, "_reconcile_owner_pid", return_value=None),
            ):
                self.assertIn("pid_mismatch", provider._owner_guard()[1])
            with mock.patch.object(provider, "_cmdline_for_pid", return_value="tts_server:app --port 7853"):
                self.assertTrue(provider._owner_guard()[0])

    def test_ready_preferred_text_json_health_and_voices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = self._provider(Path(tmp))
            with mock.patch.object(tts.urllib.request, "urlopen", return_value=Response(b"ready")):
                self.assertTrue(provider._gpu_service_ready())
            with mock.patch.object(tts.urllib.request, "urlopen", side_effect=OSError("down")):
                self.assertFalse(provider._gpu_service_ready())
            self.assertEqual(provider._preferred_base_url("http://example.test:9000"), "http://example.test:9000")
            with (
                mock.patch.object(provider, "_owner_guard", return_value=(True, "")),
                mock.patch.object(provider, "_gpu_service_ready", return_value=True),
            ):
                self.assertEqual(provider._preferred_base_url("http://127.0.0.1:7851"), "http://127.0.0.1:7853")

            provider.max_input_chars = 90
            prepared = provider._prepare_text("[Pagina 2] " + "palabra " * 30)
            self.assertIn("Página 2", prepared)
            self.assertLessEqual(len(prepared), 90)
            self.assertEqual(provider._prepare_text("\x00"), "")

            with mock.patch.object(tts.urllib.request, "urlopen", return_value=Response(b'{"voices":["a"]}')):
                self.assertEqual(provider._request_json("http://x"), {"voices": ["a"]})
            with mock.patch.object(tts.urllib.request, "urlopen", return_value=Response(b"raw")):
                self.assertEqual(provider._request_json("http://x"), {"raw": "raw"})

            with mock.patch.object(provider, "_owner_guard", return_value=(False, "owner")):
                self.assertFalse(provider.health()["ok"])
                self.assertEqual(provider.voices(), [])
            with (
                mock.patch.object(provider, "_owner_guard", return_value=(True, "")),
                mock.patch.object(tts.urllib.request, "urlopen", return_value=Response(b"ready")),
            ):
                self.assertTrue(provider.health()["ok"])
            with (
                mock.patch.object(provider, "_owner_guard", return_value=(True, "")),
                mock.patch.object(tts.urllib.request, "urlopen", side_effect=socket.timeout("busy")),
            ):
                self.assertTrue(provider.health()["ok"])
            with (
                mock.patch.object(provider, "_owner_guard", return_value=(True, "")),
                mock.patch.object(provider, "_request_json", return_value={"voices": ["a", "", 2]}),
            ):
                self.assertEqual(provider.voices(), ["a", "2"])
            with (
                mock.patch.object(provider, "_owner_guard", return_value=(True, "")),
                mock.patch.object(provider, "_request_json", side_effect=OSError("down")),
            ):
                self.assertEqual(provider.voices(), [])

    def test_synthesize_success_and_error_matrix_and_audio_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = self._provider(Path(tmp))
            with mock.patch.object(provider, "_owner_guard", return_value=(False, "owner")):
                self.assertEqual(provider.synthesize("text").detail, "owner")
            with mock.patch.object(provider, "_owner_guard", return_value=(True, "")):
                self.assertEqual(provider.synthesize("\x00").detail, "empty_tts_text")

            responses = [Response(b'{"output_file_url":"/audio/out.wav"}'), Response(b"RIFFdata")]
            with (
                mock.patch.object(provider, "_owner_guard", return_value=(True, "")),
                mock.patch.object(tts.urllib.request, "urlopen", side_effect=responses),
            ):
                artifact = provider.synthesize("texto", language="")
            self.assertTrue(artifact.ok)
            self.assertEqual(artifact.path.read_bytes(), b"RIFFdata")
            artifact.path.unlink()

            with (
                mock.patch.object(provider, "_owner_guard", return_value=(True, "")),
                mock.patch.object(tts.urllib.request, "urlopen", return_value=Response(b"{}")),
            ):
                self.assertEqual(provider.synthesize("texto").detail, "no_audio_url")
            error = urllib.error.HTTPError("http://x", 413, "large", {}, None)
            with (
                mock.patch.object(provider, "_owner_guard", return_value=(True, "")),
                mock.patch.object(tts.urllib.request, "urlopen", side_effect=error),
            ):
                self.assertEqual(provider.synthesize("texto").detail, "http_413")
            with (
                mock.patch.object(provider, "_owner_guard", return_value=(True, "")),
                mock.patch.object(tts.urllib.request, "urlopen", side_effect=OSError("down")),
            ):
                self.assertIn("down", provider.synthesize("texto").detail)

            self.assertEqual(provider._audio_url("audio/a.wav"), f"{provider.base_url}/audio/a.wav")
            self.assertEqual(
                provider._audio_url("http://localhost:9999/audio/a.wav"),
                f"{provider.base_url}/audio/a.wav",
            )
            self.assertEqual(provider._audio_url("https://remote.test/a.wav"), "https://remote.test/a.wav")


if __name__ == "__main__":
    unittest.main()
