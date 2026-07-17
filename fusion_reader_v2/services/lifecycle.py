from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from typing import Protocol

from fusion_reader_v2.tts import AudioArtifact


class StoppableBackgroundService(Protocol):
    def begin_shutdown(self) -> threading.Thread | None: ...


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
    """Owns lifecycle state and deterministic shutdown coordination."""

    def __init__(
        self,
        *,
        capture_prefetch: Callable[[], tuple[list[ThreadPoolExecutor], list[Future[AudioArtifact]]]],
        clear_prefetch: Callable[[], None],
        reset_prefetch: Callable[[Future[AudioArtifact]], None],
    ) -> None:
        self.condition = threading.Condition(threading.RLock())
        self.state = "open"
        self.active_tts = 0
        self.shutdown_context: BackgroundShutdownContext | None = None
        self.audio_export: StoppableBackgroundService | None = None
        self.preparation: StoppableBackgroundService | None = None
        self.capture_prefetch = capture_prefetch
        self.clear_prefetch_callback = clear_prefetch
        self.reset_prefetch_callback = reset_prefetch

    def bind_background_services(
        self,
        *,
        audio_export: StoppableBackgroundService,
        preparation: StoppableBackgroundService,
    ) -> None:
        if self.audio_export is not None or self.preparation is not None:
            raise RuntimeError("background_services_already_bound")
        self.audio_export = audio_export
        self.preparation = preparation

    def set_state_locked(self, state: str) -> None:
        normalized = str(state or "").strip().lower()
        if normalized not in {"open", "closing", "closed"}:
            raise ValueError(f"invalid background work state: {state!r}")
        self.state = normalized
        self.condition.notify_all()

    def begin_tts_operation(self) -> bool:
        with self.condition:
            if self.state != "open":
                return False
            self.active_tts += 1
            return True

    def end_tts_operation(self) -> None:
        with self.condition:
            if self.active_tts > 0:
                self.active_tts -= 1
            if self.active_tts == 0:
                self.condition.notify_all()

    def is_open(self) -> bool:
        with self.condition:
            return self.state == "open"

    def is_open_locked(self) -> bool:
        return self.state == "open"

    def wait_for_active_tts_locked(self, deadline: float) -> None:
        while self.active_tts > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("timed out waiting for interactive TTS to stop")
            self.condition.wait(timeout=remaining)

    def capture_shutdown_context(self, context: BackgroundShutdownContext) -> None:
        if self.audio_export is None or self.preparation is None:
            raise RuntimeError("background_services_not_bound")
        context.export_thread = self.audio_export.begin_shutdown()
        context.prepare_thread = self.preparation.begin_shutdown()
        context.executors, context.prefetch_futures = self.capture_prefetch()

    def prioritize_dialogue(self) -> None:
        if self.preparation is None:
            raise RuntimeError("background_services_not_bound")
        cancel = getattr(self.preparation, "cancel", None)
        if callable(cancel):
            cancel("Cancelando preparación para priorizar diálogo...")
        else:
            self.preparation.begin_shutdown()
        self.clear_prefetch_callback()

    def clear_prefetch_queue(self) -> None:
        self.clear_prefetch_callback()

    def reset_prefetch_queue(self, stale_future: Future[AudioArtifact]) -> None:
        self.reset_prefetch_callback(stale_future)

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
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self.condition:
            if self.state == "closed":
                return {"ok": True, "state": "closed", "detail": "already_closed"}
            context = self.shutdown_context
            if context is None:
                context = BackgroundShutdownContext()
                self.shutdown_context = context
            if self.state == "open":
                self.set_state_locked("closing")
            if not context.started:
                self.capture_shutdown_context(context)
                shutdown_errors_lock = threading.Lock()

                def shutdown_executor(executor: ThreadPoolExecutor, label: str) -> None:
                    try:
                        executor.shutdown(wait=True, cancel_futures=True)
                    except Exception as exc:  # pragma: no cover - surfaced by the caller
                        with shutdown_errors_lock:
                            context.shutdown_errors.append((label, exc))

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
        self.wait_for_thread(export_thread, label="audio export", deadline=deadline)
        self.wait_for_thread(prepare_thread, label="prepare", deadline=deadline)
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
            self.wait_for_thread(thread, label="prefetch executor shutdown", deadline=deadline)
        if shutdown_errors:
            label, shutdown_error = shutdown_errors[0]
            raise AssertionError(f"prefetch executor shutdown failed for {label}: {shutdown_error}") from shutdown_error
        with self.condition:
            self.wait_for_active_tts_locked(deadline)
            self.set_state_locked("closed")
        return {
            "ok": True,
            "state": "closed",
            "prefetch_futures": len(prefetch_futures),
            "executors": len(context.executors),
        }


__all__ = ["BackgroundLifecycleService", "BackgroundShutdownContext"]
