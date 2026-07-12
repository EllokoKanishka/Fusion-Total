import unittest
import tempfile
import os
from pathlib import Path
from fusion_reader_v2 import (
    ConversationCore,
    NullChatProvider,
    NullSTTProvider,
)
from fusion_reader_v2.dialogue import is_hallucinated_transcript
from tests.helpers import (
    test_app,
    make_reading_document,
    make_reading_sections,
    EmptyTranscriptSTTProvider,
    BrokenSTTProvider,
    HallucinatedTranscriptSTTProvider,
    FailingChatProvider,
    NullTTSProvider,
    FailingTTSProvider,
)
from tests.helpers import web_source

class DialogueTests(unittest.TestCase):
    def test_dialogue_degrades_supreme_to_thinking_by_default(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        out = app.dialogue_turn_text("¿Qué opinás?")
        self.assertEqual(out["reasoning_mode_applied"], "thinking")
        self.assertTrue(out["reasoning_degraded"])

    def test_dialogue_degrades_contrapunto_to_thinking(self):
        app = test_app()
        app.set_reasoning_mode("contrapunto")
        out = app.dialogue_turn_text("¿?")
        self.assertEqual(out["reasoning_mode_applied"], "thinking")

    def test_dialogue_turn_text_answers_with_audio_without_touching_reader_tts_path(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_dialogue_ack = False
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("¿?")
        self.assertTrue(out["audio"])
        self.assertEqual(app.dialogue_status()["turns"], 2)

    def test_dialogue_turn_text_fast_ack_skips_tts(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_dialogue_ack = True
        app.dialogue_turn_text("¿?")
        self.assertEqual(provider.calls, [])

    def test_dialogue_stop_command_does_not_answer_again(self):
        chat_provider = NullChatProvider("No.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        out = app.dialogue_turn_text("detente")
        self.assertEqual(out["answer"], "")
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_turn_audio_uses_stt_provider(self):
        stt = NullSTTProvider("Hola.")
        app = test_app(stt=stt)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.wav"
        audio.write_bytes(b"RIFF")
        out = app.dialogue_turn_audio(audio)
        self.assertEqual(out["transcript"], "Hola.")

    def test_dialogue_empty_transcript_is_recoverable(self):
        app = test_app(stt=EmptyTranscriptSTTProvider())
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.wav"
        audio.write_bytes(b"RIFF")
        out = app.dialogue_turn_audio(audio)
        self.assertEqual(out["detail"], "empty_transcript")

    def test_dialogue_stt_failure_returns_human_answer_instead_of_silence(self):
        app = test_app(stt=BrokenSTTProvider())
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.wav"
        audio.write_bytes(b"RIFF")
        out = app.dialogue_turn_audio(audio)
        self.assertEqual(out["error"], "transcription_failed")
        self.assertIn("No pude entender", out["answer"])

    def test_dialogue_chat_failure_returns_human_answer_and_trace(self):
        app = test_app()
        app.conversation = ConversationCore(FailingChatProvider())
        out = app.dialogue_turn_text("¿?")
        self.assertEqual(out["failed_stage"], "chat")
        self.assertIn("Se cayó el diálogo local", out["answer"])

    def test_dialogue_turn_text_keeps_text_when_tts_fails(self):
        app = test_app(tts=FailingTTSProvider())
        out = app.dialogue_turn_text("¿?")
        self.assertTrue(out["ok"])
        self.assertFalse(out["voice_ok"])

    def test_dialogue_turn_text_defaults_to_neural_voice_not_browser_ack(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.dialogue_turn_text("¿?")
        self.assertEqual(provider.calls[0][1], "female_03.wav")

    def test_dialogue_note_command_defaults_to_neural_voice_not_browser_ack(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.dialogue_turn_text("tomá nota de detalle")
        self.assertEqual(provider.calls[0][1], "female_03.wav")

    def test_stt_filters_common_outro_hallucinations(self):
        self.assertTrue(is_hallucinated_transcript("¡Suscríbete!"))
        self.assertFalse(is_hallucinated_transcript("quiero anotar algo"))

    def test_dialogue_hallucinated_transcript_is_ignored_before_chat(self):
        chat_provider = NullChatProvider("No.")
        app = test_app(stt=HallucinatedTranscriptSTTProvider())
        app.conversation = ConversationCore(chat_provider)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.wav"
        audio.write_bytes(b"RIFF")
        out = app.dialogue_turn_audio(audio)
        self.assertTrue(out["ignored"])
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_reference_to_recent_reply_becomes_laboratory_note(self):
        app = test_app()
        app.dialogue_turn_text("¿?")
        out = app.dialogue_turn_text("Tomá nota de esto que acabás de decir.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")

    def test_dialogue_stt_like_recent_speech_note_becomes_laboratory_note(self):
        app = test_app()
        app.dialogue_turn_text("¿Me he escuchado?")
        out = app.dialogue_turn_text("Tomando a esto que acabo de decir.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")

    def test_dialogue_generic_eso_note_routes_to_laboratory(self):
        app = test_app()
        app.dialogue_turn_text("¿?")
        out = app.dialogue_turn_text("sí, tome nota de eso")
        self.assertEqual(out["note"]["source_kind"], "laboratory")

    def test_dialogue_short_stt_artifact_note_uses_recent_laboratory_content(self):
        app = test_app()
        app.dialogue_turn_text("¿?")
        out = app.dialogue_turn_text("Toma nota D.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")

    def test_dialogue_note_command_answers_with_audio(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_note_ack = False
        out = app.dialogue_turn_text("guardá nota de esto")
        self.assertTrue(out["audio"])

    def test_dialogue_note_command_succeeds_even_when_tts_fails(self):
        app = test_app(tts=FailingTTSProvider())
        out = app.dialogue_turn_text("nota")
        self.assertTrue(out["ok"])
        self.assertFalse(out["voice_ok"])

    def test_dialogue_note_command_fast_ack_skips_tts(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_note_ack = True
        app.dialogue_turn_text("tomá nota de esto")
        self.assertEqual(provider.calls, [])

    def test_dialogue_note_command_allows_intro_and_stt_variant(self):
        app = test_app()
        out = app.dialogue_turn_text("Estamos probando, tomad nota de la inquietud filosófica.")
        self.assertEqual(out["note"]["text"], "la inquietud filosófica")

    def test_dialogue_note_command_understands_save_the_note_phrase(self):
        app = test_app()
        out = app.dialogue_turn_text("me puedes guardar la nota de ontología")
        self.assertEqual(out["note"]["text"], "ontología")

    def test_dialogue_note_command_understands_followup_save_without_note_word(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        app.dialogue_turn_text("guardá esto como nota: primera idea")
        out = app.dialogue_turn_text("y guarda también la segunda idea de este mismo bloque")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "la segunda idea de este mismo bloque")
        self.assertEqual(len(app.list_notes()["items"]), 2)
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_note_command_understands_make_me_a_note(self):
        app = test_app()
        out = app.dialogue_turn_text("haceme una nota de esto")
        self.assertTrue(out["ok"])

    def test_dialogue_note_command_understands_leave_a_note(self):
        app = test_app()
        out = app.dialogue_turn_text("Deja una nota de esto")
        self.assertTrue(out["ok"])

    def test_dialogue_note_command_saves_previous_long_phrase(self):
        app = test_app()
        out = app.dialogue_turn_text("Frase larga. Guarda eso en una nota.")
        self.assertIn("Frase larga", out["note"]["text"])

    def test_dialogue_note_uses_visible_chunk_index_from_client(self):
        app = test_app()
        app.load_text("doc", "Doc", make_reading_document("Doc", 36), prefetch=False)
        app.jump(3)
        out = app.dialogue_turn_text("tomá nota de esto corresponde al bloque dos", chunk_index=1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["note"]["chunk_number"], 2)
        self.assertEqual(app.session.status()["current"], 3)

    def test_dialogue_search_no_matches_is_not_a_hard_failure(self):
        app = test_app()
        app.load_text("doc", "Doc", "Text", prefetch=False)
        out = app.dialogue_turn_text("buscá nada")
        self.assertEqual(out["detail"], "search_no_matches")

    def test_dialogue_compare_returns_reader_compare_without_llm(self):
        app = test_app()
        app.load_text("d", "P", "A\n\nB", prefetch=False)
        app.add_reference_text("r", "C", "D\n\nE")
        out = app.dialogue_turn_text("compará bloque 1 con bloque 1 del principal")
        self.assertEqual(out["model"], "reader_compare")

    def test_dialogue_reflective_block_request_sets_focus_and_continues_with_llm(self):
        chat_provider = NullChatProvider("Lucy piensa el bloque con vuelo propio.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", make_reading_document("Principal", 24), prefetch=False)
        app.add_reference_text(
            "ref",
            "ideas.docx",
            make_reading_sections(
                ("Ideas uno", "Primer bloque de consulta."),
                ("Ideas dos", "Bloque 2: la estadística del lenguaje revela un régimen de inteligibilidad."),
                ("Ideas tres", "Tercer bloque."),
            ),
        )
        out = app.dialogue_turn_text("Quiero que pensemos filosóficamente sobre el bloque 2 de ideas.docx")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "null")
        self.assertNotEqual(out["detail"], "focus_block")
        self.assertEqual(app.laboratory_focus_status()["chunk_number"], 2)
        self.assertEqual(app.laboratory_focus_status()["title"], "ideas.docx")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("FOCO ACTUAL DEL LABORATORIO:", prompt)
        self.assertIn("ideas.docx", prompt)
        self.assertIn("Ideas dos", prompt)

    def test_dialogue_context_does_not_send_full_document(self):
        chat_provider = NullChatProvider("Respuesta breve.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text(
            "doc",
            "Doc",
            make_reading_sections(
                ("Bloque uno", "Bloque uno."),
                ("Bloque dos", "Bloque dos visible."),
                ("Bloque tres", "Bloque tres final."),
            ),
            prefetch=False,
        )
        app.jump(2)
        out = app.dialogue_turn_text("¿Qué te parece?")
        self.assertTrue(out["ok"])
        messages = chat_provider.calls[0][0]
        joined = "\n".join(item["content"] for item in messages)
        self.assertIn("Bloque dos visible", joined)
        self.assertNotIn("DOCUMENTO COMPLETO DISPONIBLE", joined)
        self.assertIn("No digas que guardaste notas", messages[0]["content"])

    def test_dialogue_uses_recent_text_chat_laboratory_material(self):
        chat_provider = NullChatProvider(".")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.chat("Pasted")
        app.dialogue_turn_text("¿Ves?")
        full_prompt = "".join(str(m["content"]) for m in chat_provider.calls[1][0])
        self.assertIn("Pasted", full_prompt)

    def test_dialogue_shortener_closes_cut_text_without_ellipsis(self):
        app = test_app()
        app.dialogue_tts_max_chars = 10
        out = app._shorten_dialogue_answer("Frase que se corta.")
        self.assertTrue(out.endswith("."))
        self.assertNotIn("...", out)

    def test_dialogue_barge_in_keeps_pre_roll_for_short_commands(self):
        server = web_source()
        self.assertIn("interruptedWhileSpeech", server)

    def test_dialogue_status_reports_degraded_reasoning(self):
        app = test_app()
        app.set_reasoning_mode("supreme")
        self.assertTrue(app.dialogue_status()["dialogue_reasoning"]["degraded"])

    def test_dialogue_turn_audio_logs_provider_failure(self):
        app = test_app()
        app.conversation = ConversationCore(FailingChatProvider())
        out = app.dialogue_turn_audio(Path("/tmp/fake.wav"))
        self.assertEqual(out["failed_stage"], "chat")
        self.assertIn("Se cayó el diálogo local", out["answer"])

    def test_dialogue_audio_trace_keeps_microphone_diagnostics(self):
        app = test_app(stt=EmptyTranscriptSTTProvider())
        out = app.dialogue_turn_audio(Path("/tmp/fake.wav"), audio_meta={"mic_rms": "0.1"})
        self.assertEqual(out["trace"]["mic_rms"], 0.1)

    def test_auto_stt_falls_back_when_primary_is_unavailable(self):
        from fusion_reader_v2.dialogue import AutoSTTProvider
        primary = BrokenSTTProvider()
        fallback = NullSTTProvider("Hecho.")
        auto = AutoSTTProvider(primary=primary, fallback=fallback)
        self.assertEqual(auto.transcribe_file(Path("/tmp/x.wav")).text, "Hecho.")

    def test_auto_stt_falls_back_when_primary_returns_empty_transcript(self):
        from fusion_reader_v2.dialogue import AutoSTTProvider
        primary = EmptyTranscriptSTTProvider()
        fallback = NullSTTProvider("Hecho.")
        auto = AutoSTTProvider(primary=primary, fallback=fallback)
        self.assertEqual(auto.transcribe_file(Path("/tmp/x.wav")).text, "Hecho.")

    def test_whisper_cli_fallback_uses_known_homebrew_path(self):
        from fusion_reader_v2.dialogue import WhisperCliSTTProvider
        command = Path("/home/linuxbrew/.linuxbrew/bin/whisper")
        if not command.exists():
            self.skipTest("Homebrew whisper command is not installed on this host")
        previous = os.environ.get("FUSION_READER_STT_COMMAND")
        try:
            os.environ.pop("FUSION_READER_STT_COMMAND", None)
            provider = WhisperCliSTTProvider()
            self.assertEqual(provider.command, str(command))
            self.assertTrue(provider.health()["ok"])
        finally:
            if previous is None:
                os.environ.pop("FUSION_READER_STT_COMMAND", None)
            else:
                os.environ["FUSION_READER_STT_COMMAND"] = previous

    def test_auto_stt_does_not_fallback_for_hallucinated_primary(self):
        from fusion_reader_v2.dialogue import AutoSTTProvider
        primary = HallucinatedTranscriptSTTProvider()
        fallback = NullSTTProvider("No debe llegar acá.")
        auto = AutoSTTProvider(primary=primary, fallback=fallback)
        res = auto.transcribe_file(Path("/tmp/x.wav"))
        self.assertEqual(res.text, "¡Suscríbete!")
