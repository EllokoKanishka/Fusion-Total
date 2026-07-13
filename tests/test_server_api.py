import unittest
import os
import tempfile
from pathlib import Path
from unittest import mock
from fusion_reader_v2 import OllamaChatProvider
from tests.helpers import test_app


def _web_source() -> str:
    paths = (
        Path("fusion_reader_v2/web/server.py"),
        Path("fusion_reader_v2/web/static/index.html"),
        Path("fusion_reader_v2/web/static/app.js"),
        Path("fusion_reader_v2/web/static/js/bootstrap.mjs"),
        Path("fusion_reader_v2/web/static/js/audio.mjs"),
        Path("fusion_reader_v2/web/static/styles.css"),
    )
    return "\\n".join(path.read_text(encoding="utf-8") for path in paths)


class ServerAPITests(unittest.TestCase):
    def test_server_api_returns_status(self):
        app = test_app()
        self.assertTrue(app.status()["ok"])

    def test_server_api_allows_switching_veils(self):
        app = test_app()
        app.set_veil("lucy")
        self.assertEqual(app.veil_status()["mode"], "lucy")

    def test_server_api_allows_switching_profiles(self):
        app = test_app()
        app.set_profile("bohemia")
        self.assertEqual(app.profile_status()["mode"], "bohemia")

    def test_server_api_allows_document_operations(self):
        app = test_app()
        app.load_text("doc", "Title", "Text", prefetch=False)
        self.assertEqual(app.status()["title"], "Title")
        app.clear_document()
        self.assertEqual(app.status()["title"], "")

    def test_server_api_includes_reasoning_mode_switch(self):
        app = test_app()
        app.set_reasoning_mode("supreme")
        self.assertEqual(app.reasoning_status()["mode"], "supreme")

    def test_server_ui_contains_critical_components(self):
        server = _web_source()
        self.assertIn('class="reader"', server)
        self.assertIn('id="chatInput"', server)

    def test_server_exposes_reference_documents_ui_and_endpoints(self):
        server = _web_source()
        self.assertIn("referenceModeToggle", server)
        self.assertIn("/api/reference/promote", server)
        self.assertIn("cargado como documento principal", server)
        self.assertIn("Cargar este archivo como consulta", server)
        self.assertIn("Lectura activa", server)

    def test_server_upload_ui_accepts_dotx_like_backend(self):
        server = _web_source()
        self.assertIn(".dotx", server)

    def test_manual_chat_uses_dialogue_voice_when_dialogue_is_active(self):
        server = _web_source()
        self.assertIn("sendTypedDialogue", server)
        self.assertIn("playDialogueAnswer", server)

    def test_reasoning_tabs_and_endpoint_exist_in_server_ui(self):
        server = _web_source()
        self.assertIn("Pensamiento supremo", server)
        self.assertIn("/api/reasoning/mode", server)

    def test_dialogue_low_latency_defaults_are_configured(self):
        server = _web_source()
        stt_server = Path("scripts/fusion_reader_v2_stt_server.py").read_text(encoding="utf-8")
        self.assertIn("silenceStopMs: 1250", server)
        self.assertIn("FUSION_READER_STT_BEAM_SIZE", stt_server)

    def test_server_exposes_free_laboratory_mode_button_and_endpoint(self):
        server = _web_source()
        self.assertIn("freeModeBtn", server)
        self.assertIn("/api/laboratory/mode", server)

    def test_academic_profile_uses_larger_token_budget(self):
        academic = Path("scripts/start_fusion_reader_v2_academic.sh").read_text(encoding="utf-8")
        self.assertIn("FUSION_READER_CHAT_NUM_PREDICT:-1536", academic)

    def test_ollama_thinking_default_token_budget_is_not_tiny(self):
        previous_think = os.environ.get("FUSION_READER_CHAT_THINK")
        try:
            os.environ["FUSION_READER_CHAT_THINK"] = "1"
            os.environ.pop("FUSION_READER_CHAT_NUM_PREDICT", None)
            provider = OllamaChatProvider(base_url="http://x")
            self.assertGreaterEqual(provider.num_predict, 1024)
        finally:
            if previous_think:
                os.environ["FUSION_READER_CHAT_THINK"] = previous_think

    def test_server_ui_contains_friendly_voice_labels(self):
        server = _web_source()
        self.assertIn("M03 — Hera", server)
        self.assertNotIn("Mujer 03 — Emilia", server)

    def test_server_ui_contains_profile_and_veil_selectors(self):
        server = _web_source()
        self.assertIn('id="profileSelect"', server)
        self.assertIn('id="veilSelect"', server)

    def test_start_fusion_reader_v2_bohemia_script_is_valid(self):
        script = Path("scripts/start_fusion_reader_v2_bohemia.sh").read_text(encoding="utf-8")
        self.assertIn("FUSION_READER_BOHEMIA_CHAT_MODEL", script)

    def test_server_contains_clear_document_button_and_endpoint(self):
        server = _web_source()
        self.assertIn('id="clearDocBtn"', server)
        self.assertIn("/api/document/clear", server)

    def test_mcp_memory_server_core_logic(self):
        from scripts import fusion_memory_mcp_server as mcp

        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp)
            (memory / "project_state.md").write_text("# Project State\n", encoding="utf-8")
            with mock.patch.object(mcp, "MEMORY_DIR", memory):
                self.assertIn("project_state.md", mcp.allowed_memory_files())
                self.assertTrue(mcp.read_memory_file("project_state.md").startswith("# Project State"))

    def test_status_reports_runtime_metadata(self):
        from scripts import fusion_reader_v2_server as server_mod

        rt = server_mod.RUNTIME_INFO
        self.assertEqual(rt["app"], "fusion_reader_v2")
        self.assertIn("commit", rt)
        self.assertIn("pid", rt)
        self.assertEqual(rt["port"], server_mod.PORT)

    def test_status_reports_runtime_services_without_ambiguous_ok(self):
        app = test_app()
        status = app.status()
        self.assertIn("services", status)
        self.assertIn("tts", status["services"])
        self.assertIn("stt", status["services"])
        self.assertIn("chat", status["services"])

    def test_voice_selector_has_persistence_logic_and_auto_repair(self):
        server = _web_source()
        self.assertIn("ensureVoiceCatalog", server)
        self.assertIn("voiceCatalogRefreshInFlight", server)
        self.assertIn("gotMany && hadMany", server)

    def test_server_ui_surfaces_friendly_tts_blocking_message_and_disables_read(self):
        server = _web_source()
        helper = Path("fusion_reader_v2/web/static/busy_controls.js").read_text(encoding="utf-8")
        self.assertIn("friendlyTtsMessage", server)
        self.assertIn("TTS bloqueado", server)
        self.assertIn("busyControls.setStatus(data, els.noteInput ? els.noteInput.value : '')", server)
        self.assertIn("El TTS de Fusion está vivo pero no quedó validado como propio.", server)
        self.assertIn("computeControlAvailability", helper)
        self.assertIn("readBtn: documentLoaded && documentHasText", helper)
        self.assertIn("applyControlState", helper)

    def test_server_ui_restores_prepare_progress_detail(self):
        server = _web_source()
        self.assertIn("Preparando documento: ${label}", server)
        self.assertIn("Documento listo: ${label}", server)
        self.assertIn("bloque ${Math.min(done, total)} de ${total}", server)
