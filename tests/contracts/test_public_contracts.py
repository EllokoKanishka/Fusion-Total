from __future__ import annotations

import inspect
import unittest

import fusion_reader_v2
from fusion_reader_v2 import FusionReaderV2
from fusion_reader_v2.service import FusionReaderV2 as LegacyFusionReaderV2
from tests.helpers import managed_test_app


PUBLIC_METHOD_PARAMETERS = {
    "status": (),
    "load_text": ("doc_id", "title", "text", "prefetch", "source_path", "source_type"),
    "load_quick_text": ("text", "title", "start_offset", "prefetch"),
    "load_file": ("path", "prefetch"),
    "clear_document": (),
    "add_reference_text": ("doc_id", "title", "text", "source_path", "source_type"),
    "add_reference_file": ("path",),
    "list_reference_documents": (),
    "remove_reference_document": ("doc_id",),
    "promote_reference_document": ("doc_id", "prefetch"),
    "read_current": ("play",),
    "next": (),
    "previous": (),
    "jump": ("one_based_index",),
    "prepare_document": ("start",),
    "prepare_status": (),
    "cancel_prepare": (),
    "start_audio_export": ("mode", "block", "start", "end"),
    "audio_export_status": ("job_id",),
    "cancel_audio_export": ("job_id",),
    "get_audio_export_download": ("job_id",),
    "voices": (),
    "get_voice_catalog": (),
    "set_voice": ("voice",),
    "test_voice": ("text", "play"),
    "list_notes": ("doc_id", "chunk_index", "current_only"),
    "create_note": ("text", "chunk_index"),
    "update_note": ("note_id", "text", "doc_id"),
    "rename_note": ("note_id", "label", "doc_id"),
    "delete_note": ("note_id", "doc_id"),
    "chat": ("message", "model", "chunk_index"),
    "dialogue_turn_text": ("text", "model", "chunk_index"),
    "dialogue_turn_audio": ("path", "mime", "model", "chunk_index", "audio_meta"),
    "dictation_turn_text": ("text", "commands_enabled"),
    "dictation_turn_audio": ("path", "mime", "commands_enabled"),
    "dictation_speak": ("text",),
    "dialogue_reset": (),
    "laboratory_mode_status": (),
    "set_laboratory_mode": ("mode",),
    "profile_status": (),
    "set_profile": ("mode",),
    "veil_status": (),
    "set_veil": ("mode",),
    "reasoning_status": (),
    "set_reasoning_mode": ("mode",),
    "shutdown_background_work": ("timeout",),
}


class PublicContractTests(unittest.TestCase):
    def test_legacy_and_package_imports_resolve_to_same_facade(self) -> None:
        self.assertIs(FusionReaderV2, LegacyFusionReaderV2)
        self.assertIs(fusion_reader_v2.FusionReaderV2, FusionReaderV2)

    def test_public_method_signatures_are_stable(self) -> None:
        for method_name, expected in PUBLIC_METHOD_PARAMETERS.items():
            with self.subTest(method=method_name):
                signature = inspect.signature(getattr(FusionReaderV2, method_name))
                names = tuple(name for name in signature.parameters if name != "self")
                self.assertEqual(names, expected)

    def test_reader_state_contract_survives_load_navigation_and_clear(self) -> None:
        with managed_test_app() as app:
            initial = app.status()
            self.assertTrue(initial["ok"])
            self.assertFalse(initial["document"]["loaded"])
            self.assertIn("services", initial)
            self.assertIn("document", initial)

            loaded = app.load_text("contract", "Contrato", "Primera frase. Segunda frase.", prefetch=False)
            self.assertTrue(loaded["ok"])
            self.assertEqual(loaded["doc_id"], "contract")
            self.assertEqual(loaded["title"], "Contrato")
            self.assertEqual(loaded["current"], 1)
            self.assertGreaterEqual(loaded["total"], 1)
            self.assertIn("document_generation", loaded)

            jumped = app.jump(1)
            self.assertTrue(jumped["ok"])
            self.assertEqual(jumped["current"], 1)

            cleared = app.clear_document()
            self.assertTrue(cleared["ok"])
            self.assertEqual(cleared["state"], "idle")
            self.assertEqual(cleared["doc_id"], "")

    def test_catalog_and_mode_json_contracts_are_stable(self) -> None:
        with managed_test_app() as app:
            self.assertIn("voices", app.get_voice_catalog())
            self.assertIn("mode", app.laboratory_mode_status())
            self.assertIn("mode", app.profile_status())
            self.assertIn("mode", app.veil_status())
            self.assertIn("mode", app.reasoning_status())
            self.assertIn("items", app.list_notes())
            self.assertIn("status", app.prepare_status())
            self.assertIn("state", app.audio_export_overview())


if __name__ == "__main__":
    unittest.main()
