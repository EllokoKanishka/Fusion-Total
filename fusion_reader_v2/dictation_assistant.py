from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from .conversation import ChatProvider
from .dictation import DictationInstruction


_ALLOWED_KINDS = {
    "insert",
    "replace",
    "replace_selection",
    "delete",
    "delete_from",
    "delete_last_words",
    "replace_last_words",
    "undo",
    "redo",
    "read",
    "stop_listening",
    "noop",
}
_ALLOWED_READ_SCOPES = {
    "all",
    "selection",
    "last_page",
    "last_paragraph",
    "current_paragraph",
    "previous_paragraph",
    "paragraph_number",
    "paragraph_matching",
    "from_cursor",
    "from_text",
}
_INSTRUCTION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": sorted(_ALLOWED_KINDS)},
        "text": {"type": "string"},
        "target": {"type": "string"},
        "scope": {"type": "string"},
        "number": {"type": "integer"},
        "all_matches": {"type": "boolean"},
    },
    "required": ["kind", "text", "target", "scope", "number", "all_matches"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class DictationAssistantResult:
    ok: bool
    instruction: DictationInstruction = DictationInstruction("noop")
    provider: str = "rules"
    model: str = ""
    detail: str = ""
    duration_ms: int = 0
    load_duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "instruction": self.instruction.to_dict(),
            "assistant_provider": self.provider,
            "assistant_model": self.model,
            "assistant_detail": self.detail,
            "assistant_ms": self.duration_ms,
            "assistant_load_ms": self.load_duration_ms,
        }


