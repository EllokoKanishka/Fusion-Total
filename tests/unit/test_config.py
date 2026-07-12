from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fusion_reader_v2.config import ConfigurationError, create_settings, ensure_within


class ConfigurationTests(unittest.TestCase):
    def test_settings_use_injected_roots_without_creating_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "not-created-runtime"
            settings = create_settings(
                repository_root=root,
                environ={
                    "HOME": str(root / "home"),
                    "FUSION_READER_RUNTIME_ROOT": str(runtime),
                    "FUSION_READER_LIBRARY_ROOT": str(root / "library"),
                    "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
                },
            )
            self.assertEqual(settings.paths.runtime, runtime.resolve())
            self.assertEqual(settings.paths.cache, (runtime / "audio_cache").resolve())
            self.assertFalse(runtime.exists())

    def test_reserved_tts_ports_are_rejected(self) -> None:
        for port in (7852, 7854):
            with self.subTest(port=port), self.assertRaises(ConfigurationError):
                create_settings(environ={"FUSION_READER_GPU_TTS_PORT": str(port)})

    def test_reserved_tts_url_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            create_settings(environ={"FUSION_READER_ALLTALK_URL": "http://127.0.0.1:7854"})

    def test_remote_bind_requires_opt_in_and_token(self) -> None:
        with self.assertRaises(ConfigurationError):
            create_settings(environ={"FUSION_READER_BIND_HOST": "0.0.0.0"})
        with self.assertRaises(ConfigurationError):
            create_settings(
                environ={
                    "FUSION_READER_BIND_HOST": "0.0.0.0",
                    "FUSION_READER_ALLOW_REMOTE": "1",
                }
            )
        settings = create_settings(
            environ={
                "FUSION_READER_BIND_HOST": "0.0.0.0",
                "FUSION_READER_ALLOW_REMOTE": "1",
                "FUSION_READER_API_TOKEN": "test-token",
            }
        )
        self.assertTrue(settings.security.allow_remote)

    def test_path_boundary_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(ensure_within(root / "child", root), (root / "child").resolve())
            with self.assertRaises(ConfigurationError):
                ensure_within(root.parent / "outside", root)


if __name__ == "__main__":
    unittest.main()
