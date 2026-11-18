"""Resource inventory, admission and the explicit Panda-only Ollama boundary."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .gpu_budget import GPUBudget, read_gpu_budget
from .resource_scheduling import PriorityGate

WORK_CONTEXT = ContextVar("panda_resource_work", default=(0, None, True))


@contextmanager
def resource_work(priority, cancel=None, *, wait_for_gpu=True):
    token = WORK_CONTEXT.set((priority, cancel, wait_for_gpu))
    try:
        yield
    finally:
        WORK_CONTEXT.reset(token)


def consumer_inventory(app, context=None):
    chat = getattr(getattr(app, "conversation", None), "provider", None)
    chat_providers = getattr(chat, "providers", {})
    local = chat_providers.get("local", chat)
    dictation = getattr(getattr(app, "dictation_assistant", None), "providers", {})
    corrector = getattr(app, "_dictation_corrector", None)
    media = getattr(context, "media", None)
    entries = [
        ("chat_local", local),
        ("dictation_local", dictation.get("local")),
        ("dictation_local14b", dictation.get("local14b")),
        ("dictation_correction", corrector),
        ("media_translation", getattr(media, "chat", local)),
        ("media_correction", getattr(media, "corrector", corrector)),
        ("reader_tts", getattr(app, "tts", None)),
        ("dialogue_stt", getattr(app, "stt", None)),
        ("dictation_stt", getattr(app, "stt", None)),
        ("media_stt", getattr(media, "stt", getattr(app, "stt", None))),
        ("stt_primary", getattr(getattr(app, "stt", None), "primary", None)),
        ("stt_fallback", getattr(getattr(app, "stt", None), "fallback", None)),
        ("chat_cloud", chat_providers.get("openai")),
        ("dictation_cloud", dictation.get("openai")),
        ("external_research", getattr(app, "external_research", None)),
        ("reading_alignment", getattr(context, "reading_timings", None)),
    ]
    rows = []
    for role, provider in entries:
        endpoint = getattr(provider, "base_url", "") or getattr(provider, "stt_url", "")
        try:
            parsed = urlsplit(str(endpoint))
            safe_endpoint = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.hostname else ""
        except ValueError:
            safe_endpoint = "[invalid_endpoint]"
        rows.append(
            {
                "role": role,
                "provider": str(getattr(provider, "name", "unconfigured")),
                "endpoint": safe_endpoint,
                "model": str(getattr(provider, "default_model", "") or getattr(provider, "model", "")),
                "device": str(getattr(provider, "resource_device", "")),
                "used_models": sorted(getattr(provider, "used_models", set())),
            }
        )
    return rows


def dedicated_settings(settings):
    if settings.resources.ollama_mode != "dedicated":
        return settings
    port = settings.resources.private_ollama_port
    if port in {
        settings.ports.api,
        settings.ports.stt,
        settings.ports.tts_gpu,
        settings.ports.tts_cpu,
        settings.ports.searxng,
    }:
        raise ValueError("private_ollama_port_conflict")
    return replace(
        settings,
        ports=replace(settings.ports, ollama=port),
        providers=replace(settings.providers, ollama_url=f"http://127.0.0.1:{port}"),
    )


class ResourceController:
    def __init__(self, settings, daemon=None, budget_reader=read_gpu_budget):
        self.settings = settings
        self.daemon = daemon
        self.gate = PriorityGate()
        self.budget_reader = budget_reader
        self.cache_lock = threading.Lock()
        self.budget_at = 0.0
        self.budget = None
        self.last_admission = "not_requested"

    def sample(self, refresh=False):
        with self.cache_lock:
            if refresh or self.budget is None or time.monotonic() - self.budget_at > 1:
                self.budget = self.budget_reader(self.settings.resources.reserve_mib)
                self.budget_at = time.monotonic()
            return self.budget

    def available(self):
        return self.daemon is None or self.daemon.ready()

    def begin_shutdown(self):
        if self.daemon is not None:
            self.daemon.request_stop()

    def _resident_models(self):
        if self.daemon is None or not self.daemon.ready():
            return []
        with urlopen(self.daemon.base_url + "/api/ps", timeout=2) as response:
            return json.load(response).get("models", [])

    @contextmanager
    def inference(self, model, timeout=120):
        priority, cancel, wait_for_gpu = WORK_CONTEXT.get()
        deadline = min(time.monotonic() + timeout, getattr(cancel, "deadline", float("inf")))

        class Signal:
            deadline: float

            def is_set(inner):
                return (cancel is not None and cancel.is_set()) or (self.daemon is not None and self.daemon.stopping)

        signal = Signal()
        signal.deadline = deadline
        while True:
            self.gate.check(signal)
            if not self.available():
                raise RuntimeError("dedicated_ollama_not_owned_or_unavailable")
            with self.gate.slot(priority, signal):
                resident = self._resident_models()
                present = any(str(row.get("name") or row.get("model")) == model for row in resident)
                estimate = (
                    self.settings.resources.small_llm_working_set_mib
                    if model == self.settings.providers.dictation_model
                    else self.settings.resources.llm_working_set_mib
                )
                estimate = 0 if present else estimate
                budget = self.sample()
                if (
                    budget.measured
                    and not budget.permits(estimate)
                    and resident
                    and not present
                    and self.daemon is not None
                ):
                    # Only this verified Panda-owned server, while no Panda inference
                    # holds the gate. A shared daemon is never eligible for eviction.
                    for row in resident:
                        self.gate.check(signal)
                        if not self.daemon.ready():
                            raise RuntimeError("dedicated_ollama_identity_changed")
                        request = Request(
                            self.daemon.base_url + "/api/generate",
                            data=json.dumps({"model": row.get("name") or row.get("model"), "keep_alive": 0}).encode(),
                            headers={"Content-Type": "application/json"},
                        )
                        with urlopen(request, timeout=5) as response:
                            json.load(response)
                    budget = self.sample(refresh=True)
                if budget.permits(estimate):
                    self.last_admission = "admitted_from_observed_budget"
                    self.gate.check(signal)
                    try:
                        yield
                    finally:
                        # Loading/inference can change residency. Never reuse the
                        # pre-inference sample as fresh evidence for another job.
                        self.budget_at = 0.0
                    return
                self.last_admission = "waiting_for_gpu_budget" if budget.measured else "gpu_measurement_unavailable"
                if not wait_for_gpu:
                    raise RuntimeError("gpu_budget_insufficient" if budget.measured else "gpu_measurement_unavailable")
            # Don't hold the inference gate while waiting for memory: a queued
            # dialogue can overtake a long job at the next admission boundary.
            time.sleep(min(0.1, max(0.001, deadline - time.monotonic())))

    def bind_stt(self, provider):
        if provider is None:
            return
        if getattr(provider, "name", "") == "whisper_cli":
            # Fallback is a cancellable CPU child, never an unbudgeted second GPU model.
            provider.resource_device = "cpu"
        self.bind_stt(getattr(provider, "primary", None))
        self.bind_stt(getattr(provider, "fallback", None))

    def bind(self, app):
        app.resource_controller = self
        self.bind_stt(app.stt)
        providers = getattr(app.conversation.provider, "providers", {"local": app.conversation.provider})
        for provider in [*providers.values(), *getattr(app.dictation_assistant, "providers", {}).values()]:
            if getattr(provider, "name", "") == "ollama":
                provider.base_url = self.settings.providers.ollama_url
                provider.availability_guard = self.available
                provider.resource_scope = self.inference
        corrector = getattr(app, "_dictation_corrector", None)
        if corrector is not None:
            corrector.base_url = self.settings.providers.ollama_url
            corrector.availability_guard = self.available
            corrector.resource_scope = self.inference

    def status(self, app, context=None):
        budget = self.budget or GPUBudget(None, None, self.settings.resources.reserve_mib, "not_sampled")
        age = max(0.0, time.monotonic() - self.budget_at) if self.budget_at else None
        return {
            "ownership": "dedicated_child" if self.daemon else "shared_not_unloaded",
            "ollama": self.daemon.status() if self.daemon else {"ok": True, "ownership": "shared"},
            "gpu": {**budget.to_dict(), "sample_age_seconds": age, "cached_observation": True},
            "last_admission": self.last_admission,
            "consumers": consumer_inventory(app, context),
            "estimates_are_not_peak_vram_guarantees": True,
        }
