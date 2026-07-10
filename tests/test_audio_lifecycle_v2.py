import os
import tempfile
import threading
import unittest
import wave
from pathlib import Path

from fusion_reader_v2 import AudioArtifact, NullTTSProvider
from tests.helpers import test_app


class ControlledTTS(NullTTSProvider):
    name = "controlled_tts"

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.available = True

    def health(self):
        return {"ok": self.available, "provider": self.name, "detail": "" if self.available else "temporarily_down"}

    def synthesize(self, text, voice="", language="es"):
        self.calls.append((text, voice, language))
        self.started.set()
        self.release.wait(2)
        if not self.available:
            return AudioArtifact(False, provider=self.name, detail="tts_down")
        fd, name = tempfile.mkstemp(prefix=("alpha_" if "ALFA" in text else "beta_"), suffix=".wav")
        os.close(fd)
        with wave.open(name, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 80)
        return AudioArtifact(True, path=Path(name), provider=self.name)


class AudioLifecycleV2Tests(unittest.TestCase):
    def test_late_read_from_replaced_document_is_stale(self):
        tts = ControlledTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "ALFA ALFA.", prefetch=False)
        result = {}
        thread = threading.Thread(target=lambda: result.update(app.read_current(play=False)))
        thread.start()
        self.assertTrue(tts.started.wait(1))
        old_generation = app.status()["document_generation"]
        app.load_text("b", "B", "BETA BETA.", prefetch=False)
        self.assertGreater(app.status()["document_generation"], old_generation)
        tts.release.set()
        thread.join(2)
        self.assertFalse(result["ok"])
        self.assertTrue(result["stale"])
        self.assertFalse(result.get("audio"))

    def test_prefetch_index_zero_is_scoped_to_document_generation(self):
        tts = ControlledTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "ALFA ALFA.", prefetch=True)
        self.assertTrue(tts.started.wait(1))
        app.load_text("b", "B", "BETA BETA.", prefetch=False)
        tts.release.set()
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["requested_doc_id"], "b")
        self.assertTrue(any("BETA" in call[0] for call in tts.calls))

    def test_clear_invalidates_pending_read_and_preserves_references_and_cache(self):
        tts = ControlledTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "ALFA ALFA.", prefetch=False)
        app.add_reference_text("ref", "Reference", "Reference text.")
        result = {}
        thread = threading.Thread(target=lambda: result.update(app.read_current(play=False)))
        thread.start()
        self.assertTrue(tts.started.wait(1))
        app.clear_document()
        tts.release.set()
        thread.join(2)
        self.assertTrue(result["stale"])
        self.assertEqual(len(app.status()["reference_documents"]), 1)
        self.assertIsNotNone(app.cache.get("ALFA ALFA.", app.voice.voice, app.voice.language))

    def test_two_simultaneous_reads_share_synthesis(self):
        tts = ControlledTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "ALFA ALFA.", prefetch=False)
        results = []
        threads = [threading.Thread(target=lambda: results.append(app.read_current(play=False))) for _ in range(2)]
        for thread in threads: thread.start()
        self.assertTrue(tts.started.wait(1))
        tts.release.set()
        for thread in threads: thread.join(2)
        self.assertEqual(len(tts.calls), 1)
        self.assertTrue(all(item["ok"] for item in results))

    def test_cached_read_works_when_health_is_down_and_uncached_fails(self):
        tts = ControlledTTS()
        tts.release.set()
        app = test_app(tts=tts)
        app.load_text("a", "A", "ALFA ALFA.", prefetch=False)
        self.assertTrue(app.read_current(play=False)["ok"])
        tts.available = False
        self.assertTrue(app.read_current(play=False)["cached"])
        app.load_text("b", "B", "BETA BETA.", prefetch=False)
        out = app.read_current(play=False)
        self.assertFalse(out["ok"])
        self.assertIn("voz no está disponible", out["error"])

    def test_promote_and_clear_advance_generation(self):
        app = test_app()
        app.load_text("a", "A", "Alpha.", prefetch=False)
        first = app.status()["document_generation"]
        app.add_reference_text("b", "B", "Beta.")
        app.promote_reference_document("b", prefetch=False)
        second = app.status()["document_generation"]
        app.clear_document()
        self.assertGreater(second, first)
        self.assertGreater(app.status()["document_generation"], second)

    def test_old_prepare_cannot_update_new_document_status(self):
        tts = ControlledTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "ALFA ALFA.", prefetch=False)
        app.prepare_document()
        self.assertTrue(tts.started.wait(1))
        old_thread = app._prepare_thread
        app.load_text("b", "B", "BETA BETA.", prefetch=False)
        tts.release.set()
        if old_thread:
            old_thread.join(1)
        status = app.prepare_status()
        self.assertEqual(status["status"], "idle")
        self.assertEqual(status["doc_id"], "")

    def test_interactive_read_during_prepare_waits_only_current_synthesis(self):
        tts = ControlledTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "ALFA ALFA.", prefetch=False)
        app.prepare_document()
        self.assertTrue(tts.started.wait(1))
        result = {}
        reader = threading.Thread(target=lambda: result.update(app.read_current(play=False)))
        reader.start()
        tts.release.set()
        reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertTrue(result["ok"])
        self.assertLessEqual(len(tts.calls), 1)

    def test_tts_runtime_state_distinguishes_starting_and_down(self):
        tts = ControlledTTS()
        app = test_app(tts=tts)
        tts.available = False
        tts.health = lambda: {"ok": False, "provider": tts.name, "detail": "model_starting"}
        self.assertEqual(app.status()["tts_state"], "starting")
        tts.health = lambda: {"ok": False, "provider": tts.name, "detail": "connection_refused"}
        self.assertEqual(app.status()["tts_state"], "temporarily_unavailable")

    def test_status_exposes_audio_identity(self):
        app = test_app()
        loaded = app.load_text("a", "A", "Alpha.", prefetch=False)
        self.assertEqual(loaded["document_generation"], app.status()["document_generation"])
        status = app.status()
        self.assertGreater(status["document_generation"], 0)
        self.assertEqual(status["audio_state"], "needs_generation")
        out = app.read_current(play=False)
        self.assertEqual(out["requested_doc_id"], "a")
        self.assertEqual(out["requested_chunk_index"], 0)
        self.assertEqual(out["audio_state"], "ready")


class AudioLifecycleFrontendTests(unittest.TestCase):
    def test_frontend_invalidates_requests_and_resets_player(self):
        text = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        for token in ("AbortController", "audioLifecycleSequence", "activeReadRequest", "els.player.pause()", "els.player.currentTime = 0", "els.player.removeAttribute('src')", "els.player.load()", "resetAudioLifecycle"):
            self.assertIn(token, text)
        load = text[text.index("async function loadFile(file)"):text.index("async function navigate(")]
        self.assertIn("if (role === 'main')", load)
        self.assertNotIn("if (role === 'reference') {\n        resetAudioLifecycle", load)

    def test_frontend_checks_identity_and_does_not_gate_read_on_tts_snapshot(self):
        text = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("Number(data.document_generation || 0) !== currentGeneration", text)
        self.assertIn("String(data.requested_doc_id || '') !== currentDocId", text)
        read = text[text.index("async function readCurrent()"):text.index("async function pollPrepare()")]
        self.assertNotIn("if (!ttsActionAvailable(status))", read)
        self.assertIn("Solicitud aceptada", read)
        self.assertIn('self._result(409 if result.get("stale") else 200, result)', text)


if __name__ == "__main__":
    unittest.main()
