from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, content: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if content in text:
        return
    if marker not in text:
        raise SystemExit(f"{path}: marker not found: {marker[:80]!r}")
    target.write_text(text.replace(marker, marker + content, 1), encoding="utf-8")


# Media job state exposes whether the optional correction pass actually completed.
replace_once(
    "fusion_reader_v2/media.py",
    '    provider: str = ""\n    media_format: str = ""\n',
    '    provider: str = ""\n'
    '    correction_requested: bool = False\n'
    '    correction_completed: bool = False\n'
    '    correction_model: str = ""\n'
    '    correction_changed_paragraphs: int = 0\n'
    '    correction_rejected_paragraphs: int = 0\n'
    '    media_format: str = ""\n',
)
replace_once(
    "fusion_reader_v2/media.py",
    '            "provider": self.provider,\n            "media_format": self.media_format,\n',
    '            "provider": self.provider,\n'
    '            "correction": {\n'
    '                "requested": self.correction_requested,\n'
    '                "completed": self.correction_completed,\n'
    '                "model": self.correction_model,\n'
    '                "changed_paragraphs": self.correction_changed_paragraphs,\n'
    '                "rejected_paragraphs": self.correction_rejected_paragraphs,\n'
    '            },\n'
    '            "media_format": self.media_format,\n',
)

