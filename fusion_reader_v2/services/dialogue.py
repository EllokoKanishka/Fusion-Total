from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol


class ReasoningCatalog(Protocol):
    def reasoning_status(self, mode: str = "") -> dict: ...


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
        self.chat_lock = threading.Lock()
        self.chat_history: list[dict] = []
        self.dialogue_lock = threading.Lock()
        self.dialogue_history: list[dict] = []

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

    def remember_chat_turn(self, user_message: str, assistant_answer: str) -> None:
        with self.chat_lock:
            if user_message:
                self.chat_history.append({"role": "user", "content": user_message})
            if assistant_answer:
                self.chat_history.append({"role": "assistant", "content": assistant_answer})
            del self.chat_history[:-20]

    def clear_history(self) -> dict:
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
        return [
            {"mode": "lucy", "label": "Lucy", "description": ""},
            {"mode": "nocturna", "label": "Nocturna", "description": "Conversación de madrugada, cercana y lenta."},
            {"mode": "critica", "label": "Crítica", "description": "Busca la tensión real y el punto débil."},
            {"mode": "sombra", "label": "Sombra", "description": "Busca el deseo, miedo o autoengaño subyacente."},
            {
                "mode": "confesional",
                "label": "Confesional",
                "description": "Habla desde Lucy cuando aclare la conversación.",
            },
            {"mode": "taller", "label": "Taller", "description": "Piensa con el lector para fabricar una idea mejor."},
            {"mode": "debate", "label": "Debate", "description": "Discute y objeta sin complacencia."},
            {"mode": "evocadora", "label": "Evocadora", "description": "Usa una imagen precisa para pensar mejor."},
            {"mode": "directa", "label": "Directa", "description": "Responde de forma frontal y sin rodeos."},
            {"mode": "incomoda", "label": "Incómoda", "description": "Muestra lo que la idea no quiere aceptar."},
            {"mode": "rigurosa", "label": "Rigurosa", "description": "Ordena el argumento y marca lo no sostenido."},
            {"mode": "intima", "label": "Íntima", "description": "Piensa al lado del lector con cercanía."},
            {
                "mode": "bar_filosofico",
                "label": "Bar filosófico",
                "description": "Discusión lúcida, cercana y con filo.",
            },
            {"mode": "desarme", "label": "Desarme", "description": "Desarma qué afirma, oculta y no sostiene."},
            {
                "mode": "pregunta_viva",
                "label": "Pregunta viva",
                "description": "Deja la idea abierta con una pregunta.",
            },
        ]

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
