from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from fusion_reader_v2.media import MediaJob
from fusion_reader_v2.services.media import MediaProcessingService


ROOT = Path(__file__).resolve().parents[2]


class MediaCorrectionContractTests(unittest.TestCase):
    def test_backend_wires_request_scoped_post_correction(self) -> None:
        route = (ROOT / "fusion_reader_v2/web/routes/media.py").read_text(encoding="utf-8")
        service = (ROOT / "fusion_reader_v2/services/media.py").read_text(encoding="utf-8")
        self.assertIn('include_post_correction=selected("post_correct", False)', route)
        self.assertIn('post_correct_transcript=selected("post_correct", False)', route)
        self.assertIn("correction_requested=bool(post_correct_transcript)", service)
        self.assertIn("_correct_transcript_paragraphs", service)

    def test_frontend_exposes_opt_in_checkbox(self) -> None:
        html = (ROOT / "fusion_reader_v2/web/static/index.html").read_text(encoding="utf-8")
        js = (ROOT / "fusion_reader_v2/web/static/js/media.mjs").read_text(encoding="utf-8")
        self.assertIn('id="mediaPostCorrectionToggle"', html)
        self.assertIn("params.set('post_correct', postCorrect ? '1' : '0')", js)

    def test_safe_rejection_still_completes_correction_stage(self) -> None:
        service = object.__new__(MediaProcessingService)
        job = MediaJob(job_id="job", operation="transcribe", filename="sample.wav", mime="audio/wav", voice="")
        outcomes = iter(
            [
                SimpleNamespace(accepted=True, changed=True, text="Hari Seldon", detail="accepted"),
                SimpleNamespace(accepted=True, changed=False, text="Trantor", detail="unchanged"),
                SimpleNamespace(accepted=False, changed=False, text="", detail="rewrite_risk"),
            ]
        )
        service.corrector = SimpleNamespace(correct=lambda *_args, **_kwargs: next(outcomes))
        service._check_cancelled = lambda _job_id: None
        service._job = lambda _job_id: job

        def update(_job_id: str, **changes) -> None:
            for key, value in changes.items():
                setattr(job, key, value)

        service._update = update
        paragraphs = [(0.0, "Harry Seldon"), (5.0, "Trantor"), (10.0, "Texto que Qwen intentó reescribir")]
        corrected = service._correct_transcript_paragraphs("job", paragraphs)

        self.assertTrue(job.correction_completed)
        self.assertEqual(job.correction_processed_paragraphs, 3)
        self.assertEqual(job.correction_accepted_paragraphs, 2)
        self.assertEqual(job.correction_unchanged_paragraphs, 1)
        self.assertEqual(job.correction_changed_paragraphs, 1)
        self.assertEqual(job.correction_rejected_paragraphs, 1)
        self.assertIn("asr_correction_partial", job.warnings)
        self.assertEqual(corrected[-1][1], paragraphs[-1][1])

        payload = job.to_dict()["correction"]
        self.assertTrue(payload["completed"])
        self.assertEqual(payload["processed_paragraphs"], 3)
        self.assertEqual(payload["accepted_paragraphs"], 2)
        self.assertEqual(payload["unchanged_paragraphs"], 1)
        self.assertEqual(payload["rejected_paragraphs"], 1)


if __name__ == "__main__":
    unittest.main()
