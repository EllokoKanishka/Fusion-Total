from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, fields
from pathlib import Path
from typing import Protocol

from fusion_reader_v2.audio_export import concat_wav_files
from fusion_reader_v2.conversation import ChatProvider
from fusion_reader_v2.dialogue import (
    AutoSTTProvider,
    FasterWhisperServerSTTProvider,
    STTProvider,
    WhisperCliSTTProvider,
)
from fusion_reader_v2.domain.jobs import JobRegistry
from fusion_reader_v2.media import (
    MediaJob,
    clean_text,
    normalize_media_audio,
    probe_media,
    safe_media_stem,
    transcript_body_text,
    transcript_paragraphs,
    write_transcript_pdf,
)
from fusion_reader_v2.output_reservation import reserve_output_path
from fusion_reader_v2.reader import Document
from fusion_reader_v2.tts import AudioArtifact

LOG = logging.getLogger(__name__)
MEDIA_STT_PROMPT_MAX_CHARS = 1200
MEDIA_STT_HOTWORDS_MAX_CHARS = 2400


def _bounded_job_context(value: str | None, max_chars: int) -> str:
    return " ".join(str(value or "").split()).strip()[:max_chars]


class ReaderFacade(Protocol):
    def load_text(
        self,
        doc_id: str,
        title: str,
        text: str,
        prefetch: bool = True,
        source_path: str = "",
        source_type: str = "text",
    ) -> dict: ...


