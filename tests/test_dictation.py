import tempfile
import unittest
from pathlib import Path

from fusion_reader_v2 import NullSTTProvider, interpret_dictation_transcript
from tests.helpers import EmptyTranscriptSTTProvider, FailingTTSProvider, test_app


class DictationInstructionTests(unittest.TestCase):
    def test_empty_utterance_is_a_noop(self):
        self.assertEqual(interpret_dictation_transcript(" \n ").kind, "noop")

    def test_plain_utterance_remains_dictation(self):
        instruction = interpret_dictation_transcript("La memoria no es un archivo inmóvil.")
        self.assertEqual(instruction.kind, "dictate")
        self.assertEqual(instruction.text, "La memoria no es un archivo inmóvil.")

    def test_commands_can_be_disabled_to_remove_ambiguity(self):
        instruction = interpret_dictation_transcript("Borrá la palabra memoria", commands_enabled=False)
        self.assertEqual(instruction.kind, "dictate")

    def test_live_audio_commands_require_lucy_wake_word(self):
        literal = interpret_dictation_transcript(
            "Borrá la palabra memoria",
            require_wake_word=True,
        )
        command = interpret_dictation_transcript(
            "Lucy, borrá la palabra memoria",
            require_wake_word=True,
        )
        unknown = interpret_dictation_transcript(
            "Lucy, inventá una orden ilimitada",
            require_wake_word=True,
        )
        self.assertEqual((literal.kind, literal.text), ("dictate", "Borrá la palabra memoria"))
        self.assertEqual((command.kind, command.target), ("delete", "la palabra memoria"))
        self.assertEqual(unknown.kind, "noop")

    def test_lucy_can_stop_dictation_with_natural_spanish(self):
        for utterance in ("Lucy, pará acá", "Lucy, paramos acá", "Lucy, detener el dictado"):
            with self.subTest(utterance=utterance):
                self.assertEqual(
                    interpret_dictation_transcript(utterance, require_wake_word=True).kind,
                    "stop_listening",
                )

    def test_lucy_pause_preamble_can_lead_into_a_bounded_command(self):
        instruction = interpret_dictation_transcript(
            "Lucy, paramos acá. Borrá piedra y escribí agua",
            require_wake_word=True,
        )
        self.assertEqual((instruction.kind, instruction.target, instruction.text), ("replace", "piedra", "agua"))

    def test_delete_and_write_becomes_bounded_replacement(self):
        instruction = interpret_dictation_transcript("No, borrá archivo inmóvil y escribí tejido vivo")
        self.assertEqual(instruction.kind, "replace")
        self.assertEqual(instruction.target, "archivo inmóvil")
        self.assertEqual(instruction.text, "tejido vivo")

    def test_delete_from_anchor_to_end_is_a_bounded_operation(self):
        for utterance in (
            "Lucy, borrá de Buenos Aires para adelante",
            "Lucy.borra.Buenos Aires.hasta el final",
        ):
            with self.subTest(utterance=utterance):
                instruction = interpret_dictation_transcript(utterance, require_wake_word=True)
                self.assertEqual((instruction.kind, instruction.target), ("delete_from", "Buenos Aires"))

    def test_replace_delete_undo_and_redo_commands(self):
        replace = interpret_dictation_transcript("Reemplazá Borges por Spinoza")
        delete = interpret_dictation_transcript("Borrá todas las veces que aparece quizá")
        self.assertEqual((replace.kind, replace.target, replace.text), ("replace", "Borges", "Spinoza"))
        self.assertEqual((delete.kind, delete.target, delete.all_matches), ("delete", "quizá", True))
        self.assertEqual(interpret_dictation_transcript("deshacer").kind, "undo")
        self.assertEqual(interpret_dictation_transcript("rehacé").kind, "redo")

    def test_editor_control_and_insertion_commands(self):
        cases = {
            "detener el dictado": ("stop_listening", ""),
            "limpiá el documento": ("clear", ""),
            "punto y aparte": ("insert", "\n\n"),
            "escribí un jardín de senderos": ("insert", "un jardín de senderos"),
            "borrá laberinto": ("delete", ""),
        }
        for utterance, expected in cases.items():
            with self.subTest(utterance=utterance):
                instruction = interpret_dictation_transcript(utterance)
                self.assertEqual((instruction.kind, instruction.text), expected)
        self.assertEqual(interpret_dictation_transcript("borrá laberinto").target, "laberinto")

    def test_read_commands_keep_scope_and_target(self):
        self.assertEqual(interpret_dictation_transcript("Léeme la última hoja").scope, "last_page")
        paragraph = interpret_dictation_transcript("Léeme el párrafo número 3")
        self.assertEqual((paragraph.scope, paragraph.number), ("paragraph_number", 3))
        start = interpret_dictation_transcript("Léeme a partir de el jardín de senderos")
        self.assertEqual((start.scope, start.target), ("from_text", "el jardín de senderos"))

    def test_read_commands_cover_each_bounded_scope(self):
        cases = {
            "Léeme": ("all", ""),
            "Léeme la selección": ("selection", ""),
            "Léeme el último párrafo": ("last_paragraph", ""),
            "Léeme el párrafo actual": ("current_paragraph", ""),
            "Léeme el párrafo anterior": ("previous_paragraph", ""),
            "Léeme el párrafo que comienza con La casa de Asterión": (
                "paragraph_matching",
                "La casa de Asterión",
            ),
            "Léeme desde el cursor": ("from_cursor", ""),
            "Léeme donde termina la tarde": ("paragraph_matching", "donde termina la tarde"),
        }
        for utterance, expected in cases.items():
            with self.subTest(utterance=utterance):
                instruction = interpret_dictation_transcript(utterance)
                self.assertEqual((instruction.scope, instruction.target), expected)

    def test_audio_turn_reuses_local_stt_and_returns_instruction(self):
        app = test_app(stt=NullSTTProvider("Lucy, borrá piedra y escribí agua"))
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "dictation.webm"
            audio.write_bytes(b"audio")
            result = app.dictation_turn_audio(audio, mime="audio/webm")
        self.assertTrue(result["ok"])
        self.assertEqual(result["stt_provider"], "null_stt")
        self.assertEqual(result["instruction"]["kind"], "replace")

    def test_audio_turn_preserves_command_like_speech_without_wake_word(self):
        app = test_app(stt=NullSTTProvider("Borrá piedra y escribí agua"))
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "dictation.webm"
            audio.write_bytes(b"audio")
            result = app.dictation_turn_audio(audio, mime="audio/webm")
        self.assertEqual(result["instruction"]["kind"], "dictate")
        self.assertEqual(result["instruction"]["text"], "Borrá piedra y escribí agua")

    def test_dictation_speech_returns_a_human_error_when_tts_is_down(self):
        app = test_app(tts=FailingTTSProvider())
        result = app.dictation_speak("Texto seleccionado")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "dictation_speech_failed")
        self.assertEqual(result["technical_detail"], "tts_down")
        self.assertNotEqual(result["detail"], "request_failed")
        self.assertIn("No pude leer", result["detail"])

    def test_audio_turn_reports_stt_failure_without_touching_editor_state(self):
        app = test_app(stt=EmptyTranscriptSTTProvider())
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "dictation.webm"
            audio.write_bytes(b"audio")
            result = app.dictation_turn_audio(audio, mime="audio/webm")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "dictation_transcription_failed")


if __name__ == "__main__":
    unittest.main()
