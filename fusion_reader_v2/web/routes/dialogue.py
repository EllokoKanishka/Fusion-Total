from __future__ import annotations

from typing import Protocol

from fusion_reader_v2 import FusionReaderV2


class DialogueResponder(Protocol):
    @property
    def app(self) -> FusionReaderV2: ...

    def _json(self, status: int, payload: dict) -> None: ...

    def _result(self, status: int, payload: dict) -> None: ...


def handle_dialogue_get(responder: DialogueResponder, path: str) -> bool:
    if path != "/api/dialogue/status":
        return False
    responder._json(200, responder.app.dialogue_status())
    return True


def handle_dialogue_post(responder: DialogueResponder, path: str, payload: dict) -> bool:
    handlers = {
        "/api/dialogue/reset": lambda: responder.app.dialogue_reset(),
        "/api/reasoning/mode": lambda: responder.app.set_reasoning_mode(str(payload.get("mode") or "")),
        "/api/laboratory/mode": lambda: responder.app.set_laboratory_mode(str(payload.get("mode") or "")),
        "/api/profile": lambda: responder.app.set_profile(str(payload.get("mode") or "")),
        "/api/veil": lambda: responder.app.set_veil(str(payload.get("mode") or "")),
        "/api/chat/provider": lambda: responder.app.set_chat_provider(str(payload.get("provider") or "")),
    }
    handler = handlers.get(path)
    if handler is not None:
        responder._json(200, handler())
        return True
    if path in {"/api/laboratory/reset", "/api/chat/reset"}:
        responder._json(200, responder.app.clear_laboratory_history())
        return True
    if path == "/api/dialogue/turn":
        chunk_value = payload.get("chunk_index")
        responder._result(
            200,
            responder.app.dialogue_turn_text(
                str(payload.get("text") or ""),
                model=str(payload.get("model") or ""),
                chunk_index=int(chunk_value) if chunk_value is not None else None,
            ),
        )
        return True
    if path == "/api/chat":
        chunk_value = payload.get("chunk_index")
        responder._result(
            200,
            responder.app.chat(
                str(payload.get("message") or ""),
                model=str(payload.get("model") or ""),
                chunk_index=int(chunk_value) if chunk_value is not None else None,
            ),
        )
        return True
    return False


__all__ = ["handle_dialogue_get", "handle_dialogue_post"]
