import os
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

from fusion_reader_v2 import AudioArtifact, NullTTSProvider
from tests.helpers import test_app
from tests.helpers import web_source


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


class OrderedTTS(ControlledTTS):
    def synthesize(self, text, voice="", language="es"):
        self.calls.append((text, voice, language))
        if len(self.calls) == 1:
            self.started.set()
            self.release.wait(2)
        fd, name = tempfile.mkstemp(prefix="ordered_", suffix=".wav")
        os.close(fd)
        with wave.open(name, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 80)
        return AudioArtifact(True, path=Path(name), provider=self.name)


class PriorityTTS(NullTTSProvider):
    name = "priority_tts"

    def __init__(self):
        super().__init__()
        self.available = True
        self.order: list[str] = []
        self._events_lock = threading.Lock()
        self._started_events: dict[str, threading.Event] = {}
        self._release_events: dict[str, threading.Event] = {}

    def _events_for(self, text: str) -> tuple[threading.Event, threading.Event]:
        with self._events_lock:
            started = self._started_events.setdefault(text, threading.Event())
            release = self._release_events.setdefault(text, threading.Event())
            return started, release

    def started_event(self, text: str) -> threading.Event:
        return self._events_for(text)[0]

    def release_text(self, text: str) -> None:
        self._events_for(text)[1].set()

    def order_snapshot(self) -> list[str]:
        with self._events_lock:
            return list(self.order)

    def synthesize(self, text, voice="", language="es"):
        with self._events_lock:
            self.calls.append((text, voice, language))
            self.order.append(text)
            started = self._started_events.setdefault(text, threading.Event())
            release = self._release_events.setdefault(text, threading.Event())
            started.set()
        release.wait(2)
        if not self.available:
            return AudioArtifact(False, provider=self.name, detail="tts_down")
        fd, name = tempfile.mkstemp(prefix="priority_", suffix=".wav")
        os.close(fd)
        with wave.open(name, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 80)
        return AudioArtifact(True, path=Path(name), provider=self.name)


