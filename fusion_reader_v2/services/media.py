from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
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
    transcript_document_text,
    transcript_paragraphs,
    write_transcript_pdf,
)
from fusion_reader_v2.output_reservation import reserve_output_path
from fusion_reader_v2.reader import Document
from fusion_reader_v2.tts import AudioArtifact


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
        runtime_root: Path,
        converted_root: Path,
        output_root: Path,
        spawn: Callable[..., threading.Thread],
        timeout_seconds: float = 2 * 60 * 60,
        max_items: int = 256,
        ttl_seconds: float = 6 * 60 * 60,
    ) -> None:
        self.reader = reader
        self.stt = long_form_stt_provider(stt, timeout_seconds)
        self.chat = chat
        self.synthesize = synthesize
        self.runtime_root = runtime_root
        self.converted_root = converted_root
        self.output_root = output_root
        self.spawn = spawn
        self.timeout_seconds = timeout_seconds
        self.lock = threading.RLock()
        self.jobs: dict[str, MediaJob] = {}
        self.active_job_id = ""
        self.latest_job_id = ""
        self.registry = JobRegistry(
            max_items=max_items,
            ttl_seconds=ttl_seconds,
            is_terminal=lambda job: job.terminal,
            updated_at=lambda job: job.updated_at,
            cleanup=self._cleanup_job,
            backing=self.jobs,
        )

    def start(self, *, operation: str, filename: str, mime: str, input_path: Path, voice: str) -> dict:
        normalized = str(operation or "").strip().lower()
        if normalized not in {"transcribe", "translate"}:
            input_path.unlink(missing_ok=True)
            return {"ok": False, "error": "media_operation_invalid"}
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
            )
            self.registry.add(job.job_id, job)
            self.active_job_id = job.job_id
            self.latest_job_id = job.job_id
        try:
            self.spawn(
                target=self._worker,
                args=(job.job_id, input_path),
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
            return job.to_dict() if job else self._idle_status()

    def status(self, job_id: str) -> dict:
        with self.lock:
            job = self.registry.get(str(job_id or "").strip())
            return job.to_dict() if job else {"ok": False, "error": "media_job_not_found"}

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.registry.get(str(job_id or "").strip())
            if not job:
                return {"ok": False, "error": "media_job_not_found"}
            if job.terminal:
                return job.to_dict()
            job.cancel_requested = True
            job.state = "canceling"
            job.detail = "Cancelando procesamiento..."
            job.updated_at = time.time()
            return job.to_dict()

    def mount(self, job_id: str) -> dict:
        with self.lock:
            job = self.registry.get(str(job_id or "").strip())
            if not job:
                return {"ok": False, "error": "media_job_not_found"}
            if job.state != "done":
                return {"ok": False, "error": "media_job_not_ready"}
            text = job.translated_text if job.operation == "translate" else job.transcript
            title_suffix = " — castellano" if job.operation == "translate" else " — transcripción"
            title = f"{Path(job.filename).stem}{title_suffix}"
        self.converted_root.mkdir(parents=True, exist_ok=True)
        filename = f"{safe_media_stem(job.filename)}_{job.job_id}{'_es' if job.operation == 'translate' else ''}.txt"
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
            source_type="media_translation" if job.operation == "translate" else "media_transcript",
        )
        with self.lock:
            current = self.jobs.get(job.job_id)
            if current:
                current.mounted = True
                current.translated_path = str(target) if current.operation == "translate" else current.translated_path
                current.transcript_path = str(target) if current.operation == "transcribe" else current.transcript_path
                current.updated_at = time.time()
        return {**result, "media_job_id": job.job_id, "mounted": True}

    def artifact(self, job_id: str, kind: str) -> dict:
        with self.lock:
            job = self.registry.get(str(job_id or "").strip())
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
                    job.state = "canceling"
                    job.detail = "Cancelando por cierre de Fusion Reader..."
                    job.updated_at = time.time()

    def _worker(self, job_id: str, input_path: Path) -> None:
        work_root = self.runtime_root / job_id
        normalized = work_root / "normalized.flac"
        work_root.mkdir(parents=True, exist_ok=True)
        try:
            self._update(job_id, state="running", stage="inspecting", progress=2, detail="Inspeccionando el archivo...")
            probe = probe_media(input_path)
            self._update(job_id, duration_seconds=probe.duration_seconds)
            self._check_cancelled(job_id)
            self._update(
                job_id,
                stage="normalizing",
                progress=8,
                detail="Extrayendo y normalizando el audio con FFmpeg...",
            )
            normalize_media_audio(input_path, normalized, timeout_seconds=self.timeout_seconds)
            self._check_cancelled(job_id)
            self._update(job_id, stage="transcribing", progress=18, detail="Transcribiendo con Whisper...")
            transcript = self.stt.transcribe_file(normalized, mime="audio/flac", language="auto")
            if not transcript.ok or not clean_text(transcript.text):
                raise RuntimeError(transcript.detail or "media_transcription_failed")
            detected = str(transcript.detected_language or "").strip().lower() or "desconocido"
            paragraphs = transcript_paragraphs(transcript.segments, transcript.text)
            title = f"{Path(self._job(job_id).filename).stem} — Transcripción"
            transcript_text = transcript_document_text(title, detected, paragraphs)
            transcript_file = work_root / "transcript.txt"
            transcript_file.write_text(transcript_text, encoding="utf-8")
            self._update(
                job_id,
                detected_language=detected,
                transcript=transcript_text,
                transcript_path=str(transcript_file),
                stage="building_pdf",
                progress=48,
                detail="Generando el PDF de la transcripción...",
            )
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
            self._check_cancelled(job_id)
            if self._job(job_id).operation == "translate":
                self._translate_and_synthesize(job_id, work_root, paragraphs, detected)
            self._update(
                job_id,
                state="done",
                stage="done",
                progress=100,
                detail="Procesamiento terminado. Ya podés descargar o montar el resultado.",
            )
        except _MediaCancelled:
            self._update(
                job_id,
                state="cancelled",
                stage="cancelled",
                detail="Procesamiento cancelado.",
            )
        except Exception as exc:
            self._update(
                job_id,
                state="error",
                stage="error",
                detail="No pude procesar este audio o video.",
                error=str(exc) or type(exc).__name__,
            )
        finally:
            input_path.unlink(missing_ok=True)
            normalized.unlink(missing_ok=True)
            with self.lock:
                if self.active_job_id == job_id:
                    self.active_job_id = ""

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
        title = f"{Path(self._job(job_id).filename).stem} — Traducción al castellano"
        translated_text = transcript_document_text(title, "es", translated)
        translated_file = work_root / "translated_es.txt"
        translated_file.write_text(translated_text, encoding="utf-8")
        self._update(job_id, translated_text=translated_text, translated_path=str(translated_file))
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
            stage="synthesizing",
            progress=76,
            detail="Generando el audio en castellano...",
        )
        chunks = Document.from_text(f"media-{job_id}", title, "\n\n".join(text for _, text in translated)).chunks
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

    def _check_cancelled(self, job_id: str) -> None:
        if self._job(job_id).cancel_requested:
            raise _MediaCancelled

    def _cleanup_job(self, job: MediaJob) -> None:
        root = self.runtime_root / job.job_id
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)

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
