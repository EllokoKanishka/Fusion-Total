from __future__ import annotations

import threading
import time
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fusion_reader_v2.tts import AudioArtifact

if TYPE_CHECKING:
    from fusion_reader_v2.service import FusionReaderV2


@dataclass
class BackgroundShutdownContext:
    export_thread: threading.Thread | None = None
    prepare_thread: threading.Thread | None = None
    prefetch_futures: list[Future[AudioArtifact]] = field(default_factory=list)
    executors: list[ThreadPoolExecutor] = field(default_factory=list)
    shutdown_threads: list[threading.Thread] = field(default_factory=list)
    shutdown_errors: list[tuple[str, Exception]] = field(default_factory=list)
    started: bool = False


class BackgroundLifecycleService:
    """Owns state transitions and deterministic shutdown of background work."""

    def __init__(self, owner: FusionReaderV2) -> None:
        self.owner = owner

    def set_state_locked(self, state: str) -> None:
        owner = self.owner
        normalized = str(state or "").strip().lower()
        if normalized not in {"open", "closing", "closed"}:
            raise ValueError(f"invalid background work state: {state!r}")
        owner._background_work_state = normalized
        owner._background_work_closing = normalized == "closing"
        owner._background_work_closed = normalized == "closed"
        owner._background_work_condition.notify_all()

    def begin_tts_operation(self) -> bool:
        owner = self.owner
        with owner._background_work_condition:
            if owner._background_work_state != "open":
                return False
            owner._background_work_active_tts += 1
            return True

    def end_tts_operation(self) -> None:
        owner = self.owner
        with owner._background_work_condition:
            if owner._background_work_active_tts > 0:
                owner._background_work_active_tts -= 1
            if owner._background_work_active_tts == 0:
                owner._background_work_condition.notify_all()

    def is_open(self) -> bool:
        owner = self.owner
        with owner._background_work_condition:
            return owner._background_work_state == "open"

    def is_open_locked(self) -> bool:
        return self.owner._background_work_state == "open"

    def wait_for_active_tts_locked(self, deadline: float) -> None:
        owner = self.owner
        while owner._background_work_active_tts > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("timed out waiting for interactive TTS to stop")
            owner._background_work_condition.wait(timeout=remaining)

    def capture_shutdown_context(self, context: BackgroundShutdownContext) -> None:
        owner = self.owner
        owner._audio_export_cancel.set()
        owner._prepare_cancel.set()
        with owner._audio_export_lock:
            context.export_thread = owner._audio_export_thread
            export_job_id = owner._audio_export_active_job_id
            if export_job_id:
                job = owner._audio_export_jobs.get(export_job_id)
                if job and job.state in {"queued", "running"}:
                    job.state = "canceling" if job.state == "running" else "cancelled"
                    job.detail = "Cancelando exportación de audio..."
        with owner._prepare_lock:
            context.prepare_thread = owner._prepare_thread
            if owner._prepare_status.get("status") == "running":
                owner._prepare_status["status"] = "canceling"
                owner._prepare_status["message"] = "Cancelando preparación..."
                owner._prepare_status["updated_ts"] = time.time()
        with owner._prefetch_lock:
            context.executors = list(dict.fromkeys(owner._prefetch_executors + [owner._executor]))
            context.prefetch_futures = list(owner._prefetch_futures.values())
            owner._prefetch_futures = {}
            owner._prefetch_started = {}
            owner._prefetch_future = None
            owner._prefetch_index = None
            owner._prefetch_started_ts = None
        with owner._tts_gate:
            owner._prefetch_promoted_keys.clear()
            owner._tts_gate.notify_all()

    def prioritize_dialogue(self) -> None:
        owner = self.owner
        owner._prepare_cancel.set()
        with owner._prepare_lock:
            if owner._prepare_status.get("status") == "running":
                owner._prepare_status["status"] = "canceling"
                owner._prepare_status["message"] = "Cancelando preparación para priorizar diálogo..."
                owner._prepare_status["updated_ts"] = time.time()
        owner._clear_prefetch_queue()

    def clear_prefetch_queue(self) -> None:
        owner = self.owner
        with owner._background_work_condition:
            background_open = owner._background_work_is_open_locked()
            with owner._prefetch_lock:
                tracked_futures = list(owner._prefetch_futures.values())
                old_executor = owner._executor
                if background_open:
                    owner._executor = ThreadPoolExecutor(
                        max_workers=owner.prefetch_workers,
                        thread_name_prefix="fusion-reader-v2-tts",
                    )
                    owner._prefetch_executors.append(owner._executor)
                owner._prefetch_futures = {}
                owner._prefetch_started = {}
                owner._prefetch_future = None
                owner._prefetch_index = None
                owner._prefetch_started_ts = None
        for future in tracked_futures:
            future.cancel()
        with owner._tts_gate:
            owner._prefetch_promoted_keys.clear()
            owner._tts_gate.notify_all()
        old_executor.shutdown(wait=False, cancel_futures=True)

    def reset_prefetch_queue(self, stale_future: Future[AudioArtifact]) -> None:
        owner = self.owner
        old_executor: ThreadPoolExecutor | None = None
        stale_keys: list[tuple] = []
        stale_futures: list[Future[AudioArtifact]] = []
        with owner._background_work_condition:
            background_open = owner._background_work_is_open_locked()
            with owner._prefetch_lock:
                stale_keys = [key for key, future in owner._prefetch_futures.items() if future is stale_future]
                stale_futures = [future for future in owner._prefetch_futures.values() if future is stale_future]
                if not background_open:
                    for key in stale_keys:
                        owner._prefetch_futures.pop(key, None)
                        owner._prefetch_started.pop(key, None)
                    if owner._prefetch_future is stale_future:
                        owner._prefetch_future = None
                        owner._prefetch_index = None
                        owner._prefetch_started_ts = None
                else:
                    if owner._prefetch_future is not stale_future and not stale_keys:
                        return
                    old_executor = owner._executor
                    owner._executor = ThreadPoolExecutor(
                        max_workers=owner.prefetch_workers,
                        thread_name_prefix="fusion-reader-v2-tts",
                    )
                    owner._prefetch_executors.append(owner._executor)
                    for key in stale_keys:
                        owner._prefetch_futures.pop(key, None)
                        owner._prefetch_started.pop(key, None)
                    owner._prefetch_future = None
                    owner._prefetch_index = None
                    owner._prefetch_started_ts = None
                    owner._set_primary_prefetch_locked()
        for future in stale_futures:
            future.cancel()
        if stale_keys:
            with owner._tts_gate:
                removed = False
                for key in stale_keys:
                    if key in owner._prefetch_promoted_keys:
                        owner._prefetch_promoted_keys.discard(key)
                        removed = True
                if removed:
                    owner._tts_gate.notify_all()
        if old_executor is not None:
            old_executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def wait_for_thread(thread: threading.Thread | None, *, label: str, deadline: float) -> None:
        if thread is None:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {label} thread to stop")
        thread.join(timeout=remaining)
        if thread.is_alive():
            raise AssertionError(f"timed out waiting for {label} thread to stop: {thread.name}")

    def shutdown(self, timeout: float = 10.0) -> dict:
        owner = self.owner
        deadline = time.monotonic() + max(0.0, float(timeout))
        with owner._background_work_condition:
            if owner._background_work_state == "closed":
                return {"ok": True, "state": "closed", "detail": "already_closed"}
            context = owner._background_work_shutdown_context
            if context is None:
                context = BackgroundShutdownContext()
                owner._background_work_shutdown_context = context
            if owner._background_work_state == "open":
                owner._set_background_work_state_locked("closing")
            if not context.started:
                owner._capture_background_shutdown_context(context)
                shutdown_errors_lock = threading.Lock()

                def shutdown_executor(executor: ThreadPoolExecutor, label: str) -> None:
                    try:
                        executor.shutdown(wait=True, cancel_futures=True)
                    except Exception as exc:  # pragma: no cover - surfaced by the caller
                        with shutdown_errors_lock:
                            context.shutdown_errors.append((label, exc))

                context.shutdown_threads = []
                for index, executor in enumerate(context.executors):
                    thread = threading.Thread(
                        target=shutdown_executor,
                        args=(executor, f"executor-{index}"),
                        name=f"fusion-reader-v2-shutdown-{index}",
                        daemon=False,
                    )
                    thread.start()
                    context.shutdown_threads.append(thread)
                context.started = True
            export_thread = context.export_thread
            prepare_thread = context.prepare_thread
            prefetch_futures = list(context.prefetch_futures)
            shutdown_threads = list(context.shutdown_threads)
            shutdown_errors = context.shutdown_errors
        owner._wait_for_thread(export_thread, label="audio export", deadline=deadline)
        owner._wait_for_thread(prepare_thread, label="prepare", deadline=deadline)
        for future in prefetch_futures:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("timed out waiting for prefetch future to stop")
            if future.done() or future.cancel():
                continue
            try:
                future.result(timeout=max(0.0, deadline - time.monotonic()))
            except CancelledError:
                pass
            except TimeoutError as exc:
                raise AssertionError("timed out waiting for prefetch future to stop") from exc
        for thread in shutdown_threads:
            owner._wait_for_thread(thread, label="prefetch executor shutdown", deadline=deadline)
        if shutdown_errors:
            label, shutdown_error = shutdown_errors[0]
            raise AssertionError(f"prefetch executor shutdown failed for {label}: {shutdown_error}") from shutdown_error
        with owner._background_work_condition:
            owner._wait_for_active_tts_locked(deadline)
            owner._set_background_work_state_locked("closed")
        return {
            "ok": True,
            "state": "closed",
            "prefetch_futures": len(prefetch_futures),
            "executors": len(context.executors),
        }
