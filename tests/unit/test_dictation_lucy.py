import unittest

from fusion_reader_v2.dictation import interpret_dictation_transcript


class LucyCompoundCommandsTests(unittest.TestCase):
    def test_swap_is_a_bounded_batch_not_literal_replacement_text(self):
        instruction = interpret_dictation_transcript(
            "Lucy, cambia verde por azul, azul por verde", require_wake_word=True
        )
        self.assertEqual(instruction.kind, "replace_many")
        self.assertEqual(instruction.replacements, (("verde", "azul"), ("azul", "verde")))

    def test_repeated_verbs_and_spanish_connector(self):
        instruction = interpret_dictation_transcript(
            "Lucy, cambiá verde por azul y reemplazá azul por verde.", require_wake_word=True
        )
        self.assertEqual(instruction.replacements, (("verde", "azul"), ("azul", "verde")))

    def test_quoted_separator_is_replacement_content(self):
        instruction = interpret_dictation_transcript(
            'Lucy, cambia verde por "azul, azul por verde"', require_wake_word=True
        )
        self.assertEqual(instruction.kind, "replace")
        self.assertEqual(instruction.text, "azul, azul por verde")

    def test_ambiguous_batch_is_not_partially_applied(self):
        instruction = interpret_dictation_transcript("Lucy, cambia verde por azul, borra todo", require_wake_word=True)
        self.assertEqual(instruction.kind, "noop")

    def test_command_disabled_remains_literal(self):
        instruction = interpret_dictation_transcript(
            "Lucy, cambia verde por azul, azul por verde", commands_enabled=False, require_wake_word=True
        )
        self.assertEqual(instruction.kind, "dictate")

    def test_plain_speech_without_wake_stays_literal(self):
        instruction = interpret_dictation_transcript("cambia verde por azul, azul por verde", require_wake_word=True)
        self.assertEqual(instruction.kind, "dictate")

    def test_quoted_target_may_contain_the_word_por(self):
        instruction = interpret_dictation_transcript(
            'Lucy, cambia "razón por la cual" por motivo', require_wake_word=True
        )
        self.assertEqual((instruction.target, instruction.text), ("razón por la cual", "motivo"))

    def test_bounds_and_unclosed_quotes_are_explicit_rejections(self):
        for command in [
            'Lucy, cambia verde por "azul',
            "Lucy, cambia verde por azul y borrá todo",
            "Lucy, cambia verde por azul, borra todo",
            "Lucy cambia " + "x" * 20001,
        ]:
            with self.subTest(command=command[:100]):
                instruction = interpret_dictation_transcript(command, require_wake_word=True)
                self.assertEqual((instruction.kind, instruction.scope), ("noop", "ambiguous_replacement"))

    def test_swap_survives_transcriber_punctuation_and_conjunction(self):
        for separator in (" y ", ". ", " y, ", ", "):
            with self.subTest(separator=separator):
                instruction = interpret_dictation_transcript(
                    f"Lucy, cambia azul por verde{separator}verde por azul.", require_wake_word=True
                )
                self.assertEqual(instruction.replacements, (("azul", "verde"), ("verde", "azul")))

    def test_incomplete_conjunction_cannot_insert_a_piece_of_the_order(self):
        instruction = interpret_dictation_transcript("Lucy, cambia azul por verde y.", require_wake_word=True)
        self.assertEqual((instruction.kind, instruction.scope), ("noop", "ambiguous_replacement"))
