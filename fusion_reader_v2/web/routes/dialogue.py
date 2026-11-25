from __future__ import annotations

from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from fusion_reader_v2 import FusionReaderV2
from fusion_reader_v2.turn_provider import LABORATORY_PROVIDERS, laboratory_session_id, use_turn_provider


class DialogueResponder(Protocol):
    @property
    def app(self) -> FusionReaderV2: ...

    def _json(self, status: int, payload: dict) -> None: ...

    def _result(self, status: int, payload: dict) -> None: ...


def handle_dialogue_get(responder: DialogueResponder, path: str, raw_path: str) -> bool:
    if path == "/api/dialogue/status":
        responder._json(200, responder.app.dialogue_status())
    elif path == "/api/chat/openai/usage":
        responder._json(200, responder.app.openai_usage_status())
    elif path == "/api/laboratory/conversations":
        responder._json(200, responder.app.archived_laboratory_conversations())
    elif path == "/api/laboratory/conversation":
        params = parse_qs(urlsplit(raw_path).query)
        session = laboratory_session_id((params.get("session") or [""])[0])
        if not session:
            raise ValueError("invalid_laboratory_session")
        before = (params.get("before") or [""])[0]
        if before and (len(before) > 18 or not before.isascii() or not before.isdecimal() or int(before) < 1):
            raise ValueError("invalid_laboratory_history_cursor")
        page = responder.app.archived_laboratory_turns(session, int(before) if before else None)
        responder._json(200, page)
    else:
        return False
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
        session = laboratory_session_id(payload.get("laboratory_session", ""))
        responder._json(200, responder.app.clear_laboratory_history(session))
        return True
    if path in {"/api/dialogue/turn", "/api/chat"}:
        chunk_value = payload.get("chunk_index")
        generation = payload.get("document_generation")
        dialogue = path == "/api/dialogue/turn"
        text = str(payload.get("text" if dialogue else "message") or "")
        with use_turn_provider(
            payload.get("provider", ""),
            LABORATORY_PROVIDERS,
            "local",
            allow_external_research=payload.get("allow_external_research") is True,
            laboratory_session=payload.get("laboratory_session", ""),
            laboratory_generation=int(generation) if generation is not None else None,
        ):
            scope = responder.app.capture_laboratory_archive_scope()
            run_turn = responder.app.dialogue_turn_text if dialogue else responder.app.chat
            result = run_turn(
                text,
                model=str(payload.get("model") or ""),
                chunk_index=int(chunk_value) if chunk_value is not None else None,
            )
            responder._result(200, responder.app.archive_laboratory_turn(scope, text, result))
        return True
    return False


__all__ = ["handle_dialogue_get", "handle_dialogue_post"]