class AudioLifecycleV2Tests(unittest.TestCase):
    def test_navigation_returns_enriched_identity_without_advancing_generation(self):
        app = test_app()
        app.load_text("nav", "Navigation", "Alpha.", prefetch=False)
        app.session.document.chunks = ["Alpha.", "Beta.", "Gamma."]
        generation = app.status()["document_generation"]
        for snapshot in (app.next(), app.previous(), app.jump(3)):
            self.assertEqual(snapshot["document_generation"], generation)
            self.assertTrue(snapshot["document"]["loaded"])
            self.assertIn(snapshot["audio_state"], {"cached", "needs_generation"})
        self.assertTrue(app.read_current(play=False)["ok"])

    def test_prepare_can_restart_with_a_fresh_cancel_event(self):
        tts = ControlledTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "ALFA ALFA.", prefetch=False)
        app.prepare_document()
        first_event = app._prepare_cancel
        self.assertTrue(tts.started.wait(1))
        app.cancel_prepare()
        tts.release.set()
        app._prepare_thread.join(2)
        restarted = app.prepare_document()
        self.assertIsNot(app._prepare_cancel, first_event)
        self.assertFalse(app._prepare_cancel.is_set())
        self.assertEqual(restarted["status"], "running")
        app._prepare_thread.join(2)
        self.assertEqual(app.prepare_status()["status"], "done")

    def test_voice_change_makes_inflight_read_stale(self):
        tts = ControlledTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "ALFA ALFA.", prefetch=False)
        result = {}
        thread = threading.Thread(target=lambda: result.update(app.read_current(play=False)))
        thread.start()
        self.assertTrue(tts.started.wait(1))
        app.voice.voice = "voice-b.wav"
        tts.release.set()
        thread.join(2)
        self.assertTrue(result["stale"])
        self.assertEqual(result["detail"], "audio_identity_changed")

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
        for thread in threads:
            thread.start()
        self.assertTrue(tts.started.wait(1))
        tts.release.set()
        for thread in threads:
            thread.join(2)
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
        time.sleep(0.01)
        tts.release.set()
        reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertTrue(result["ok"])
        self.assertLessEqual(len(tts.calls), 1)

    def test_interactive_read_is_next_after_current_prepare_unit(self):
        tts = OrderedTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "One.", prefetch=False)
        app.session.document.chunks = ["One.", "Two.", "Three.", "Four.", "Five."]
        app.prepare_document(start="beginning")
        self.assertTrue(tts.started.wait(1))
        app.jump(5)
        result = {}
        reader = threading.Thread(target=lambda: result.update(app.read_current(play=False)))
        reader.start()
        time.sleep(0.01)
        tts.release.set()
        reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertTrue(result["ok"])
        self.assertEqual(tts.calls[1][0], "Five.")
        app._prepare_thread.join(2)
        self.assertEqual(app.prepare_status()["status"], "done")

    def test_exact_prefetch_promotion_keeps_interactive_priority_until_audio_is_ready(self):
        tts = PriorityTTS()
        app = test_app(tts=tts)
        app.load_text("a", "A", "One.\n\nTwo.\n\nThree.\n\nFour.\n\nFive.", prefetch=False)
        app.session.document.chunks = ["One.", "Two.", "Three.", "Four.", "Five."]
        app.prepare_document(start="beginning")
        self.assertTrue(tts.started_event("One.").wait(1))
        app.jump(5)
        exact_key = app._prefetch_key(app._document_generation, 4, "Five.", app.voice.voice, app.voice.language)
        with app._prefetch_lock:
            exact_future = app._prefetch_futures.get(exact_key)
            self.assertIsNotNone(exact_future)
            self.assertFalse(exact_future.done())
        result = {}
        reader = threading.Thread(target=lambda: result.update(app.read_current(play=False)))
        reader.start()
        try:
            with app._tts_gate:
                self.assertTrue(app._tts_gate.wait_for(lambda: app._interactive_tts_pending > 0, timeout=1))
                self.assertEqual(app._interactive_tts_pending, 1)
                self.assertFalse(app._tts_gate.wait_for(lambda: app._interactive_tts_pending == 0, timeout=0.1))
            self.assertEqual(tts.order_snapshot(), ["One."])
            tts.release_text("One.")
            self.assertTrue(tts.started_event("Five.").wait(1))
            self.assertEqual(tts.order_snapshot(), ["One.", "Five."])
            self.assertFalse(tts.started_event("Two.").is_set())
            self.assertFalse(tts.started_event("Three.").is_set())
            self.assertFalse(tts.started_event("Four.").is_set())
            tts.release_text("Five.")
            reader.join(2)
            self.assertFalse(reader.is_alive())
            self.assertTrue(result["ok"])
            self.assertEqual(result["requested_chunk_index"], 4)
            self.assertEqual(result["requested_doc_id"], "a")
            tts.release_text("Two.")
            tts.release_text("Three.")
            tts.release_text("Four.")
            if app._prepare_thread:
                app._prepare_thread.join(2)
            self.assertEqual(app.prepare_status()["status"], "done")
        finally:
            for text in ("One.", "Two.", "Three.", "Four.", "Five."):
                tts.release_text(text)
            reader.join(2)
            if app._prepare_thread:
                app._prepare_thread.join(2)

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
        text = web_source()
        for token in (
            "AbortController",
            "audioLifecycleSequence",
            "activeReadRequest",
            "els.player.pause()",
            "els.player.currentTime = 0",
            "els.player.removeAttribute('src')",
            "els.player.load()",
            "resetAudioLifecycle",
        ):
            self.assertIn(token, text)
        load = text[text.index("async function loadFile(file)") : text.index("async function navigate(")]
        self.assertIn("if (role === 'main')", load)
        self.assertNotIn("if (role === 'reference') {\n        resetAudioLifecycle", load)

    def test_frontend_checks_identity_and_does_not_gate_read_on_tts_snapshot(self):
        text = web_source()
        self.assertIn("Number(data.document_generation || 0) !== currentGeneration", text)
        self.assertIn("String(data.requested_doc_id || '') !== currentDocId", text)
        self.assertIn("String(data.voice || '') !== currentVoice", text)
        self.assertIn("String(data.language || '') !== currentLanguage", text)
        change_voice = text[
            text.index("async function changeVoice()") : text.index("async function ensureVoiceCatalog()")
        ]
        self.assertIn("resetAudioLifecycle", change_voice)
        read = text[text.index("async function readCurrent()") : text.index("async function pollPrepare()")]
        self.assertNotIn("if (!ttsActionAvailable(status))", read)
        self.assertIn("Solicitud aceptada", read)
        self.assertIn('self._result(409 if result.get("stale") else 200, result)', text)

    def test_frontend_busy_leases_are_balanced_for_resetting_operations(self):
        server_text = web_source()
        helper_text = Path("fusion_reader_v2/web/static/busy_controls.js").read_text(encoding="utf-8")
        self.assertNotIn("__BUSY_CONTROL_HELPERS__", server_text)
        self.assertIn('src="/static/busy_controls.js"', server_text)
        self.assertIn("busyControls.setStatus(data, els.noteInput ? els.noteInput.value : '')", server_text)
        self.assertIn(
            "els.noteInput.addEventListener('input', () => busyControls.setNoteText(els.noteInput.value));", server_text
        )
        self.assertNotIn("function setBusy(", server_text)
        self.assertIn("computeControlAvailability", helper_text)
        self.assertIn("applyControlState", helper_text)
        self.assertIn("createBusyControlState", helper_text)

        blocks = {
            "changeVoice()": (
                "async function changeVoice()",
                "async function ensureVoiceCatalog()",
                "resetAudioLifecycle",
            ),
            "clearDocument()": (
                "async function clearDocument()",
                "async function setLaboratoryMode(mode)",
                "resetAudioLifecycle",
            ),
            "promoteReference(docId)": (
                "async function promoteReference(docId)",
                "async function removeReference(docId)",
                "resetAudioLifecycle",
            ),
            "loadFile(file)": ("async function loadFile(file)", "function canConvertPdf(file)", "resetAudioLifecycle"),
            "navigate(path, body = {})": (
                "async function navigate(path, body = {})",
                "async function readCurrent()",
                "invalidatePendingRead();",
            ),
            "readCurrent()": (
                "async function readCurrent()",
                "async function pollPrepare()",
                "invalidatePendingRead();",
            ),
            "setReasoningMode(mode)": (
                "async function setReasoningMode(mode)",
                "function renderLabFocus(focus)",
                "const data = await api('/api/reasoning/mode', { mode: targetMode });",
            ),
            "startAudioExport()": (
                "async function startAudioExport()",
                "async function cancelAudioExport()",
                "const data = await api('/api/audio-export', payload);",
            ),
            "readNextWhenAudioEnds()": (
                "async function readNextWhenAudioEnds()",
                "async function sendChat()",
                "log('Avanzando al siguiente bloque...');",
            ),
            "sendChat()": (
                "async function sendChat()",
                "function stopDialoguePlaybackForTypedTurn()",
                "if (dialogue.active) {",
            ),
            "clearLaboratoryHistory()": (
                "async function clearLaboratoryHistory()",
                "function dialogueMimeType()",
                "const data = await api('/api/laboratory/reset', {});",
            ),
            "saveCurrentNote()": (
                "async function saveCurrentNote()",
                "async function goToNote(note)",
                "const data = await api('/api/notes/create', { text });",
            ),
        }
        for name, (start_marker, end_marker, first_action) in blocks.items():
            block = server_text[server_text.index(start_marker) : server_text.index(end_marker)]
            self.assertIn("beginBusyLease()", block, name)
            self.assertIn("releaseBusy();", block, name)
            self.assertIn("try {", block, name)
            self.assertIn("finally {", block, name)
            self.assertLess(block.index("beginBusyLease()"), block.index(first_action), name)

        prepare_block = server_text[
            server_text.index("async function prepareDocument()") : server_text.index("async function cancelPrepare()")
        ]
        self.assertIn("const releaseBusy = beginBusyLease();", prepare_block)
        self.assertIn("started = true;", prepare_block)
        self.assertIn("if (started) {", prepare_block)
        self.assertIn("await pollPrepare();", prepare_block)
        self.assertNotIn("setBusy(", prepare_block)
        read_block = server_text[
            server_text.index("async function readCurrent()") : server_text.index("async function pollPrepare()")
        ]
        self.assertIn("if (activeReadController === controller) {", read_block)
        self.assertIn("activeReadController = null;", read_block)


if __name__ == "__main__":
    unittest.main()
