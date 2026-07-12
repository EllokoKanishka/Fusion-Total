import unittest
import os
import json
import tempfile
import time
from pathlib import Path
from unittest import mock
from concurrent.futures import Future
from fusion_reader_v2 import AllTalkProvider
from tests.helpers import (
    test_app,
    NullTTSProvider,
)


class TTSTests(unittest.TestCase):
    def test_voice_cache_reuses_audio(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        first = app.test_voice("Hola mundo")
        second = app.test_voice("Hola mundo")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(provider.calls), 1)

    def test_default_voice_is_lucy_female_03_and_can_be_overridden(self):
        previous = os.environ.get("FUSION_READER_VOICE")
        try:
            os.environ.pop("FUSION_READER_VOICE", None)
            self.assertEqual(test_app().voice.voice, "female_03.wav")
            self.assertEqual(AllTalkProvider(base_url="http://example.invalid").default_voice, "female_03.wav")
            os.environ["FUSION_READER_VOICE"] = "female_06.wav"
            self.assertEqual(test_app().voice.voice, "female_06.wav")
            self.assertEqual(AllTalkProvider(base_url="http://example.invalid").default_voice, "female_06.wav")
        finally:
            if previous is None:
                os.environ.pop("FUSION_READER_VOICE", None)
            else:
                os.environ["FUSION_READER_VOICE"] = previous

    def test_alltalk_audio_url_uses_configured_local_port(self):
        provider = AllTalkProvider(base_url="http://127.0.0.1:7899")
        self.assertEqual(
            provider._audio_url("http://127.0.0.1:7851/outputs/fusion.wav"),
            "http://127.0.0.1:7899/outputs/fusion.wav",
        )

    def test_alltalk_default_uses_fusion_gpu_port_and_requires_owner(self):
        keys = [
            "FUSION_READER_ALLTALK_URL",
            "FUSION_READER_GPU_TTS_PORT",
            "FUSION_READER_REQUIRE_TTS_OWNER",
            "FUSION_READER_TTS_OWNER_FILE",
        ]
        previous = {key: os.environ.get(key) for key in keys}
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.environ.pop("FUSION_READER_ALLTALK_URL", None)
                os.environ["FUSION_READER_GPU_TTS_PORT"] = "7853"
                os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "1"
                os.environ["FUSION_READER_TTS_OWNER_FILE"] = str(Path(tmp) / "missing_owner.json")
                provider = AllTalkProvider()
                self.assertEqual(provider.base_url, "http://127.0.0.1:7853")
                health = provider.health()
                self.assertFalse(health["ok"])
                self.assertIn("tts_owner_missing", health["detail"])
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_alltalk_prefers_owned_gpu_url_over_cpu_fallback_env(self):
        keys = [
            "FUSION_READER_ALLTALK_URL",
            "FUSION_READER_GPU_TTS_PORT",
            "FUSION_READER_CPU_TTS_PORT",
            "FUSION_READER_REQUIRE_TTS_OWNER",
            "FUSION_READER_TTS_OWNER_FILE",
        ]
        previous = {key: os.environ.get(key) for key in keys}
        with tempfile.TemporaryDirectory() as tmp:
            owner_file = Path(tmp) / "tts_owner.json"
            owner_file.write_text(
                json.dumps({"owner": "fusion_reader_v2", "port": 7853, "owner_pid": os.getpid()}), encoding="utf-8"
            )
            try:
                os.environ["FUSION_READER_ALLTALK_URL"] = "http://127.0.0.1:7851"
                os.environ["FUSION_READER_GPU_TTS_PORT"] = "7853"
                os.environ["FUSION_READER_CPU_TTS_PORT"] = "7851"
                os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "1"
                os.environ["FUSION_READER_TTS_OWNER_FILE"] = str(owner_file)
                with (
                    mock.patch.object(AllTalkProvider, "_gpu_service_ready", return_value=True),
                    mock.patch.object(AllTalkProvider, "_owner_guard", return_value=(True, "")),
                ):
                    provider = AllTalkProvider()
                self.assertEqual(provider.base_url, "http://127.0.0.1:7853")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_alltalk_keeps_cpu_fallback_when_gpu_not_ready(self):
        keys = [
            "FUSION_READER_ALLTALK_URL",
            "FUSION_READER_GPU_TTS_PORT",
            "FUSION_READER_CPU_TTS_PORT",
            "FUSION_READER_REQUIRE_TTS_OWNER",
        ]
        previous = {key: os.environ.get(key) for key in keys}
        try:
            os.environ["FUSION_READER_ALLTALK_URL"] = "http://127.0.0.1:7851"
            os.environ["FUSION_READER_GPU_TTS_PORT"] = "7853"
            os.environ["FUSION_READER_CPU_TTS_PORT"] = "7851"
            os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "1"
            with (
                mock.patch.object(AllTalkProvider, "_gpu_service_ready", return_value=False),
                mock.patch.object(AllTalkProvider, "_owner_guard", return_value=(False, "tts_owner_missing")),
            ):
                provider = AllTalkProvider()
            self.assertEqual(provider.base_url, "http://127.0.0.1:7851")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_alltalk_rejects_doctora_and_historic_ports_even_when_configured(self):
        keys = ["FUSION_READER_REQUIRE_TTS_OWNER", "LUCY_TTS_PORT"]
        previous = {key: os.environ.get(key) for key in keys}
        try:
            os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "0"
            os.environ["LUCY_TTS_PORT"] = "7854"
            cases = [
                ("http://127.0.0.1:7854", "tts_foreign_doctora_lucy_port"),
                ("http://127.0.0.1:7852", "tts_historic_unassigned_port"),
            ]
            for url, detail in cases:
                provider = AllTalkProvider(base_url=url)
                health = provider.health()
                self.assertFalse(health["ok"])
                self.assertIn(detail, health["detail"])
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_alltalk_recovers_stale_owner_pid_from_live_listener(self):
        keys = ["FUSION_READER_GPU_TTS_PORT", "FUSION_READER_REQUIRE_TTS_OWNER", "FUSION_READER_TTS_OWNER_FILE"]
        previous = {key: os.environ.get(key) for key in keys}
        with tempfile.TemporaryDirectory() as tmp:
            owner_file = Path(tmp) / "tts_owner.json"
            owner_file.write_text(
                json.dumps({"owner": "fusion_reader_v2", "port": 7853, "owner_pid": 999999}), encoding="utf-8"
            )
            try:
                os.environ["FUSION_READER_GPU_TTS_PORT"] = "7853"
                os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "1"
                os.environ["FUSION_READER_TTS_OWNER_FILE"] = str(owner_file)
                provider = AllTalkProvider(base_url="http://127.0.0.1:7853")
                with (
                    mock.patch.object(provider, "_listening_pid", return_value=4321),
                    mock.patch.object(provider, "_listener_matches_fusion_tts", return_value=True),
                    mock.patch.object(provider, "_cmdline_for_pid", side_effect=FileNotFoundError()),
                ):
                    ok, detail = provider._owner_guard()
                self.assertTrue(ok)
                self.assertEqual(detail, "")
                rewritten = json.loads(owner_file.read_text(encoding="utf-8"))
                self.assertEqual(rewritten["owner_pid"], 4321)
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_alltalk_owner_reconciliation_uses_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner_file = Path(tmp) / "tts_owner.json"
            owner_file.write_text(
                json.dumps({"owner": "fusion_reader_v2", "port": 7853, "owner_pid": 1}), encoding="utf-8"
            )
            provider = AllTalkProvider(base_url="http://127.0.0.1:7853")
            provider.owner_file = owner_file
            real_replace = os.replace
            with mock.patch("fusion_reader_v2.tts.os.replace", wraps=real_replace) as replace:
                provider._rewrite_owner_pid({"owner": "fusion_reader_v2", "port": 7853}, 4321)
            replace.assert_called_once()
            self.assertEqual(json.loads(owner_file.read_text(encoding="utf-8"))["owner_pid"], 4321)

    def test_alltalk_valid_listener_survives_metadata_write_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner_file = Path(tmp) / "tts_owner.json"
            owner_file.write_text(
                json.dumps({"owner": "fusion_reader_v2", "port": 7853, "owner_pid": 999999}), encoding="utf-8"
            )
            provider = AllTalkProvider(base_url="http://127.0.0.1:7853")
            provider.owner_file = owner_file
            with (
                mock.patch.object(provider, "_listening_pid", return_value=4321),
                mock.patch.object(provider, "_listener_matches_fusion_tts", return_value=True),
                mock.patch.object(provider, "_cmdline_for_pid", side_effect=FileNotFoundError()),
                mock.patch("fusion_reader_v2.tts.os.replace", side_effect=OSError("read-only")),
            ):
                self.assertEqual(provider._owner_guard(), (True, ""))
            self.assertEqual(json.loads(owner_file.read_text(encoding="utf-8"))["owner_pid"], 999999)

    def test_alltalk_does_not_reconcile_incorrect_listener(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner_file = Path(tmp) / "tts_owner.json"
            owner_file.write_text(
                json.dumps({"owner": "fusion_reader_v2", "port": 7853, "owner_pid": 999999}), encoding="utf-8"
            )
            provider = AllTalkProvider(base_url="http://127.0.0.1:7853")
            provider.owner_file = owner_file
            with (
                mock.patch.object(provider, "_listening_pid", return_value=4321),
                mock.patch.object(provider, "_listener_matches_fusion_tts", return_value=False),
                mock.patch.object(provider, "_cmdline_for_pid", side_effect=FileNotFoundError()),
            ):
                ok, detail = provider._owner_guard()
            self.assertFalse(ok)
            self.assertIn("tts_owner_pid_stale", detail)
            self.assertEqual(json.loads(owner_file.read_text(encoding="utf-8"))["owner_pid"], 999999)

    def test_fusion_launchers_do_not_auto_claim_antigravity_tts_port(self):
        root = Path(__file__).resolve().parents[1]
        launchers = [
            root / "scripts" / "start_fusion_reader_v2.sh",
            root / "scripts" / "open_fusion_reader.sh",
            root / "scripts" / "start_reader_neural_tts_gpu_5090.sh",
        ]
        for launcher in launchers:
            text = launcher.read_text(encoding="utf-8")
            self.assertNotIn("DIRECT_CHAT_ALLTALK_GPU_PORT", text)
            self.assertNotIn("127.0.0.1:7852", text)

    def test_fusion_launchers_require_owned_gpu_tts(self):
        root = Path(__file__).resolve().parents[1]
        for rel in (
            "scripts/start_reader_neural_tts_gpu_5090.sh",
            "scripts/start_fusion_reader_v2.sh",
            "scripts/open_fusion_reader.sh",
        ):
            text = (root / rel).read_text(encoding="utf-8")
            self.assertIn("FUSION_READER_TTS_OWNER_FILE", text)
            self.assertIn('"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"', text)

    def test_fusion_launcher_waits_for_owned_gpu_tts_before_cpu_fallback(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "start_fusion_reader_v2.sh").read_text(encoding="utf-8")
        self.assertIn("FUSION_READER_GPU_TTS_WAIT_SECONDS", text)
        self.assertIn("fusion_gpu_ready()", text)

    def test_fusion_launcher_has_persistent_log_and_pid_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "start_fusion_reader_v2.sh").read_text(encoding="utf-8")
        self.assertIn('RUNTIME_DIR="${FUSION_READER_RUNTIME_DIR:-$ROOT/runtime/fusion_reader_v2}"', text)
        self.assertIn("fusion_reader_v2.pid", text)

    def test_voice_port_isolation_verifier_separates_strict_and_external_sections(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "verify_voice_port_isolation.sh").read_text(encoding="utf-8")
        self.assertIn("FUSION STRICT CHECKS", text)
        self.assertIn("EXTERNAL BOUNDARY / DOCTORA INFO", text)
        self.assertIn("FINAL RESULT", text)
        self.assertIn("FUSION STRICT CHECKS: $(strict_status)", text)
        self.assertIn("EXTERNAL BOUNDARY / DOCTORA INFO: $(external_status)", text)
        self.assertIn("FINAL RESULT: $(final_status)", text)
        self.assertIn("OK_WITH_EXTERNAL_WARNINGS", text)
        self.assertIn("OK_WITH_STRICT_WARNINGS", text)
        self.assertIn('external_warn "missing file: $file"', text)
        self.assertIn('strict_fail "missing file: $file"', text)

    def test_voice_port_verifier_is_read_only_for_owner_metadata(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "verify_voice_port_isolation.sh").read_text(encoding="utf-8")
        self.assertIn("owner_pid metadata is stale; runtime reconciliation is needed", text)
        self.assertNotIn("path.write_text", text)
        self.assertNotIn('data["owner_pid"]', text)
        self.assertNotIn('python3 - "$OWNER_FILE"', text)

    def test_voice_catalog_returns_available_voices(self):
        class VoiceTTS(NullTTSProvider):
            def voices(self):
                return ["voice1.wav", "voice2.wav"]

        app = test_app(tts=VoiceTTS())
        app.voice.voice = "voice1.wav"
        catalog = app.get_voice_catalog()
        self.assertTrue(catalog["ok"])
        self.assertEqual(catalog["current"], "voice1.wav")
        self.assertEqual(catalog["voices"], ["voice1.wav", "voice2.wav"])

    def test_set_voice_updates_state_and_persists(self):
        root = Path(tempfile.mkdtemp())

        class VoiceTTS(NullTTSProvider):
            def voices(self):
                return ["female_03.wav", "new_voice.wav"]

        app = test_app(tts=VoiceTTS(), root=root)
        app.voice.voice = "female_03.wav"
        out = app.set_voice("new_voice.wav")
        self.assertTrue(out["ok"])
        self.assertEqual(app.voice.voice, "new_voice.wav")
        reopened = test_app(tts=VoiceTTS(), root=root)
        self.assertEqual(reopened.voice.voice, "new_voice.wav")

    def test_set_voice_cancels_prefetch_and_running_prepare(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Uno.\n\nDos.", prefetch=True)
        with app._prefetch_lock:
            old_executor = app._executor
            future = Future()
            app._prefetch_futures[1] = future
        app.prepare_document()
        for _ in range(20):
            if app.prepare_status()["status"] == "running":
                break
            time.sleep(0.01)
        self.assertEqual(app.prepare_status()["status"], "running")
        app.set_voice("female_01.wav")
        self.assertEqual(len(app._prefetch_futures), 0)
        self.assertIsNot(app._executor, old_executor)
        self.assertIsNone(app._prefetch_future)
        self.assertIsNone(app._prefetch_index)
        self.assertIsNone(app._prefetch_started_ts)
        self.assertTrue(future.cancelled())
        self.assertEqual(app.status()["prefetch_indexes"], [])
        for _ in range(20):
            if app.prepare_status()["status"] != "running":
                break
            time.sleep(0.01)
        self.assertNotEqual(app.prepare_status()["status"], "running")
