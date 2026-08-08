import tempfile
import unittest
from pathlib import Path

from fusion_reader_v2 import NullSTTProvider, interpret_dictation_transcript
from tests.helpers import EmptyTranscriptSTTProvider, test_app


class DictationInstructionTests(unittest.TestCase):
    def test_plain_utterance_remains_dictation(self):
        instruction = interpret_dictation_transcript("La memoria no es un archivo inmóvil.")
        self.assertEqual(instruction.kind, "dictate")
        self.assertEqual(instruction.text, "La memoria no es un archivo inmóvil.")

    def test_commands_can_be_disabled_to_remove_ambiguity(self):
        instruction = interpret_dictation_transcript("Borrá la palabra memoria", commands_enabled=False)
        self.assertEqual(instruction.kind, "dictate")

    def test_delete_and_write_becomes_bounded_replacement(self):
        instruction = interpret_dictation_transcript("No, borrá archivo inmóvil y escribí tejido vivo")
        self.assertEqual(instruction.kind, "replace")
        self.assertEqual(instruction.target, "archivo inmóvil")
        self.assertEqual(instruction.text, "tejido vivo")

    def test_replace_delete_undo_and_redo_commands(self):
        replace = interpret_dictation_transcript("Reemplazá Borges por Spinoza")
        delete = interpret_dictation_transcript("Borrá todas las veces que aparece quizá")
        self.assertEqual((replace.kind, replace.target, replace.text), ("replace", "Borges", "Spinoza"))
        self.assertEqual((delete.kind, delete.target, delete.all_matches), ("delete", "quizá", True))
        self.assertEqual(interpret_dictation_transcript("deshacer").kind, "undo")
        self.assertEqual(interpret_dictation_transcript("rehacé").kind, "redo")

    def test_read_commands_keep_scope_and_target(self):
        self.assertEqual(interpret_dictation_transcript("Léeme la última hoja").scope, "last_page")
        paragraph = interpret_dictation_transcript("Léeme el párrafo número 3")
        self.assertEqual((paragraph.scope, paragraph.number), ("paragraph_number", 3))
        start = interpret_dictation_transcript("Léeme a partir de el jardín de senderos")
        self.assertEqual((start.scope, start.target), ("from_text", "el jardín de senderos"))

    def test_audio_turn_reuses_local_stt_and_returns_instruction(self):
        app = test_app(stt=NullSTTProvider("Borrá piedra y escribí agua"))
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "dictation.webm"
            audio.write_bytes(b"audio")
            result = app.dictation_turn_audio(audio, mime="audio/webm")
        self.assertTrue(result["ok"])
        self.assertEqual(result["stt_provider"], "null_stt")
        self.assertEqual(result["instruction"]["kind"], "replace")

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