class MediaProcessingService:
    """Owns one bounded long-form media pipeline and its downloadable artifacts."""

    def __init__(
        self,
        *,
        reader: ReaderFacade,
        stt: STTProvider,
        chat: ChatProvider,
        synthesize: Callable[[str, str, str], AudioArtifact],
        tts_health: Callable[[], dict] | None = None,
        runtime_root: Path,
        converted_root: Path,
        output_root: Path,
        spawn: Callable[..., threading.Thread],
        timeout_seconds: float = 2 * 60 * 60,
        max_items: int = 256,
        ttl_seconds: float = 6 * 60 * 60,
        max_duration_seconds: float = 6 * 60 * 60,
        max_input_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        self.reader = reader
        self.stt = long_form_stt_provider(stt, timeout_seconds)
        self.chat = chat
        self.synthesize = synthesize
        self.tts_health = tts_health
        self.runtime_root = runtime_root
        self.converted_root = converted_root
        self.output_root = output_root
        self.manifest_root = output_root / ".manifests"
        self.manifest_root.mkdir(parents=True, exist_ok=True)
        self.spawn = spawn
        self.timeout_seconds = timeout_seconds
        self.ttl_seconds = ttl_seconds
        self.max_duration_seconds = max_duration_seconds
        self.max_input_bytes = max_input_bytes
        self.lock = threading.RLock()
        self.jobs: dict[str, MediaJob] = {}
        self.active_job_id = ""
        self.latest_job_id = ""
        self.cancel_events: dict[str, threading.Event] = {}
        self.deadlines: dict[str, float] = {}
        self.registry = JobRegistry(
            max_items=max_items,
            ttl_seconds=ttl_seconds,
            is_terminal=lambda job: job.terminal,
            updated_at=lambda job: job.updated_at,
            cleanup=self._cleanup_job,
            backing=self.jobs,
        )
        self._sweep_expired_storage()

    def capabilities(
        self,
        *,
        operation: str,
        include_translated_pdf: bool = False,
        include_spanish_audio: bool = False,
        input_bytes: int = 0,
    ) -> dict:
        normalized = str(operation or "").strip().lower()
        if normalized not in {"transcribe", "translate"}:
            return {"ok": False, "error": "media_operation_invalid"}
        dependencies = {
            "ffprobe": bool(shutil.which("ffprobe")),
            "ffmpeg": bool(shutil.which("ffmpeg")),
        }
        stt = dict(self.stt.health() or {})
        chat = (
            dict(self.chat.health() or {})
            if normalized == "translate" and (include_translated_pdf or include_spanish_audio)
            else {"ok": True}
        )
        tts = (
            dict(self.tts_health() or {})
            if normalized == "translate" and include_spanish_audio and self.tts_health is not None
            else {"ok": True}
        )
        try:
            free_bytes = shutil.disk_usage(self.runtime_root.parent).free
        except OSError:
            free_bytes = 0
        errors: list[str] = []
        with self.lock:
            active = self.jobs.get(self.active_job_id)
            busy = bool(active and not active.terminal)
        if busy:
            errors.append("media_processing_busy")
        if input_bytes > self.max_input_bytes:
            errors.append("media_too_large")
        if not dependencies["ffprobe"]:
            errors.append("ffprobe_not_available")
        if not dependencies["ffmpeg"]:
            errors.append("ffmpeg_not_available")
        if not stt.get("ok"):
            errors.append("stt_not_available")
        if not chat.get("ok"):
            errors.append("translation_not_available")
        if not tts.get("ok"):
            errors.append("tts_not_available")
        required_free_bytes = max(512 * 1024 * 1024, max(0, int(input_bytes)) * 3 + 256 * 1024 * 1024)
        if free_bytes and free_bytes < required_free_bytes:
            errors.append("media_disk_space_low")
        detail = {
            "media_processing_busy": "Ya hay un audio o video procesándose.",
            "media_too_large": "El archivo supera el límite multimedia configurado.",
            "ffprobe_not_available": "Falta FFprobe para inspeccionar el archivo.",
            "ffmpeg_not_available": "Falta FFmpeg para extraer el audio.",
            "stt_not_available": "Whisper no está disponible. Iniciá el servidor STT o instalá el fallback local.",
            "translation_not_available": "El modelo local de traducción no está disponible.",
            "tts_not_available": "El servicio de voz no está disponible para generar el audio.",
            "media_disk_space_low": "No hay al menos 512 MiB libres para procesar el archivo.",
        }
        return {
            "ok": not errors,
            "operation": normalized,
            "errors": errors,
            "error": errors[0] if errors else "",
            "detail": detail.get(errors[0], "Listo para procesar localmente.")
            if errors
            else "Listo para procesar localmente.",
            "dependencies": dependencies,
            "stt": stt,
            "translation": chat,
            "tts": tts,
            "free_bytes": free_bytes,
            "required_free_bytes": required_free_bytes,
            "max_duration_seconds": self.max_duration_seconds,
            "max_input_bytes": self.max_input_bytes,
        }

    def start(
        self,
        *,
        operation: str,
        filename: str,
        mime: str,
        input_path: Path,
        voice: str,
        include_original_pdf: bool = True,
        include_translated_pdf: bool | None = None,
        include_spanish_audio: bool | None = None,
        stt_initial_prompt: str = "",
        stt_hotwords: str = "",
    ) -> dict:
        normalized = str(operation or "").strip().lower()
        if normalized not in {"transcribe", "translate"}:
            input_path.unlink(missing_ok=True)
            return {"ok": False, "error": "media_operation_invalid"}
        original_requested = bool(include_original_pdf)
        translated_requested = normalized == "translate" and (
            True if include_translated_pdf is None else bool(include_translated_pdf)
        )
        audio_requested = normalized == "translate" and (
            True if include_spanish_audio is None else bool(include_spanish_audio)
        )
        if not any((original_requested, translated_requested, audio_requested)):
            input_path.unlink(missing_ok=True)
            return {
                "ok": False,
                "error": "media_output_required",
                "detail": "Elegí al menos una salida para procesar.",
            }
        readiness = self.capabilities(
            operation=normalized,
            include_translated_pdf=translated_requested,
            include_spanish_audio=audio_requested,
            input_bytes=input_path.stat().st_size if input_path.exists() else 0,
        )
        if not readiness.get("ok"):
            input_path.unlink(missing_ok=True)
            return readiness
        job_prompt = _bounded_job_context(stt_initial_prompt, MEDIA_STT_PROMPT_MAX_CHARS)
        job_hotwords = _bounded_job_context(stt_hotwords, MEDIA_STT_HOTWORDS_MAX_CHARS)
        with self.lock:
            active = self.jobs.get(self.active_job_id)
            if active and not active.terminal:
                input_path.unlink(missing_ok=True)
                return {
                    "ok": False,
                    "error": "media_processing_busy",
                    "detail": "Ya hay un audio o video procesándose.",
                }
            job = MediaJob(
                job_id=uuid.uuid4().hex[:16],
                operation=normalized,
                filename=Path(filename).name,
                mime=str(mime or "application/octet-stream"),
                voice=str(voice or ""),
                original_pdf_requested=original_requested,
                translated_pdf_requested=translated_requested,
                spanish_audio_requested=audio_requested,
            )
            self.registry.add(job.job_id, job)
            self.cancel_events[job.job_id] = threading.Event()
            self.deadlines[job.job_id] = time.monotonic() + self.timeout_seconds
            self.active_job_id = job.job_id
            self.latest_job_id = job.job_id
            self._persist_job(job)
        try:
            self.spawn(
                target=self._worker,
                args=(job.job_id, input_path, job_prompt, job_hotwords),
                name=f"fusion-media-{normalized}-{job.job_id}",
            )
        except Exception:
            input_path.unlink(missing_ok=True)
            with self.lock:
                self.registry.remove(job.job_id)
                if self.active_job_id == job.job_id:
                    self.active_job_id = ""
            raise
        return job.to_dict()

    def overview(self) -> dict:
        with self.lock:
            self.registry.prune()
            job_id = self.active_job_id or self.latest_job_id
            job = self.jobs.get(job_id) if job_id else None
            if not job:
                job = self._latest_persisted_job()
            return job.to_dict() if job else self._idle_status()

    def status(self, job_id: str) -> dict:
        with self.lock:
            normalized = str(job_id or "").strip()
            job = self.registry.get(normalized) or self._load_persisted_job(normalized)
            return job.to_dict() if job else {"ok": False, "error": "media_job_not_found"}

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            normalized = str(job_id or "").strip()
            job = self.registry.get(normalized) or self._load_persisted_job(normalized)
            if not job:
                return {"ok": False, "error": "media_job_not_found"}
            if job.terminal:
                self.registry.remove(job.job_id)
                (self.manifest_root / f"{job.job_id}.json").unlink(missing_ok=True)
                if self.active_job_id == job.job_id:
                    self.active_job_id = ""
                if self.latest_job_id == job.job_id:
                    self.latest_job_id = ""
                return self._idle_status()
            job.cancel_requested = True
            event = self.cancel_events.setdefault(job.job_id, threading.Event())
            event.set()
            job.state = "canceling"
            job.detail = "Cancelando procesamiento..."
            job.updated_at = time.time()
            self._persist_job(job)
            result = job.to_dict()
        self.stt.cancel(normalized)
        return result

    def mount(self, job_id: str) -> dict:
        with self.lock:
            normalized = str(job_id or "").strip()
            job = self.registry.get(normalized) or self._load_persisted_job(normalized)
            if not job:
                return {"ok": False, "error": "media_job_not_found"}
            if job.state not in {"done", "partial"} or not (job.translated_text or job.transcript):
                return {"ok": False, "error": "media_job_not_ready"}
            mount_translation = bool(job.translated_text)
            text = job.translated_text if mount_translation else job.transcript
            title_suffix = " — castellano" if mount_translation else " — transcripción"
            title = f"{Path(job.filename).stem}{title_suffix}"
        self.converted_root.mkdir(parents=True, exist_ok=True)
        filename = f"{safe_media_stem(job.filename)}_{job.job_id}{'_es' if mount_translation else ''}.txt"
        reservation = reserve_output_path(self.converted_root, filename, default_suffix=".txt")
        temporary = reservation.path.with_name(f".{reservation.path.name}.{uuid.uuid4().hex}.part")
        try:
            temporary.write_text(text, encoding="utf-8")
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            target = reservation.publish(temporary)
        finally:
            reservation.cleanup()
            temporary.unlink(missing_ok=True)
        result = self.reader.load_text(
            f"media-{job.job_id}",
            title,
            text,
            prefetch=False,
            source_path=str(target),
            source_type="media_translation" if mount_translation else "media_transcript",
        )
        with self.lock:
            current = self.jobs.get(job.job_id)
            if current:
                current.mounted = True
                current.translated_path = str(target) if mount_translation else current.translated_path
                current.transcript_path = str(target) if not mount_translation else current.transcript_path
                current.updated_at = time.time()
                self._persist_job(current)
        return {**result, "media_job_id": job.job_id, "mounted": True}

    def artifact(self, job_id: str, kind: str) -> dict:
        with self.lock:
            normalized = str(job_id or "").strip()
            job = self.registry.get(normalized) or self._load_persisted_job(normalized)
            if not job:
                return {"ok": False, "error": "media_job_not_found"}
            paths = {
                "pdf": job.pdf_path,
                "translated-pdf": job.translated_pdf_path,
                "audio": job.audio_path,
            }
            raw = paths.get(str(kind or "")) or ""
            if not raw:
                return {"ok": False, "error": "media_artifact_not_ready"}
            path = Path(raw)
        if path.is_symlink():
            return {"ok": False, "error": "media_artifact_invalid"}
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(self.output_root.resolve())
        except (OSError, ValueError):
            return {"ok": False, "error": "media_artifact_invalid"}
        if not resolved.is_file():
            return {"ok": False, "error": "media_artifact_invalid"}
        return {"ok": True, "path": str(resolved), "filename": resolved.name, "kind": kind}

    def request_shutdown(self) -> None:
        with self.lock:
            for job in self.jobs.values():
                if not job.terminal:
                    job.cancel_requested = True
                    self.cancel_events.setdefault(job.job_id, threading.Event()).set()
                    job.state = "canceling"
                    job.detail = "Cancelando por cierre de Fusion Reader..."
                    job.updated_at = time.time()
                    self.stt.cancel(job.job_id)

    def _worker(
        self,
        job_id: str,
        input_path: Path,
        stt_initial_prompt: str = "",
        stt_hotwords: str = "",
    ) -> None:
        work_root = self.runtime_root / job_id
        normalized = work_root / "normalized.flac"
        work_root.mkdir(parents=True, exist_ok=True)
        try:
            signal = _MediaSignal(self.cancel_events[job_id], self.deadlines[job_id])
            pipeline_started = time.perf_counter()
            self._update(job_id, state="running", stage="inspecting", progress=2, detail="Inspeccionando el archivo...")
            stage_started = time.perf_counter()
            probe = probe_media(input_path, cancel_event=signal)
            self._record_timing(job_id, "probe_ms", stage_started)
            if probe.duration_seconds > self.max_duration_seconds:
                raise RuntimeError("media_duration_exceeded")
            self._update(
                job_id,
                duration_seconds=probe.duration_seconds,
                media_format=probe.format_name,
                audio_codec=probe.audio_codec,
            )
            self._check_cancelled(job_id)
            self._update(
                job_id,
                stage="normalizing",
                progress=8,
                detail="Extrayendo y normalizando el audio con FFmpeg...",
            )
            stage_started = time.perf_counter()
            normalize_media_audio(input_path, normalized, timeout_seconds=self._remaining(job_id), cancel_event=signal)
            self._record_timing(job_id, "normalize_ms", stage_started)
            self._check_cancelled(job_id)
            self._update(job_id, stage="transcribing", progress=18, detail="Transcribiendo con Whisper...")
            stage_started = time.perf_counter()
            context_kwargs: dict[str, str] = {}
            if stt_initial_prompt:
                context_kwargs["initial_prompt"] = stt_initial_prompt
            if stt_hotwords:
                context_kwargs["hotwords"] = stt_hotwords
            transcript = self.stt.transcribe_file_cancellable(
                normalized,
                mime="audio/flac",
                language="auto",
                cancel_event=signal,
                request_id=job_id,
                long_form=True,
                **context_kwargs,
            )
            self._record_timing(job_id, "stt_ms", stage_started)
            self._check_cancelled(job_id)
            if not transcript.ok or not clean_text(transcript.text):
                if transcript.detail == "cancelled":
                    raise _MediaCancelled
                raise RuntimeError(transcript.detail or "media_transcription_failed")
            detected = str(transcript.detected_language or "").strip().lower() or "desconocido"
            paragraphs = transcript_paragraphs(transcript.segments, transcript.text)
            title = f"{Path(self._job(job_id).filename).stem} — Transcripción"
            transcript_text = transcript_body_text(paragraphs)
            transcript_file = work_root / "transcript.txt"
            transcript_file.write_text(transcript_text, encoding="utf-8")
            self._update(
                job_id,
                detected_language=detected,
                provider=transcript.provider,
                timings={**self._job(job_id).timings, **dict(transcript.timings or {})},
                transcript=transcript_text,
                paragraph_count=len(paragraphs),
                transcript_path=str(transcript_file),
                stage="building_pdf",
                progress=48,
                detail="Generando el PDF de la transcripción...",
            )
            job = self._job(job_id)
            if job.original_pdf_requested:
                pdf_path = self._publish_pdf(
                    job_id,
                    work_root,
                    title=title,
                    subtitle=f"Idioma detectado: {detected} · Generado localmente por Fusion Reader v2",
                    paragraphs=paragraphs,
                    suffix="transcripcion",
                )
                self._update(
                    job_id,
                    pdf_path=str(pdf_path),
                    pdf_download_url=f"/api/media/download/{job_id}/pdf",
                    progress=58,
                )
            else:
                self._update(job_id, progress=58, detail="Transcripción original preparada.")
            self._check_cancelled(job_id)
            if job.operation == "translate" and (job.translated_pdf_requested or job.spanish_audio_requested):
                self._translate_and_synthesize(job_id, work_root, paragraphs, detected)
            self._update(
                job_id,
                state="done",
                stage="done",
                progress=100,
                detail="Procesamiento terminado. Ya podés descargar o montar el resultado.",
                timings={**self._job(job_id).timings, "total_ms": int((time.perf_counter() - pipeline_started) * 1000)},
            )
        except _MediaCancelled:
            self._remove_artifacts(job_id)
            self._update(
                job_id,
                state="cancelled",
                stage="cancelled",
                detail="Procesamiento cancelado.",
            )
        except _MediaTimeout:
            self.stt.cancel(job_id)
            self._finish_failure(job_id, "media_timeout", "El procesamiento superó el tiempo máximo configurado.")
        except Exception as exc:
            if self._signal(job_id).timed_out:
                self._finish_failure(job_id, "media_timeout", "El procesamiento superó el tiempo máximo configurado.")
            elif self._signal(job_id).cancelled:
                self._remove_artifacts(job_id)
                self._update(job_id, state="cancelled", stage="cancelled", detail="Procesamiento cancelado.")
            else:
                code = self._error_code(exc)
                if code == "media_processing_failed":
                    LOG.exception("media job %s failed at %s", job_id, self._job(job_id).stage)
                else:
                    LOG.warning("media job %s failed at %s: %s", job_id, self._job(job_id).stage, code)
                self._finish_failure(job_id, code, "No pude completar todo el procesamiento.")
        finally:
            input_path.unlink(missing_ok=True)
            shutil.rmtree(work_root, ignore_errors=True)
            with self.lock:
                if self.active_job_id == job_id:
                    self.active_job_id = ""
                self.cancel_events.pop(job_id, None)
                self.deadlines.pop(job_id, None)

    def _translate_and_synthesize(
        self,
        job_id: str,
        work_root: Path,
        paragraphs: list[tuple[float, str]],
        detected_language: str,
    ) -> None:
        translated: list[tuple[float, str]] = []
        total = max(1, len(paragraphs))
        spanish = detected_language.startswith("es")
        for index, (start, text) in enumerate(paragraphs, start=1):
            self._check_cancelled(job_id)
            self._update(
                job_id,
                stage="translating",
                progress=58 + int(index * 17 / total),
                detail=f"Traduciendo fragmento {index} de {total} al castellano...",
            )
            translated.append((start, text if spanish else self._translate_paragraph(text, detected_language)))
            self._check_cancelled(job_id)
        title = f"{Path(self._job(job_id).filename).stem} — Traducción al castellano"
        translated_text = transcript_body_text(translated)
        translated_file = work_root / "translated_es.txt"
        translated_file.write_text(translated_text, encoding="utf-8")
        self._update(job_id, translated_text=translated_text, translated_path=str(translated_file))
        job = self._job(job_id)
        if job.translated_pdf_requested:
            translated_pdf = self._publish_pdf(
                job_id,
                work_root,
                title=title,
                subtitle=f"Traducción desde {detected_language} · Generada localmente por Fusion Reader v2",
                paragraphs=translated,
                suffix="castellano",
            )
            self._update(
                job_id,
                translated_pdf_path=str(translated_pdf),
                translated_pdf_download_url=f"/api/media/download/{job_id}/translated-pdf",
                progress=76,
            )
        if not job.spanish_audio_requested:
            return
        self._update(
            job_id,
            stage="synthesizing",
            progress=76,
            detail="Generando el audio en castellano...",
        )
        chunks = Document.from_text(f"media-{job_id}", title, "\n\n".join(text for _, text in translated)).chunks
        self._update(job_id, audio_chunk_count=len(chunks))
        wavs: list[Path] = []
        total_chunks = max(1, len(chunks))
        voice = self._job(job_id).voice
        for index, chunk in enumerate(chunks, start=1):
            self._check_cancelled(job_id)
            self._update(
                job_id,
                progress=76 + int(index * 21 / total_chunks),
                detail=f"Generando audio {index} de {total_chunks} con {voice or 'la voz seleccionada'}...",
            )
            artifact = self.synthesize(chunk, voice, "es")
            self._check_cancelled(job_id)
            if not artifact.ok or not artifact.path or not artifact.path.exists():
                raise RuntimeError(artifact.detail or "media_tts_failed")
            wavs.append(Path(artifact.path))
        if not wavs:
            raise RuntimeError("media_tts_empty")
        reservation = reserve_output_path(
            self.output_root,
            f"{safe_media_stem(self._job(job_id).filename)}_castellano.wav",
            default_suffix=".wav",
        )
        temporary = work_root / "castellano.part.wav"
        try:
            if len(wavs) == 1:
                shutil.copyfile(wavs[0], temporary)
            else:
                concat_wav_files(wavs, temporary)
            target = reservation.publish(temporary)
        finally:
            reservation.cleanup()
            temporary.unlink(missing_ok=True)
        self._update(
            job_id,
            audio_path=str(target),
            audio_download_url=f"/api/media/download/{job_id}/audio",
        )

    def _translate_paragraph(self, text: str, source_language: str) -> str:
        result = self.chat.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Traducí fielmente al castellano rioplatense neutro el fragmento recibido. "
                        "Conservá nombres propios, citas, tecnicismos y sentido académico. "
                        "No resumas, no expliques, no agregues encabezados y devolvé solamente la traducción."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Idioma de origen detectado: {source_language}.\n\n{text}",
                },
            ],
            think=False,
            num_predict=max(512, min(2048, len(text) * 2)),
        )
        translated = clean_text(result.answer)
        if not result.ok or not translated:
            raise RuntimeError(result.detail or "media_translation_failed")
        return translated

    def _publish_pdf(
        self,
        job_id: str,
        work_root: Path,
        *,
        title: str,
        subtitle: str,
        paragraphs: list[tuple[float, str]],
        suffix: str,
    ) -> Path:
        temporary = work_root / f"{suffix}.part.pdf"
        write_transcript_pdf(temporary, title=title, subtitle=subtitle, paragraphs=paragraphs)
        reservation = reserve_output_path(
            self.output_root,
            f"{safe_media_stem(self._job(job_id).filename)}_{suffix}.pdf",
            default_suffix=".pdf",
        )
        try:
            return reservation.publish(temporary)
        finally:
            reservation.cleanup()
            temporary.unlink(missing_ok=True)

    def _job(self, job_id: str) -> MediaJob:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise RuntimeError("media_job_not_found")
            return job

    def _update(self, job_id: str, **changes) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = time.time()
            durable_fields = {
                "state",
                "transcript",
                "translated_text",
                "pdf_path",
                "translated_pdf_path",
                "audio_path",
                "mounted",
            }
            if job.state in {"error", "cancelled"}:
                (self.manifest_root / f"{job.job_id}.json").unlink(missing_ok=True)
            elif job.terminal or durable_fields.intersection(changes):
                self._persist_job(job)

    def _persist_job(self, job: MediaJob) -> None:
        self.manifest_root.mkdir(parents=True, exist_ok=True)
        target = self.manifest_root / f"{job.job_id}.json"
        temporary = target.with_suffix(".json.part")
        payload = {"schema": 1, "job": asdict(job)}
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def _load_persisted_job(self, job_id: str) -> MediaJob | None:
        if not re.fullmatch(r"[0-9a-f]{16}", job_id):
            return None
        try:
            payload = json.loads((self.manifest_root / f"{job_id}.json").read_text(encoding="utf-8"))
            raw = payload.get("job") if isinstance(payload, dict) else None
            if not isinstance(raw, dict):
                return None
            allowed = {item.name for item in fields(MediaJob)}
            job = MediaJob(**{key: value for key, value in raw.items() if key in allowed})
            if job.job_id != job_id:
                return None
            if job.state not in {"done", "partial"} or time.time() - job.updated_at > self.ttl_seconds:
                (self.manifest_root / f"{job_id}.json").unlink(missing_ok=True)
                return None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        self.registry.add(job.job_id, job)
        self.latest_job_id = job.job_id
        return job

    def _latest_persisted_job(self) -> MediaJob | None:
        try:
            candidates = sorted(
                self.manifest_root.glob("*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        for candidate in candidates:
            job = self._load_persisted_job(candidate.stem)
            if job:
                return job
        return None

    def _check_cancelled(self, job_id: str) -> None:
        signal = self._signal(job_id)
        if signal.timed_out:
            raise _MediaTimeout
        if signal.cancelled or self._job(job_id).cancel_requested:
            raise _MediaCancelled

    def _cleanup_job(self, job: MediaJob) -> None:
        root = self.runtime_root / job.job_id
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        (self.manifest_root / f"{job.job_id}.json").unlink(missing_ok=True)

    def _signal(self, job_id: str) -> "_MediaSignal":
        return _MediaSignal(
            self.cancel_events.setdefault(job_id, threading.Event()),
            self.deadlines.get(job_id, time.monotonic() + self.timeout_seconds),
        )

    def _remaining(self, job_id: str) -> float:
        remaining = self.deadlines.get(job_id, time.monotonic()) - time.monotonic()
        if remaining <= 0:
            raise _MediaTimeout
        return remaining

    def _record_timing(self, job_id: str, name: str, started: float) -> None:
        timings = dict(self._job(job_id).timings)
        timings[name] = int((time.perf_counter() - started) * 1000)
        self._update(job_id, timings=timings)

    def _remove_artifacts(self, job_id: str) -> None:
        job = self._job(job_id)
        for raw in (job.pdf_path, job.translated_pdf_path, job.audio_path):
            if raw:
                Path(raw).unlink(missing_ok=True)
        self._update(
            job_id,
            pdf_path="",
            translated_pdf_path="",
            audio_path="",
            pdf_download_url="",
            translated_pdf_download_url="",
            audio_download_url="",
        )

    def _finish_failure(self, job_id: str, code: str, detail: str) -> None:
        job = self._job(job_id)
        usable = bool(
            job.transcript or job.translated_text or job.pdf_path or job.translated_pdf_path or job.audio_path
        )
        self._update(
            job_id,
            state="partial" if usable else "error",
            stage="partial" if usable else "error",
            detail=(f"{detail} Conservé los resultados que sí terminaron." if usable else detail),
            error=code,
            warnings=[*job.warnings, code] if usable else job.warnings,
        )

    @staticmethod
    def _error_code(exc: Exception) -> str:
        candidate = str(exc or "").strip()
        return candidate if re.fullmatch(r"[a-z0-9_]{3,80}", candidate) else "media_processing_failed"

    def _sweep_expired_storage(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for manifest in self.manifest_root.glob("*.json"):
            try:
                if manifest.stat().st_mtime < cutoff:
                    manifest.unlink(missing_ok=True)
            except OSError:
                continue
        if self.runtime_root.exists():
            for root in self.runtime_root.iterdir():
                try:
                    if root.is_dir() and root.stat().st_mtime < cutoff:
                        shutil.rmtree(root, ignore_errors=True)
                except OSError:
                    continue

    @staticmethod
    def _idle_status() -> dict:
        return {
            "ok": True,
            "job_id": "",
            "type": "media_processing",
            "operation": "",
            "state": "idle",
            "stage": "idle",
            "progress": 0,
            "detail": "Sin procesamiento multimedia activo.",
            "terminal": True,
            "output": {},
        }


class _MediaCancelled(Exception):
    pass


class _MediaTimeout(Exception):
    pass


class _MediaSignal:
    def __init__(self, event: threading.Event, deadline: float) -> None:
        self.event = event
        self.deadline = deadline

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    @property
    def timed_out(self) -> bool:
        return time.monotonic() >= self.deadline

    def is_set(self) -> bool:
        return self.cancelled or self.timed_out


def long_form_stt_provider(provider: STTProvider, timeout_seconds: float) -> STTProvider:
    if isinstance(provider, AutoSTTProvider):
        return AutoSTTProvider(
            primary=long_form_stt_provider(provider.primary, timeout_seconds),
            fallback=long_form_stt_provider(provider.fallback, timeout_seconds),
        )
    if isinstance(provider, FasterWhisperServerSTTProvider):
        return FasterWhisperServerSTTProvider(base_url=provider.base_url, timeout_seconds=timeout_seconds)
    if isinstance(provider, WhisperCliSTTProvider):
        return WhisperCliSTTProvider(
            command=provider.command,
            model=provider.model,
            timeout_seconds=timeout_seconds,
            threads=provider.threads,
        )
    return provider


__all__ = ["MediaProcessingService", "long_form_stt_provider"]
