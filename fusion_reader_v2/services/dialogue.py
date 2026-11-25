from __future__ import annotations

from fusion_reader_v2.veils import veil_catalog as build_veil_catalog

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from fusion_reader_v2.openclaw_bridge import ExternalResearchResult

from .research import WebIntent


class ReasoningCatalog(Protocol):
    def reasoning_status(self, mode: str = "") -> dict: ...

LaboratoryScope = tuple[str, str, int, str]


@dataclass(frozen=True)
class LaboratoryResearchRequest:
    text: str
    cursor: int
    previous_answer: str = ""
    previous_question: str = ""
    web_intent: WebIntent | None = None


@dataclass
class LaboratoryResearch:
    pending: LaboratoryResearchRequest | None = None
    last_attempt: LaboratoryResearchRequest | None = None
    previous_request: LaboratoryResearchRequest | None = None
    last_search: ExternalResearchResult | None = None
    opened_url: str = ""
    opened_context: str = ""
    opened_link: dict[str, str] = field(default_factory=dict)
    opened_digest: str = ""

    @property
    def sources(self) -> list[dict]:
        return self.last_search.sources if self.last_search is not None else []


@dataclass
class _LaboratoryThread:
    history: list[dict] = field(default_factory=list)
    focus: dict = field(default_factory=dict)
    research: LaboratoryResearch = field(default_factory=LaboratoryResearch)