# Media service wiring.
replace_once(
    "fusion_reader_v2/services/media.py",
    'from fusion_reader_v2.tts import AudioArtifact\n',
    'from fusion_reader_v2.tts import AudioArtifact\n'
    'from fusion_reader_v2.transcript_correction import OllamaTranscriptCorrector\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '        self.chat = chat\n        self.synthesize = synthesize\n',
    '        self.chat = chat\n'
    '        self.corrector = OllamaTranscriptCorrector()\n'
    '        self.synthesize = synthesize\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '        include_spanish_audio: bool = False,\n        input_bytes: int = 0,\n',
    '        include_spanish_audio: bool = False,\n'
    '        include_post_correction: bool = False,\n'
    '        input_bytes: int = 0,\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '        tts = (\n'
    '            dict(self.tts_health() or {})\n'
    '            if normalized == "translate" and include_spanish_audio and self.tts_health is not None\n'
    '            else {"ok": True}\n'
    '        )\n'
    '        try:\n',
    '        tts = (\n'
    '            dict(self.tts_health() or {})\n'
    '            if normalized == "translate" and include_spanish_audio and self.tts_health is not None\n'
    '            else {"ok": True}\n'
    '        )\n'
    '        correction = (\n'
    '            self.corrector.health()\n'
    '            if include_post_correction\n'
    '            else {"ok": True, "requested": False, "model": self.corrector.model, "detail": "not_requested"}\n'
    '        )\n'
    '        try:\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '        if not tts.get("ok"):\n            errors.append("tts_not_available")\n',
    '        if not tts.get("ok"):\n'
    '            errors.append("tts_not_available")\n'
    '        if include_post_correction and not correction.get("ok"):\n'
    '            errors.append("asr_correction_not_available")\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '            "tts_not_available": "El servicio de voz no está disponible para generar el audio.",\n'
    '            "media_disk_space_low": "No hay al menos 512 MiB libres para procesar el archivo.",\n',
    '            "tts_not_available": "El servicio de voz no está disponible para generar el audio.",\n'
    '            "asr_correction_not_available": "Qwen 14B no está disponible para la corrección conservadora opcional.",\n'
    '            "media_disk_space_low": "No hay al menos 512 MiB libres para procesar el archivo.",\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '            "translation": chat,\n            "tts": tts,\n',
    '            "translation": chat,\n'
    '            "tts": tts,\n'
    '            "correction": correction,\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '        include_spanish_audio: bool | None = None,\n        stt_initial_prompt: str = "",\n',
    '        include_spanish_audio: bool | None = None,\n'
    '        post_correct_transcript: bool = False,\n'
    '        stt_initial_prompt: str = "",\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '            include_spanish_audio=audio_requested,\n            input_bytes=input_path.stat().st_size if input_path.exists() else 0,\n',
    '            include_spanish_audio=audio_requested,\n'
    '            include_post_correction=bool(post_correct_transcript),\n'
    '            input_bytes=input_path.stat().st_size if input_path.exists() else 0,\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '                spanish_audio_requested=audio_requested,\n            )\n',
    '                spanish_audio_requested=audio_requested,\n'
    '                correction_requested=bool(post_correct_transcript),\n'
    '                correction_model=self.corrector.model if post_correct_transcript else "",\n'
    '            )\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '            paragraphs = transcript_paragraphs(transcript.segments, transcript.text)\n'
    '            title = f"{Path(self._job(job_id).filename).stem} — Transcripción"\n',
    '            paragraphs = transcript_paragraphs(transcript.segments, transcript.text)\n'
    '            if self._job(job_id).correction_requested:\n'
    '                stage_started = time.perf_counter()\n'
    '                paragraphs = self._correct_transcript_paragraphs(\n'
    '                    job_id,\n'
    '                    paragraphs,\n'
    '                    context=stt_initial_prompt,\n'
    '                    glossary=stt_hotwords,\n'
    '                )\n'
    '                self._record_timing(job_id, "correction_ms", stage_started)\n'
    '                self._check_cancelled(job_id)\n'
    '            title = f"{Path(self._job(job_id).filename).stem} — Transcripción"\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '                    subtitle=f"Idioma detectado: {detected} · Generado localmente por Fusion Reader v2",\n',
    '                    subtitle=(\n'
    '                        f"Idioma detectado: {detected} · Generado localmente por Fusion Reader v2"\n'
    '                        + (\n'
    '                            f" · Corrección conservadora: {job.correction_model}"\n'
    '                            if job.correction_completed\n'
    '                            else ""\n'
    '                        )\n'
    '                    ),\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '                detail="Procesamiento terminado. Ya podés descargar o montar el resultado.",\n',
    '                detail=(\n'
    '                    "Procesamiento terminado con corrección conservadora local. "\n'
    '                    "Ya podés descargar o montar el resultado."\n'
    '                    if self._job(job_id).correction_completed\n'
    '                    else "Procesamiento terminado. Ya podés descargar o montar el resultado."\n'
    '                ),\n',
)
replace_once(
    "fusion_reader_v2/services/media.py",
    '    def _translate_and_synthesize(\n',
    '    def _correct_transcript_paragraphs(\n'
    '        self,\n'
    '        job_id: str,\n'
    '        paragraphs: list[tuple[float, str]],\n'
    '        *,\n'
    '        context: str = "",\n'
    '        glossary: str = "",\n'
    '    ) -> list[tuple[float, str]]:\n'
    '        corrected: list[tuple[float, str]] = []\n'
    '        total = max(1, len(paragraphs))\n'
    '        changed = 0\n'
    '        rejected = 0\n'
    '        accepted = 0\n'
    '        for index, (start, text) in enumerate(paragraphs, start=1):\n'
    '            self._check_cancelled(job_id)\n'
    '            self._update(\n'
    '                job_id,\n'
    '                stage="correcting",\n'
    '                progress=36 + int(index * 11 / total),\n'
    '                detail=f"Corrigiendo conservadoramente fragmento {index} de {total} con Qwen 14B...",\n'
    '            )\n'
    '            outcome = self.corrector.correct(text, context=context, glossary=glossary)\n'
    '            self._check_cancelled(job_id)\n'
    '            if outcome.accepted:\n'
    '                accepted += 1\n'
    '                changed += int(outcome.changed)\n'
    '                corrected.append((start, outcome.text))\n'
    '            else:\n'
    '                rejected += 1\n'
    '                corrected.append((start, text))\n'
    '                LOG.warning(\n'
    '                    "media job %s rejected ASR correction paragraph %s: %s",\n'
    '                    job_id,\n'
    '                    index,\n'
    '                    outcome.detail,\n'
    '                )\n'
    '        completed = bool(paragraphs) and accepted == len(paragraphs)\n'
    '        warnings = list(self._job(job_id).warnings)\n'
    '        if rejected and "asr_correction_partial" not in warnings:\n'
    '            warnings.append("asr_correction_partial")\n'
    '        self._update(\n'
    '            job_id,\n'
    '            correction_completed=completed,\n'
    '            correction_changed_paragraphs=changed,\n'
    '            correction_rejected_paragraphs=rejected,\n'
    '            warnings=warnings,\n'
    '        )\n'
    '        return corrected\n\n'
    '    def _translate_and_synthesize(\n',
)

# API query contract.
replace_once(
    "fusion_reader_v2/web/routes/media.py",
    '            include_spanish_audio=selected("spanish_audio", operation == "translate"),\n'
    '            input_bytes=input_bytes,\n',
    '            include_spanish_audio=selected("spanish_audio", operation == "translate"),\n'
    '            include_post_correction=selected("post_correct", False),\n'
    '            input_bytes=input_bytes,\n',
)
replace_once(
    "fusion_reader_v2/web/routes/media.py",
    '            include_spanish_audio=selected("spanish_audio", operation == "translate"),\n'
    '            stt_initial_prompt=str((params.get("stt_prompt") or [""])[-1]),\n',
    '            include_spanish_audio=selected("spanish_audio", operation == "translate"),\n'
    '            post_correct_transcript=selected("post_correct", False),\n'
    '            stt_initial_prompt=str((params.get("stt_prompt") or [""])[-1]),\n',
)

