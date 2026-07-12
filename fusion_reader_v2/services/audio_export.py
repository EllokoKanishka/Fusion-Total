from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from fusion_reader_v2.audio_export import (
    AudioExportJob,
    AudioExportSnapshot,
    build_audio_export_filename,
    concat_wav_files,
    unique_audio_download_target,
)
from fusion_reader_v2.domain.jobs import JobRegistry

if TYPE_CHECKING:
    from fusion_reader_v2.service import FusionReaderV2


class AudioExportService:
    """Owns bounded export jobs, snapshots, cancellation, and publication."""

    def __init__(
        self,
        owner: FusionReaderV2,
        *,
        jobs: dict[str, AudioExportJob],
        max_items: int = 256,
        ttl_seconds: float = 6 * 60 * 60,
    ) -> None:
        self.owner = owner
        self.registry = JobRegistry(
            max_items=max_items,
            ttl_seconds=ttl_seconds,
            is_terminal=lambda job: job.terminal,
            updated_at=lambda job: job.updated_at,
            backing=jobs,
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
        owner = self.owner
        document = owner.session.document
        if not document or not document.chunks:
            raise ValueError("no_document_loaded")
        total = len(document.chunks)
        current_block = owner.session.cursor + 1
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
            voice=owner.voice.voice,
            language=owner.voice.language,
            total_blocks=total,
            blocks=blocks,
        )

    def overview(self) -> dict:
        owner = self.owner
        with owner._audio_export_lock:
            self.registry.prune()
            job_id = owner._audio_export_active_job_id or owner._audio_export_latest_job_id
            job = owner._audio_export_jobs.get(job_id) if job_id else None
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
        owner = self.owner
        with owner._audio_export_lock:
            job = self.registry.get(clean)
            return job.to_dict() if job else {"ok": False, "error": "audio_export_job_not_found"}

    def start(self, mode: str, block: int | None = None, start: int | None = None, end: int | None = None) -> dict:
        owner = self.owner
        try:
            snapshot = owner._resolve_audio_export_snapshot(mode, block=block, start=start, end=end)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if any(not owner.cache.get(text, snapshot.voice, snapshot.language) for _, text in snapshot.blocks):
            tts_health = owner.tts.health()
            if not bool(tts_health.get("ok")):
                return {
                    "ok": False,
                    "error": "tts_unavailable_for_audio_export",
                    "detail": str(tts_health.get("detail") or ""),
                }
        owner._before_audio_export_registration()
        with owner._background_work_condition:
            if not owner._background_work_is_open_locked():
                return {"ok": False, "error": "service_shutting_down", "detail": "El lector se está cerrando."}
            with owner._audio_export_lock:
                if not owner._background_work_is_open_locked():
                    return {"ok": False, "error": "service_shutting_down", "detail": "El lector se está cerrando."}
                if owner._audio_export_thread and owner._audio_export_thread.is_alive():
                    return {
                        "ok": False,
                        "error": "audio_export_busy",
                        "detail": "Ya hay una exportación de audio en curso.",
                    }
                owner._audio_export_cancel.clear()
                job = owner._new_audio_export_job(snapshot)
                job.download_url = f"/api/audio-export/download/{job.job_id}"
                try:
                    self.registry.add(job.job_id, job)
                except RuntimeError:
                    return {"ok": False, "error": "audio_export_registry_full"}
                owner._audio_export_active_job_id = job.job_id
                owner._audio_export_latest_job_id = job.job_id
                owner._audio_export_thread = threading.Thread(
                    target=owner._audio_export_worker,
                    args=(job.job_id,),
                    name="fusion-reader-v2-audio-export",
                    daemon=False,
                )
                owner._audio_export_thread.start()
                return job.to_dict()

    def cancel(self, job_id: str) -> dict:
        owner = self.owner
        clean = str(job_id or "").strip()
        with owner._audio_export_lock:
            target_id = clean or owner._audio_export_active_job_id
            job = owner._audio_export_jobs.get(target_id)
            if not job:
                return {"ok": False, "error": "audio_export_job_not_found"}
            if job.state not in {"queued", "running"}:
                return job.to_dict()
            job.state = "canceling" if job.state == "running" else "cancelled"
            job.detail = "Cancelando exportación de audio..."
            owner._audio_export_cancel.set()
            return job.to_dict()

    def download(self, job_id: str) -> dict:
        owner = self.owner
        with owner._audio_export_lock:
            job = owner._audio_export_jobs.get(str(job_id or "").strip())
            if not job:
                return {"ok": False, "error": "audio_export_job_not_found"}
            if job.state != "done" or not job.output_path:
                return {"ok": False, "error": "audio_export_not_ready"}
            raw_path = Path(job.output_path)
            if raw_path.is_symlink():
                return {"ok": False, "error": "audio_export_path_invalid"}
            path = raw_path.resolve()
            filename = job.filename
        downloads_dir = owner.audio_export_root.resolve()
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
        owner = self.owner
        with owner._audio_export_lock:
            job = owner._audio_export_jobs.get(job_id)
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
            if owner._audio_export_active_job_id == job_id:
                owner._audio_export_active_job_id = ""

    def worker(self, job_id: str) -> None:
        owner = self.owner
        with owner._audio_export_lock:
            job = owner._audio_export_jobs.get(job_id)
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
                if owner._audio_export_cancel.is_set():
                    owner._finish_audio_export_job(job_id, "cancelled", "Exportación cancelada.")
                    return
                owner._wait_for_interactive_tts()
                with owner._audio_export_lock:
                    job = owner._audio_export_jobs.get(job_id)
                    if not job:
                        return
                    job.current_block = chunk_number
                    job.detail = f"Generando bloque {job.completed_blocks + 1} de {job.total_blocks}..."
                artifact = owner.cache.get(text, snapshot.voice, snapshot.language)
                if artifact:
                    with owner._audio_export_lock:
                        owner._audio_export_jobs[job_id].cached_blocks += 1
                else:
                    artifact = owner._synthesize_cached_with_settings(text, snapshot.voice, snapshot.language)
                    if not artifact.ok:
                        owner._finish_audio_export_job(
                            job_id,
                            "error",
                            "No pude generar audio para exportar.",
                            error=artifact.detail or "tts_failed",
                        )
                        return
                    with owner._audio_export_lock:
                        owner._audio_export_jobs[job_id].generated_blocks += 1
                if not artifact.ok or not artifact.path or not artifact.path.exists():
                    owner._finish_audio_export_job(
                        job_id,
                        "error",
                        "No encontré el WAV de un bloque exportado.",
                        error="audio_export_missing_artifact",
                    )
                    return
                inputs.append(Path(artifact.path))
                with owner._audio_export_lock:
                    current_job = owner._audio_export_jobs.get(job_id)
                    if current_job:
                        current_job.completed_blocks += 1
                        current_job.detail = f"Bloque {chunk_number} listo."
            if not inputs:
                owner._finish_audio_export_job(
                    job_id, "error", "No había bloques para exportar.", error="audio_export_no_inputs"
                )
                return
            target = unique_audio_download_target(job.filename, owner.audio_export_root)
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
            if owner._audio_export_cancel.is_set():
                temporary.unlink(missing_ok=True)
                owner._finish_audio_export_job(job_id, "cancelled", "Exportación cancelada.")
                return
            os.replace(temporary, target)
            temporary = None
            owner._finish_audio_export_job(
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
            owner._finish_audio_export_job(job_id, "error", "Falló la exportación de audio.", error=type(exc).__name__)
        finally:
            owner._audio_export_cancel.clear()
