import time
import unittest

from fusion_reader_v2.dialogue import FasterWhisperServerSTTProvider, strip_hallucinated_suffix


class DictationHallucinationFilterTests(unittest.TestCase):
    def test_known_latter_day_saints_suffix_is_removed_after_real_dictation(self):
        transcript = (
            "Hola, me escuchás? "
            "Este es el canal de subtítulos en español de la Iglesia de Jesucristo "
            "de los Santos de los Últimos Días. Gracias por ver."
        )
        self.assertEqual(strip_hallucinated_suffix(transcript), "Hola, me escuchás?")

    def test_server_provider_keeps_real_prefix_and_drops_hallucinated_suffix(self):
        provider = FasterWhisperServerSTTProvider()
        result = provider._result_from_data(
            {
                "ok": True,
                "text": (
                    "Probando el dictado. "
                    "Este es el canal de subtítulos en español de la Iglesia de Jesucristo "
                    "de los Santos de los Últimos Días."
                ),
                "provider": "faster_whisper_server",
            },
            time.perf_counter(),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "Probando el dictado.")

    def test_hallucination_without_real_prefix_is_rejected(self):
        provider = FasterWhisperServerSTTProvider()
        result = provider._result_from_data(
            {
                "ok": True,
                "text": (
                    "Este es el canal de subtítulos en español de la Iglesia de Jesucristo "
                    "de los Santos de los Últimos Días."
                ),
                "provider": "faster_whisper_server",
            },
            time.perf_counter(),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "hallucinated_transcript")


if __name__ == "__main__":
    unittest.main()