class DialogueService:
    """Owns dialogue histories and user-selectable conversation modes."""

    def __init__(
        self,
        *,
        conversation: ReasoningCatalog,
        reasoning_mode: str = "thinking",
        dialogue_allow_supreme: bool = False,
        persist: Callable[[], None],
        trace: Callable[[dict], None],
        services_status: Callable[[], dict],
        restore_laboratory: Callable[[LaboratoryScope], tuple[list[dict], LaboratoryResearch]] | None = None,
    ) -> None:
        self.conversation = conversation
        self.reasoning_mode = reasoning_mode
        self.laboratory_mode = "document"
        self.profile = "academica"
        self.veil = "lucy"
        self.dialogue_allow_supreme = dialogue_allow_supreme
        self.persist = persist
        self.trace = trace
        self.services_status = services_status
        self.restore_laboratory = restore_laboratory
        self.chat_lock = threading.Lock()
        self.chat_history: list[dict] = []
        self.dialogue_lock = threading.Lock()
        self.dialogue_history: list[dict] = []
        self._laboratory_lock = threading.Lock()
        self._laboratory_threads: OrderedDict[LaboratoryScope, _LaboratoryThread] = OrderedDict()

    def _laboratory_thread(self, key: LaboratoryScope) -> _LaboratoryThread:
        thread = self._laboratory_threads.get(key)
        if thread is None:
            if self.restore_laboratory is None:
                thread = _LaboratoryThread()
            else:
                history, research = self.restore_laboratory(key)
                thread = _LaboratoryThread(history=history, research=research)
            self._laboratory_threads[key] = thread
        self._laboratory_threads.move_to_end(key)
        if len(self._laboratory_threads) > 48:
            self._laboratory_threads.popitem(last=False)
        return thread

    def laboratory_history(self, key: LaboratoryScope) -> tuple[list[dict], list[dict]]:
        with self._laboratory_lock:
            history = self._laboratory_thread(key).history
            return history, list(history)

    def laboratory_research(self, key: LaboratoryScope) -> LaboratoryResearch:
        with self._laboratory_lock:
            return self._laboratory_thread(key).research

    def laboratory_focus(self, key: LaboratoryScope) -> dict:
        with self._laboratory_lock:
            thread = self._laboratory_threads.get(key)
            return dict(thread.focus) if thread is not None else {}

    def set_laboratory_focus(self, key: LaboratoryScope, focus: dict) -> None:
        with self._laboratory_lock:
            self._laboratory_thread(key).focus = focus

    def drop_laboratory_focus(self, doc_id: str) -> None:
        with self._laboratory_lock:
            for thread in self._laboratory_threads.values():
                if thread.focus.get("doc_id") == doc_id:
                    thread.focus = {}

    def remember_laboratory_turn(
        self, history: list[dict], question: str, answer: str, *, research_failed: bool = False
    ) -> None:
        if not answer.strip():
            return
        stamp = time.time()
        with self._laboratory_lock:
            history.extend(
                {
                    "role": role, "content": content[:4000], "ts": stamp,
                    **({"research_failed": True} if research_failed else {}),
                }
                for role, content in (("user", question), ("assistant", answer))
            )
            del history[:-8]

    def clear_laboratory_session(self, session: str = "") -> int:
        with self._laboratory_lock:
            keys = [key for key in self._laboratory_threads if not session or key[0] == session]
            return sum(len(self._laboratory_threads.pop(key).history) for key in keys)

    def effective_reasoning(self, *, dialogue: bool = False) -> dict:
        requested = str(self.reasoning_mode or "thinking")
        if requested == "contrapunto":
            requested = "pensamiento_critico"
        applied = requested
        degraded = False
        reason = ""
        if dialogue and requested in {"supreme", "pensamiento_critico"} and not self.dialogue_allow_supreme:
            applied = "thinking"
            degraded = True
            reason = f"dialogue_{requested}_degraded_to_thinking"
        return {
            "requested": requested,
            "applied": applied,
            "degraded": degraded,
            "reason": reason,
        }

    def remember_turn(self, user_message: str, assistant_answer: str, *, dialogue: bool = False) -> None:
        lock = self.dialogue_lock if dialogue else self.chat_lock
        with lock:
            history = self.dialogue_history if dialogue else self.chat_history
            stamp = time.time()
            if user_message:
                history.append({"role": "user", "content": user_message, "ts": stamp})
            if assistant_answer:
                history.append({"role": "assistant", "content": assistant_answer, "ts": stamp})
            limit = 16 if dialogue else 20
            del history[:-limit]

    def last_assistant_answer(self, *, dialogue: bool) -> str:
        lock = self.dialogue_lock if dialogue else self.chat_lock
        with lock:
            history = self.dialogue_history if dialogue else self.chat_history
            return next((str(item["content"]) for item in reversed(history) if item.get("role") == "assistant"), "")

    def clear_history(self) -> dict:
        self.clear_laboratory_session()
        with self.chat_lock:
            chat_turns = len(self.chat_history)
            self.chat_history.clear()
        with self.dialogue_lock:
            dialogue_turns = len(self.dialogue_history)
            self.dialogue_history.clear()
        return {"ok": True, "cleared": True, "chat_items": chat_turns, "dialogue_items": dialogue_turns}

    def reset(self) -> dict:
        with self.dialogue_lock:
            self.dialogue_history.clear()
        return self.status()

    def status(self) -> dict:
        reasoning = self.effective_reasoning(dialogue=True)
        services = self.services_status()
        return {
            "ok": True,
            "stt": services["stt"],
            "tts": services["tts"],
            "chat": services["chat"],
            "external_research": services["external_research"],
            "services": services,
            "turns": len(self.dialogue_history),
            "reasoning": self.reasoning_status(),
            "laboratory_mode": self.laboratory_mode_status(),
            "dialogue_reasoning": {
                **self.conversation.reasoning_status(reasoning["applied"]),
                "requested_mode": reasoning["requested"],
                "applied_mode": reasoning["applied"],
                "degraded": reasoning["degraded"],
                "degraded_reason": reasoning["reason"],
            },
        }

    def reasoning_status(self) -> dict:
        info = self.conversation.reasoning_status(self.reasoning_mode)
        info["selected"] = info.get("mode") == self.reasoning_mode
        return info

    def laboratory_mode_status(self) -> dict:
        mode = "free" if str(self.laboratory_mode or "").strip().lower() == "free" else "document"
        return {
            "mode": mode,
            "label": "Modo libre" if mode == "free" else "Anclado al texto",
            "description": (
                "Lucy puede conversar libremente aunque el tema no dependa del texto; el documento queda como contexto opcional."
                if mode == "free"
                else "Lucy prioriza lo que ves, el texto activo y los documentos cargados."
            ),
            "selected": True,
        }

    def profile_status(self) -> dict:
        mode = "bohemia" if str(self.profile or "").strip().lower() == "bohemia" else "academica"
        return {
            "mode": mode,
            "label": "Bohemia" if mode == "bohemia" else "Académica",
            "description": (
                "Lucy Bohemia: más libre, literaria y directa. Menos escolar, más exploratoria."
                if mode == "bohemia"
                else "Lucy Académica: seria, formal, precisa y orientada al estudio riguroso."
            ),
            "selected": True,
        }

    def set_reasoning_mode(self, mode: str) -> dict:
        profile = self.conversation.reasoning_status(mode)
        self.reasoning_mode = str(profile.get("mode") or self.reasoning_mode or "thinking")
        if self.reasoning_mode == "contrapunto":
            self.reasoning_mode = "pensamiento_critico"
        self.persist()
        self.trace(
            {
                "ts": time.time(),
                "event": "reasoning_mode_changed",
                "requested_mode": str(mode or ""),
                "selected_mode": self.reasoning_mode,
                "dialogue_allow_supreme": self.dialogue_allow_supreme,
            }
        )
        out = self.reasoning_status()
        dialogue_reasoning = self.effective_reasoning(dialogue=True)
        out["dialogue_reasoning"] = {
            **self.conversation.reasoning_status(dialogue_reasoning["applied"]),
            "requested_mode": dialogue_reasoning["requested"],
            "applied_mode": dialogue_reasoning["applied"],
            "degraded": dialogue_reasoning["degraded"],
            "degraded_reason": dialogue_reasoning["reason"],
        }
        return out

    def set_laboratory_mode(self, mode: str) -> dict:
        self.laboratory_mode = "free" if str(mode or "").strip().lower() == "free" else "document"
        self.persist()
        self.trace({"ts": time.time(), "event": "laboratory_mode_changed", "selected_mode": self.laboratory_mode})
        return self.laboratory_mode_status()

    def set_profile(self, mode: str) -> dict:
        self.profile = "bohemia" if str(mode or "").strip().lower() == "bohemia" else "academica"
        self.persist()
        self.trace({"ts": time.time(), "event": "profile_changed", "selected_mode": self.profile})
        return self.profile_status()

    @staticmethod
    def veil_catalog() -> list[dict]:
        return build_veil_catalog()

    def veil_status(self) -> dict:
        mode = str(self.veil or "lucy").strip().lower()
        catalog = self.veil_catalog()
        item = next((value for value in catalog if value["mode"] == mode), catalog[0])
        return {**item, "available": catalog}

    def set_veil(self, mode: str) -> dict:
        clean = str(mode or "lucy").strip().lower()
        catalog = self.veil_catalog()
        item = next((value for value in catalog if value["mode"] == clean), catalog[0])
        self.veil = item["mode"]
        self.persist()
        self.trace({"ts": time.time(), "event": "veil_changed", "selected_mode": self.veil})
        return self.veil_status()


__all__ = ["DialogueService"]
