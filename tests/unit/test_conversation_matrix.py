from __future__ import annotations

import json
import subprocess
import unittest
import urllib.error
from unittest import mock

from fusion_reader_v2 import conversation


class Response:
    def __init__(self, payload: object) -> None:
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.raw


class OllamaProviderMatrixTests(unittest.TestCase):
    def test_base_health_and_ollama_health_matrix(self) -> None:
        self.assertFalse(conversation.ChatProvider().health()["ok"])
        self.assertFalse(conversation.ChatProvider().chat([], model="m").ok)
        provider = conversation.OllamaChatProvider(base_url="http://local", default_model="model", timeout_seconds=1)
        with mock.patch.object(
            conversation.urllib.request,
            "urlopen",
            return_value=Response({"models": [{"name": "model"}, {"model": "other"}, "invalid"]}),
        ):
            health = provider.health()
        self.assertTrue(health["ok"])
        self.assertTrue(health["model_present"])
        with mock.patch.object(conversation.urllib.request, "urlopen", return_value=Response([])):
            self.assertIsNone(provider.health()["model_present"])
        with mock.patch.object(conversation.urllib.request, "urlopen", return_value=Response({"models": "bad"})):
            self.assertEqual(provider.health()["available_models"], [])
        error = urllib.error.HTTPError("http://local", 503, "down", {}, None)
        with mock.patch.object(conversation.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(provider.health()["detail"], "http_503")
        with mock.patch.object(conversation.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertIn("down", provider.health()["detail"])

    def test_ollama_chat_success_empty_http_and_transport_errors(self) -> None:
        provider = conversation.OllamaChatProvider(base_url="http://local", default_model="model", timeout_seconds=1)
        cases = (
            ({"message": {"content": " respuesta "}}, True, "respuesta", ""),
            ({"message": {}}, False, "", "empty_answer"),
            ([], False, "", "empty_answer"),
        )
        for payload, ok, answer, detail in cases:
            with (
                self.subTest(payload=payload),
                mock.patch.object(conversation.urllib.request, "urlopen", return_value=Response(payload)),
            ):
                result = provider.chat([{"role": "user", "content": "q"}], think=True, num_predict=7)
                self.assertEqual((result.ok, result.answer, result.detail), (ok, answer, detail))
        error = urllib.error.HTTPError("http://local", 429, "busy", {}, None)
        with mock.patch.object(conversation.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(provider.chat([], model="other").detail, "http_429")
        with mock.patch.object(conversation.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertIn("down", provider.chat([]).detail)

    def test_ollama_model_install_is_explicit_owned_and_verified(self) -> None:
        provider = conversation.OllamaChatProvider(base_url="http://local", default_model="qwen3:4b")
        completed = subprocess.CompletedProcess(["ollama", "pull", "qwen3:4b"], 0, "ok", "")
        with (
            mock.patch.object(conversation.shutil, "which", return_value="/usr/bin/ollama"),
            mock.patch.object(conversation, "run_owned", return_value=completed) as owned,
            mock.patch.object(
                provider,
                "health",
                return_value={"ok": True, "provider": "ollama", "model_present": True},
            ),
        ):
            result = provider.install_model()
        self.assertTrue(result["ok"])
        self.assertEqual(result["detail"], "installed")
        self.assertEqual(owned.call_args.args[0], ["/usr/bin/ollama", "pull", "qwen3:4b"])

        with mock.patch.object(conversation.shutil, "which", return_value=None):
            missing = provider.install_model()
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["detail"], "ollama_cli_unavailable")


class ConversationHelperMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = conversation.ConversationCore(conversation.NullChatProvider("ok"), max_document_chars=200)

    def test_all_persona_veil_branches_and_laboratory_history(self) -> None:
        veils = (
            "lucy",
            "nocturna",
            "critica",
            "sombra",
            "confesional",
            "taller",
            "debate",
            "evocadora",
            "directa",
            "incomoda",
            "rigurosa",
            "intima",
            "bar_filosofico",
            "desarme",
            "pregunta_viva",
        )
        for veil in veils:
            with self.subTest(veil=veil):
                text = self.core._persona_overlay(
                    reasoning_mode="thinking",
                    dialogue=True,
                    profile="bohemia" if veil == "nocturna" else "academica",
                    free_mode=veil == "taller",
                    veil=veil,
                )
                self.assertTrue(text)
        self.assertEqual(self.core._laboratory_context_text([]), "")
        history = [
            {"role": "assistant", "content": "ignored"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "x" * 5000},
            {"role": "user", "content": "último"},
        ]
        context = self.core._laboratory_context_text(history)
        self.assertIn("recortado", context)
        self.assertIn("último", context)

    def test_document_catalog_focus_selection_and_excerpt_branches(self) -> None:
        chunks = [
            {"chunk_number": 1, "text": "Introducción breve"},
            {"chunk_number": 2, "text": "metafísica " * 80},
            {"chunk_number": 3, "text": ""},
        ]
        snapshot = {
            "title": "Principal",
            "doc_id": "main",
            "current": 2,
            "total": 3,
            "document_text": "documento " * 100,
            "document_chunks": chunks,
            "notes": [{"chunk_number": 2, "text": "nota"}, {"text": ""}],
            "reference_documents": [
                "invalid",
                {
                    "doc_id": "ref",
                    "title": "Referencia",
                    "source_type": "pdf",
                    "total": 3,
                    "preview": "metafísica",
                    "chunks": chunks,
                },
            ],
            "laboratory_focus": {
                "title": "Referencia",
                "role": "reference",
                "chunk_number": 2,
                "total": 3,
                "query": "metafísica",
                "reason": "search",
                "text": "x" * 600,
            },
        }
        records = self.core._document_records(snapshot)
        self.assertEqual(len(records), 2)
        self.assertIn("Principal", self.core._document_catalog_text(snapshot))
        self.assertIn("Referencia", self.core._reference_catalog_text(snapshot["reference_documents"]))
        self.assertEqual(self.core._reference_catalog_text("bad"), "")
        self.assertIn("recortado", self.core._laboratory_focus_text(snapshot["laboratory_focus"]))
        self.assertEqual(self.core._laboratory_focus_text({}), "")
        self.assertEqual(self.core._laboratory_focus_text("bad"), "")

        selected = self.core._select_relevant_records("ref metafísica", records)
        self.assertTrue(selected)
        requested = self.core._extract_requested_chunk_numbers("bloque 2 y sección 2 y chunk 99")
        self.assertEqual(requested, [2, 99])
        excerpt = self.core._render_document_excerpt(selected[0], "metafísica", requested)
        self.assertIn("Bloques disponibles", excerpt)
        self.assertIn("recortado", excerpt)
        self.assertEqual(self.core._select_chunk_indexes({"chunks": []}, "", []), [])
        self.assertIn("Sin chunks", self.core._render_document_excerpt({"title": "Sin chunks"}, "", []))
        self.assertEqual(self.core._keyword_overlap_score("de la", "texto"), 0)
        self.assertGreater(self.core._keyword_overlap_score("metafísica", "texto metafísica"), 0)

        context = self.core._context_text(
            "analiza bloque 2 de referencia",
            snapshot,
            history=[{"role": "user", "content": "metafísica"}],
            include_document=True,
            include_blocks=True,
        )
        self.assertIn("Documento recortado", context)
        self.assertIn("FOCO ACTUAL", context)

    def test_context_helper_empty_short_reference_and_limit_branches(self) -> None:
        main = {
            "doc_id": "",
            "title": "Main object",
            "source_type": "",
            "total": 2,
            "chunks": [{"text": "uno"}, {"text": "dos"}],
        }
        snapshot = {
            "main_document": main,
            "current": 1,
            "reference_documents": [{"title": "Sin id", "chunks": [{"text": "referencia"}], "total": 1}],
        }
        records = self.core._document_records(snapshot)
        self.assertEqual(records[0]["role"], "main")
        self.assertEqual(self.core._document_records({}), [])
        self.assertIn("Sin id", self.core._reference_catalog_text(snapshot["reference_documents"]))
        focus = self.core._laboratory_focus_text({"title": "Foco", "text": "corto"})
        self.assertIn("corto", focus)

        fallback = self.core._select_relevant_records("", records)
        self.assertEqual(fallback[0]["title"], "Main object")
        self.assertEqual(self.core._select_relevant_records("", []), [])
        reference_indexes = self.core._select_chunk_indexes(records[1], "", [1, 99])
        self.assertEqual(reference_indexes, [0])
        main_indexes = self.core._select_chunk_indexes(records[0], "", [2, 99])
        self.assertIn(1, main_indexes)

        no_chunks = self.core._render_document_excerpt({"title": "Vacío", "preview": "resumen"}, "", [])
        self.assertIn("Resumen breve", no_chunks)
        with mock.patch.object(self.core, "_select_chunk_indexes", return_value=[-1, 99, 1]):
            excerpt = self.core._render_document_excerpt(
                {"title": "Saltos", "chunks": [{"text": "uno"}, {"text": ""}], "total": 2},
                "",
                [],
            )
        self.assertNotIn("- Bloque", excerpt)

        self.assertEqual(self.core._relevant_documents_text("q", {}), "")
        with mock.patch.object(self.core, "_render_document_excerpt", return_value=""):
            self.assertEqual(self.core._relevant_documents_text("q", snapshot), "")
        self.core.max_document_excerpt_chars = 20
        self.core.max_reference_chars = 20
        clipped = self.core._relevant_documents_text("referencia", snapshot)
        self.assertIn("recortados", clipped)

        minimal_context = self.core._context_text(
            "q",
            {"title": "T", "doc_id": "d"},
            include_document=False,
            include_blocks=False,
        )
        self.assertNotIn("DOCUMENTO COMPLETO", minimal_context)


if __name__ == "__main__":
    unittest.main()
