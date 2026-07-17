from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from fusion_reader_v2.audio_export import (
    AudioExportJob,
    AudioExportSnapshot,
    build_audio_export_filename,
    concat_wav_files,
)
from fusion_reader_v2.domain.jobs import JobRegistry
from fusion_reader_v2.output_reservation import reserve_output_path
from fusion_reader_v2.reader import ReaderSession
from fusion_reader_v2.tts import AudioArtifact


class VoiceSelection(Protocol):
    voice: str
    language: str


class AudioCacheReader(Protocol):
    def get(self, text: str, voice: str, language: str) -> AudioArtifact | None: ...


class TTSHealth(Protocol):
    def health(self) -> dict: ...


class AudioExportService:
    """Owns bounded export jobs, snapshots, cancellation, and publication."""

    def __init__(
        self,
        *,
        session: ReaderSession,
        voice: VoiceSelection,
        cache: AudioCacheReader,
        tts: TTSHealth,
        output_root: Path,
        background_condition: threading.Condition,
        background_is_open_locked: Callable[[], bool],
        before_registration: Callable[[], None],
        wait_for_interactive_tts: Callable[[], None],
        synthesize: Callable[[str, str, str], AudioArtifact],
        max_items: int = 256,
        ttl_seconds: float = 6 * 60 * 60,
    ) -> None:
        self.session = session
        self.voice = voice
        self.cache = cache
        self.tts = tts
        self.output_root = output_root
        self.background_condition = background_condition
        self.background_is_open_locked = background_is_open_locked
        self.before_registration = before_registration
        self.wait_for_interactive_tts = wait_for_interactive_tts
        self.synthesize = synthesize
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.jobs: dict[str, AudioExportJob] = {}
        self.active_job_id = ""
        self.latest_job_id = ""
        self.registry = JobRegistry(
            max_items=max_items,
            ttl_seconds=ttl_seconds,
            is_terminal=lambda job: job.terminal,
            updated_at=lambda job: job.updated_at,
            backing=self.jobs,
        )

    def new_job(self, snapshot: AudioExportSnapshot) -> AudioExportJob:
        start_block = snapshot.blocks[0][0]
        end_block = snapshot.blocks[-1][0]
        return AudioExportJob(
            job_id=uuid.uuid4().hex[:16],
            state="queued",
            title=snapshot.title,
            start_block=start_block,
            end_block=end_block,
            total_blocks=len(snapshot.blocks),
            filename=build_audio_export_filename(snapshot.title, start_block, end_block, snapshot.total_blocks),
            detail="En cola para exportar audio.",
            started_at=time.time(),
            doc_id=snapshot.doc_id,
            voice=snapshot.voice,
            language=snapshot.language,
            snapshot=snapshot,
        )

    def resolve_snapshot(
        self,
        mode: str,
        block: int | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> AudioExportSnapshot:
        document = self.session.document
        if not document or not document.chunks:
            raise ValueError("no_document_loaded")
        total = len(document.chunks)
        current_block = self.session.cursor + 1
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode == "current":
            start_block = end_block = current_block
        elif normalized_mode == "block":
            start_block = end_block = int(block or 0)
            if start_block < 1 or start_block > total:
                raise ValueError("audio_export_block_out_of_range")
        elif normalized_mode == "range":
            start_block = int(start or 0)
            end_block = int(end or 0)
            if start_block < 1 or end_block < 1 or start_block > end_block or end_block > total:
                raise ValueError("audio_export_range_invalid")
        elif normalized_mode == "full":
            start_block, end_block = 1, total
        else:
            raise ValueError("audio_export_mode_invalid")
        blocks = [(index + 1, document.chunks[index]) for index in range(start_block - 1, end_block)]
        return AudioExportSnapshot(
            doc_id=document.doc_id,
            title=document.title,
            voice=self.voice.voice,
            language=self.voice.language,
            total_blocks=total,
            blocks=blocks,
        )

    def overview(self) -> dict:
        with self.lock:
            self.registry.prune()
            job_id = self.active_job_id or self.latest_job_id
            job = self.jobs.get(job_id) if job_id else None
            if job is not None:
                return job.to_dict()
        return {
            "ok": True,
            "job_id": "",
            "state": "idle",
            "detail": "Sin exportación de audio activa.",
            "title": "",
            "start_block": 0,
            "end_block": 0,
            "total_blocks": 0,
            "completed_blocks": 0,
            "cached_blocks": 0,
            "generated_blocks": 0,
            "current_block": 0,
            "output_path": "",
            "filename": "",
            "download_url": "",
            "concat_method": "",
            "error": "",
        }

    def status(self, job_id: str) -> dict:
        clean = str(job_id or "").strip()
        if not clean:
            return self.overview()
        with self.lock:
            job = self.registry.get(clean)
            return job.to_dict() if job else {"ok": False, "error": "audio_export_job_not_found"}

    def start(self, mode: str, block: int | None = None, start: int | None = None, end: int | None = None) -> dict:
        try:
            snapshot = self.resolve_snapshot(mode, block=block, start=start, end=end)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if any(not self.cache.get(text, snapshot.voice, snapshot.language) for _, text in snapshot.blocks):
            tts_health = self.tts.health()
            if not bool(tts_health.get("ok")):
                return {
                    "ok": False,
                    "error": "tts_unavailable_for_audio_export",
                    "detail": str(tts_health.get("detail") or ""),
                }
        self.before_registration()
        with self.background_condition:
            if not self.background_is_open_locked():
                return {"ok": False, "error": "service_shutting_down", "detail": "El lector se está cerrando."}
            with self.lock:
                if not self.background_is_open_locked():
                    return {"ok": False, "error": "service_shutting_down", "detail": "El lector se está cerrando."}
                if self.thread and self.thread.is_alive():
                    return {
                        "ok": False,
                        "error": "audio_export_busy",
                        "detail": "Ya hay una exportación de audio en curso.",
                    }
                self.cancel_event.clear()
                job = self.new_job(snapshot)
                job.download_url = f"/api/audio-export/download/{job.job_id}"
                try:
                    self.registry.add(job.job_id, job)
                except RuntimeError:
                    return {"ok": False, "error": "audio_export_registry_full"}
                self.active_job_id = job.job_id
                self.latest_job_id = job.job_id
                self.thread = threading.Thread(
                    target=self.worker,
                    args=(job.job_id,),
                    name="fusion-reader-v2-audio-export",
                    daemon=False,
                )
                self.thread.start()
                return job.to_dict()

    def cancel(self, job_id: str) -> dict:
        clean = str(job_id or "").strip()
        with self.lock:
            target_id = clean or self.active_job_id
            job = self.jobs.get(target_id)
            if not job:
                return {"ok": False, "error": "audio_export_job_not_found"}
            if job.state not in {"queued", "running"}:
                return job.to_dict()
            job.state = "canceling" if job.state == "running" else "cancelled"
            job.detail = "Cancelando exportación de audio..."
            self.cancel_event.set()
            return job.to_dict()

    def download(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(str(job_id or "").strip())
            if not job:
                return {"ok": False, "error": "audio_export_job_not_found"}
            if job.state != "done" or not job.output_path:
                return {"ok": False, "error": "audio_export_not_ready"}
            raw_path = Path(job.output_path)
            if raw_path.is_symlink():
                return {"ok": False, "error": "audio_export_path_invalid"}
            path = raw_path.resolve()
            filename = job.filename
        downloads_dir = self.output_root.resolve()
        try:
            path.relative_to(downloads_dir)
        except ValueError:
            return {"ok": False, "error": "audio_export_path_invalid"}
        if path.is_symlink() or not path.is_file():
            return {"ok": False, "error": "audio_export_file_missing"}
        return {"ok": True, "path": str(path), "filename": filename}

    def finish(
        self,
        job_id: str,
        state: str,
        detail: str,
        *,
        output_path: Path | None = None,
        concat_method: str = "",
        error: str = "",
    ) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job.state = state
            job.detail = detail
            job.concat_method = concat_method
            job.error = error
            job.finished_at = time.time()
            if output_path:
                job.output_path = str(output_path)
                job.filename = output_path.name
            if self.active_job_id == job_id:
                self.active_job_id = ""

    def worker(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or not job.snapshot:
                return
            snapshot = job.snapshot
            job.state = "running"
            job.detail = "Preparando exportación de audio..."
        inputs: list[Path] = []
        target: Path | None = None
        temporary: Path | None = None
        try:
            for chunk_number, text in snapshot.blocks:
                if self.cancel_event.is_set():
                    self.finish(job_id, "cancelled", "Exportación cancelada.")
                    return
                self.wait_for_interactive_tts()
                with self.lock:
                    job = self.jobs.get(job_id)
                    if not job:
                        return
                    job.current_block = chunk_number
                    job.detail = f"Generando bloque {job.completed_blocks + 1} de {job.total_blocks}..."
                artifact = self.cache.get(text, snapshot.voice, snapshot.language)
                if artifact:
                    with self.lock:
                        self.jobs[job_id].cached_blocks += 1
                else:
                    artifact = self.synthesize(text, snapshot.voice, snapshot.language)
                    if not artifact.ok:
                        self.finish(
                            job_id,
                            "error",
                            "No pude generar audio para exportar.",
                            error=artifact.detail or "tts_failed",
                        )
                        return
                    with self.lock:
                        self.jobs[job_id].generated_blocks += 1
                if not artifact.ok or not artifact.path or not artifact.path.exists():
                    self.finish(
                        job_id,
                        "error",
                        "No encontré el WAV de un bloque exportado.",
                        error="audio_export_missing_artifact",
                    )
                    return
                inputs.append(Path(artifact.path))
                with self.lock:
                    current_job = self.jobs.get(job_id)
                    if current_job:
                        current_job.completed_blocks += 1
                        current_job.detail = f"Bloque {chunk_number} listo."
            if not inputs:
                self.finish(job_id, "error", "No había bloques para exportar.", error="audio_export_no_inputs")
                return
            reservation = reserve_output_path(self.output_root, job.filename, default_suffix=".wav")
            target = reservation.path
            temporary = target.with_name(f".{target.stem}.{job_id}.part.wav")
            if len(inputs) == 1:
                with inputs[0].open("rb") as source, temporary.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                concat_method = "copy"
            else:
                concat_method = concat_wav_files(inputs, temporary)
            if self.cancel_event.is_set():
                temporary.unlink(missing_ok=True)
                self.finish(job_id, "cancelled", "Exportación cancelada.")
                return
            reservation.publish(temporary)
            temporary = None
            self.finish(
                job_id,
                "done",
                "Listo: guardado en Descargas.",
                output_path=target,
                concat_method=concat_method,
            )
        except Exception as exc:
            if temporary:
                temporary.unlink(missing_ok=True)
            if target and target.exists():
                target.unlink(missing_ok=True)
            self.finish(job_id, "error", "Falló la exportación de audio.", error=type(exc).__name__)
        finally:
            if "reservation" in locals():
                reservation.cleanup()
            self.cancel_event.clear()

    def begin_shutdown(self) -> threading.Thread | None:
        self.cancel_event.set()
        with self.lock:
            thread = self.thread
            job = self.jobs.get(self.active_job_id)
            if job and job.state in {"queued", "running"}:
                job.state = "canceling" if job.state == "running" else "cancelled"
                job.detail = "Cancelando exportación de audio..."
            return thread