class DictationAssistant:
    """Route an explicit Lucy command to a bounded editor-operation classifier."""

    def __init__(
        self,
        providers: dict[str, ChatProvider] | None = None,
        *,
        selected: str = "rules",
        max_context_chars: int = 12_000,
    ) -> None:
        self.providers = dict(providers or {})
        self.selected = selected if selected in {"rules", *self.providers} else "rules"
        self.max_context_chars = max(1_000, int(max_context_chars))

    def select(self, provider_id: str) -> str:
        clean = str(provider_id or "").strip().lower()
        if clean not in {"rules", *self.providers}:
            raise ValueError("invalid_dictation_assistant")
        self.selected = clean
        return clean

    def catalog(self) -> list[dict]:
        items = [
            {
                "id": "rules",
                "label": "Reglas instantáneas",
                "model": "",
                "cloud": False,
                "description": "Sin IA; ejecuta órdenes conocidas sin cargar modelos.",
            }
        ]
        labels = {
            "local": "IA local ligera",
            "openai": "OpenAI nano",
        }
        descriptions = {
            "local": "Usa Ollama sólo cuando las reglas no alcanzan.",
            "openai": "Envía la orden y un contexto acotado a OpenAI mediante OpenClaw.",
        }
        for provider_id, provider in self.providers.items():
            items.append(
                {
                    "id": provider_id,
                    "label": labels.get(provider_id, provider_id),
                    "model": str(getattr(provider, "default_model", "") or ""),
                    "cloud": provider_id == "openai",
                    "description": descriptions.get(provider_id, "Clasificador de órdenes acotadas."),
                }
            )
        return items

    def status(self) -> dict:
        catalog = self.catalog()
        active = next((item for item in catalog if item["id"] == self.selected), catalog[0])
        if self.selected == "rules":
            health = {"ok": True, "detail": "ready", "provider": "rules"}
        else:
            provider = self.providers[self.selected]
            health = dict(provider.health() or {})
            if health.get("model_present") is False:
                health["ok"] = False
                health["detail"] = "model_not_installed"
        return {
            "ok": True,
            **active,
            "selected": self.selected,
            "ready": bool(health.get("ok")),
            "detail": str(health.get("detail") or ""),
            "active_provider": str(health.get("provider") or self.selected),
            "available": catalog,
        }

    def interpret(
        self,
        command: str,
        *,
        draft: str = "",
        selection_start: int = 0,
        selection_end: int = 0,
    ) -> DictationAssistantResult:
        started = time.perf_counter()
        if self.selected == "rules":
            return DictationAssistantResult(
                False,
                provider="rules",
                detail="rules_only",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        provider = self.providers.get(self.selected)
        if provider is None:
            return DictationAssistantResult(False, provider=self.selected, detail="assistant_unavailable")

        clean_command = " ".join(str(command or "").strip().split())
        clean_draft = str(draft or "")[: self.max_context_chars]
        start = max(0, min(int(selection_start or 0), len(clean_draft)))
        end = max(start, min(int(selection_end or start), len(clean_draft)))
        messages = self._messages(clean_command, clean_draft, start, end)
        structured_chat = getattr(provider, "chat_structured", None)
        if callable(structured_chat):
            result = structured_chat(
                messages,
                schema=_INSTRUCTION_SCHEMA,
                think=False,
                num_predict=320,
                keep_alive="10m",
            )
        else:
            result = provider.chat(messages, think=False, num_predict=320)
        duration_ms = result.duration_ms or int((time.perf_counter() - started) * 1000)
        if not result.ok:
            return DictationAssistantResult(
                False,
                provider=self.selected,
                model=result.model,
                detail=result.detail or "assistant_failed",
                duration_ms=duration_ms,
                load_duration_ms=result.load_duration_ms,
            )
        instruction, detail = self._parse_instruction(result.answer)
        if instruction is None:
            return DictationAssistantResult(
                False,
                provider=self.selected,
                model=result.model,
                detail=detail,
                duration_ms=duration_ms,
                load_duration_ms=result.load_duration_ms,
            )
        return DictationAssistantResult(
            True,
            instruction=instruction,
            provider=self.selected,
            model=result.model,
            detail="classified",
            duration_ms=duration_ms,
            load_duration_ms=result.load_duration_ms,
        )

    @staticmethod
    def _messages(command: str, draft: str, selection_start: int, selection_end: int) -> list[dict]:
        schema = json.dumps(_INSTRUCTION_SCHEMA, ensure_ascii=False, separators=(",", ":"))
        system = (
            "Sos un clasificador de órdenes editoriales en castellano. No converses ni expliques. "
            "Devolvé un único objeto JSON y nada más. La orden ya fue invocada con Lucy. "
            "Elegí solamente una operación del esquema permitido. Usá texto exacto del CONTEXTO para target. "
            "delete_from significa borrar desde target hasta el final; delete_last_words usa number; "
            "replace_last_words usa number y text. "
            "replace_selection sólo si hay selección. "
            "Para reescribir un párrafo sin selección, devolvé replace con el párrafo exacto en target y la nueva versión en text. "
            "Si la intención o el ancla no son seguras, devolvé kind=noop. Nunca devuelvas el borrador completo. "
            f"Esquema: {schema}"
        )
        selected = draft[selection_start:selection_end]
        user = (
            f"ORDEN:\n{command}\n\n"
            f"SELECCIÓN ({selection_start}:{selection_end}):\n{selected}\n\n"
            f"CONTEXTO DEL BORRADOR:\n{draft}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @classmethod
    def _parse_instruction(cls, raw: str) -> tuple[DictationInstruction | None, str]:
        clean = str(raw or "").strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", clean, flags=re.IGNORECASE | re.DOTALL)
        if fenced:
            clean = fenced.group(1).strip()
        try:
            payload = json.loads(clean)
        except (TypeError, ValueError):
            return None, "assistant_invalid_json"
        if not isinstance(payload, dict):
            return None, "assistant_invalid_instruction"
        allowed_fields = {"kind", "text", "target", "scope", "number", "all_matches"}
        if set(payload) - allowed_fields:
            return None, "assistant_invalid_instruction"
        kind = str(payload.get("kind") or "noop").strip().lower()
        if kind not in _ALLOWED_KINDS:
            return None, "assistant_disallowed_instruction"
        text = str(payload.get("text") or "")
        target = str(payload.get("target") or "").strip()
        scope = str(payload.get("scope") or "").strip().lower()
        if len(text) > 12_000 or len(target) > 2_000:
            return None, "assistant_instruction_too_large"
        if not isinstance(payload.get("all_matches", False), bool):
            return None, "assistant_invalid_instruction"
        if isinstance(payload.get("number", 0), bool):
            return None, "assistant_invalid_instruction"
        try:
            number = max(0, int(payload.get("number") or 0))
        except (TypeError, ValueError):
            return None, "assistant_invalid_instruction"
        all_matches = bool(payload.get("all_matches", False))
        if kind in {"replace", "delete", "delete_from"} and not target:
            return None, "assistant_missing_target"
        if kind in {"delete_last_words", "replace_last_words"} and not 0 < number <= 10_000:
            return None, "assistant_invalid_word_count"
        if kind in {"insert", "replace", "replace_selection", "replace_last_words"} and not text:
            return None, "assistant_missing_text"
        if kind == "read":
            if scope not in _ALLOWED_READ_SCOPES:
                return None, "assistant_invalid_read_scope"
            if scope in {"paragraph_matching", "from_text"} and not target:
                return None, "assistant_missing_target"
        return (
            DictationInstruction(
                kind=kind,
                text=text,
                target=target,
                scope=scope,
                number=number,
                all_matches=all_matches,
            ),
            "",
        )


__all__ = ["DictationAssistant", "DictationAssistantResult"]
