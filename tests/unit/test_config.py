from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fusion_reader_v2.config import (
    ConfigurationError,
    create_settings,
    ensure_within,
    environment_copy,
    environment_has,
    environment_value,
    is_loopback_host,
)


class ConfigurationTests(unittest.TestCase):
    def test_environment_compatibility_reads_are_centralized(self) -> None:
        with mock.patch.dict("os.environ", {"FUSION_TEST_VALUE": "configured"}, clear=True):
            self.assertEqual(environment_value("FUSION_TEST_VALUE"), "configured")
            self.assertEqual(environment_value("MISSING", "fallback"), "fallback")
            self.assertTrue(environment_has("FUSION_TEST_VALUE"))
            self.assertFalse(environment_has("MISSING"))
            self.assertEqual(environment_copy(), {"FUSION_TEST_VALUE": "configured"})

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

    def test_remote_bind_is_postponed_and_always_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            create_settings(environ={"FUSION_READER_BIND_HOST": "0.0.0.0"})
        with self.assertRaises(ConfigurationError):
            create_settings(
                environ={
                    "FUSION_READER_BIND_HOST": "0.0.0.0",
                    "FUSION_READER_ALLOW_REMOTE": "1",
                }
            )
        with self.assertRaises(ConfigurationError):
            create_settings(
                environ={
                    "FUSION_READER_BIND_HOST": "0.0.0.0",
                    "FUSION_READER_ALLOW_REMOTE": "1",
                    "FUSION_READER_API_TOKEN": "test-token",
                }
            )

    def test_path_boundary_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(ensure_within(root / "child", root), (root / "child").resolve())
            with self.assertRaises(ConfigurationError):
                ensure_within(root.parent / "outside", root)
            with self.assertRaises(ConfigurationError):
                ensure_within(root / "child")

    def test_loopback_and_numeric_validation_matrix(self) -> None:
        for host, expected in (
            ("localhost", True),
            ("127.0.0.1", True),
            ("::1", True),
            ("0.0.0.0", False),
            ("bad", False),
        ):
            self.assertEqual(is_loopback_host(host), expected)
        for name, value in (
            ("FUSION_READER_V2_PORT", "bad"),
            ("FUSION_READER_V2_PORT", "0"),
            ("FUSION_READER_TTS_TIMEOUT", "bad"),
            ("FUSION_READER_TTS_TIMEOUT", "0"),
        ):
            with self.subTest(name=name, value=value), self.assertRaises(ConfigurationError):
                create_settings(environ={name: value})

    def test_provider_port_and_path_validation_matrix(self) -> None:
        cases = (
            {"FUSION_READER_GPU_TTS_PORT": "9000"},
            {"FUSION_READER_CPU_TTS_PORT": "9001"},
            {"FUSION_READER_ALLTALK_URL": "http://127.0.0.1:9999"},
            {"FUSION_READER_EXTERNAL_RESEARCH_PROVIDER": "invalid"},
            {"FUSION_READER_STT_PROVIDER": "invalid"},
        )
        for env in cases:
            with self.subTest(env=env), self.assertRaises(ConfigurationError):
                create_settings(environ=env)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ConfigurationError):
                create_settings(
                    repository_root=root,
                    environ={
                        "FUSION_READER_RUNTIME_ROOT": str(root / "runtime"),
                        "FUSION_READER_CACHE_ROOT": str(root / "outside-cache"),
                    },
                )

    def test_download_default_prefers_existing_localized_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            downloads = home / "Downloads"
            downloads.mkdir()
            settings = create_settings(environ={"HOME": str(home)})
            self.assertEqual(settings.paths.downloads, downloads.resolve())
            (home / "Descargas").mkdir()
            settings = create_settings(environ={"HOME": str(home)})
            self.assertEqual(settings.paths.downloads, (home / "Descargas").resolve())


if __name__ == "__main__":
    unittest.main()