# UI: one opt-in checkbox in the existing per-file context card.
replace_once(
    "fusion_reader_v2/web/static/index.html",
    '          <p class="upload-info">Separalos con comas. Sirve para nombres propios, lugares, siglas y vocabulario técnico que el audio pueda confundir.</p>\n',
    '          <p class="upload-info">Separalos con comas. Sirve para nombres propios, lugares, siglas y vocabulario técnico que el audio pueda confundir.</p>\n'
    '          <label class="toggle upload-toggle"><input id="mediaPostCorrectionToggle" type="checkbox"> Corregir después con Qwen 14B</label>\n'
    '          <p class="upload-info">Segunda pasada opcional y conservadora, con thinking apagado. Si intenta reescribir demasiado, Panda conserva el fragmento de Whisper.</p>\n',
)
replace_once(
    "fusion_reader_v2/web/static/js/ui.mjs",
    "  'mediaSttPromptInput', 'mediaSttHotwordsInput',\n",
    "  'mediaSttPromptInput', 'mediaSttHotwordsInput', 'mediaPostCorrectionToggle',\n",
)
replace_once(
    "fusion_reader_v2/web/static/js/media.mjs",
    '      elements.mediaSttPromptInput,\n      elements.mediaSttHotwordsInput\n',
    '      elements.mediaSttPromptInput,\n'
    '      elements.mediaSttHotwordsInput,\n'
    '      elements.mediaPostCorrectionToggle\n',
)
replace_once(
    "fusion_reader_v2/web/static/js/media.mjs",
    '      params.set(\'file_bytes\', String(Number(file.size || 0)));\n',
    '      params.set(\'file_bytes\', String(Number(file.size || 0)));\n'
    '      const postCorrect = Boolean(elements.mediaPostCorrectionToggle && elements.mediaPostCorrectionToggle.checked);\n'
    '      params.set(\'post_correct\', postCorrect ? \'1\' : \'0\');\n',
)

# JS tests know about and verify the new request-scoped toggle.
replace_once(
    "tests/media_controller.test.js",
    '    mediaSttHotwordsInput: element(),\n    mediaPdfDownload: element(),\n',
    '    mediaSttHotwordsInput: element(),\n'
    '    mediaPostCorrectionToggle: element(),\n'
    '    mediaPdfDownload: element(),\n',
)
append_once(
    "tests/media_controller.test.js",
    "  controller.dispose();\n});\n",
    "\n\ntest('optional ASR post-correction is scoped to the selected upload and participates in preflight', async () => {\n"
    "  global.window = { setTimeout, clearTimeout };\n"
    "  const { createMediaController } = await import('../fusion_reader_v2/web/static/js/media.mjs');\n"
    "  const requests = [];\n"
    "  global.fetch = async (url, options) => {\n"
    "    requests.push({ url, options });\n"
    "    if (url.includes('/capabilities')) return { ok: true, async json() { return { ok: true }; } };\n"
    "    return { ok: true, async json() { return { ok: true, job_id: 'corrected', state: 'running', output: {} }; } };\n"
    "  };\n"
    "  const ui = elements();\n"
    "  ui.mediaPostCorrectionToggle.checked = true;\n"
    "  const controller = createMediaController({ elements: ui, log() {}, async refreshMainStatus() {} });\n"
    "  const file = new Blob(['audio']);\n"
    "  file.name = 'foundation.wav';\n"
    "  await controller.start('transcribe', file);\n"
    "  assert.equal(requests.length, 2);\n"
    "  assert.equal(new URL(requests[0].url, 'http://local').searchParams.get('post_correct'), '1');\n"
    "  assert.equal(new URL(requests[1].url, 'http://local').searchParams.get('post_correct'), '1');\n"
    "  assert.equal(ui.mediaPostCorrectionToggle.disabled, true);\n"
    "  controller.render({ job_id: 'corrected', state: 'done', output: {} });\n"
    "  assert.equal(ui.mediaPostCorrectionToggle.disabled, false);\n"
    "  controller.dispose();\n"
    "});\n",
)

# Configuration documentation only; the feature remains opt-in in the UI.
replace_once(
    ".env.example",
    '# FUSION_READER_STT_HOTWORD_MAX_ITEMS=128\n',
    '# FUSION_READER_STT_HOTWORD_MAX_ITEMS=128\n'
    '# Optional conservative second pass after Whisper. Enabled per file from the UI.\n'
    '# FUSION_READER_ASR_CORRECTOR_MODEL=qwen3:14b-q8_0\n'
    '# FUSION_READER_ASR_CORRECTOR_TIMEOUT=45\n'
    '# FUSION_READER_ASR_CORRECTOR_KEEP_ALIVE=1m\n',
)

print("ASR post-correction patch applied")
