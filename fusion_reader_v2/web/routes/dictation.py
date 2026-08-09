from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from fusion_reader_v2 import FusionReaderV2


class DictationResponder(Protocol):
    path: str
    headers: object

    @property
    def app(self) -> FusionReaderV2: ...

    def _json(self, status: int, payload: dict) -> None: ...

    def _result(self, status: int, payload: dict) -> None: ...

    def _read_body_to_temp(self, filename: str) -> Path: ...


def _assistant_status(responder: DictationResponder, payload: dict) -> dict:
    out = dict(payload)
    status = getattr(getattr(responder, "context", None), "dictation_model_install_status", None)
    if callable(status):
        out["installation"] = status()
    return out


def handle_dictation_get(responder: DictationResponder, path: str) -> bool:
    if path != "/api/dictation/assistant":
        return False
    responder._json(200, _assistant_status(responder, responder.app.dictation_assistant_status()))
    return True


def handle_dictation_raw_post(responder: DictationResponder, path: str) -> bool:
    if path != "/api/dictation/transcribe":
        return False
    parsed = urlparse(responder.path)
    params = parse_qs(parsed.query)
    filename = str((params.get("filename") or ["dictation.webm"])[0])
    commands_enabled = str((params.get("commands") or ["1"])[0]).strip().lower() not in {
        "0",
        "false",
        "no",
    }
    content_type = str(responder.headers.get("Content-Type", "") or "")  # type: ignore[attr-defined]
    temporary = responder._read_body_to_temp(filename)
    try:
        responder._json(
            200,
            responder.app.dictation_turn_audio(
                temporary,
                mime=content_type,
                commands_enabled=commands_enabled,
            ),
        )
    finally:
        temporary.unlink(missing_ok=True)
    return True


def handle_dictation_post(responder: DictationResponder, path: str, payload: dict) -> bool:
    if path == "/api/dictation/assistant/install":
        install = getattr(getattr(responder, "context", None), "start_dictation_model_install", None)
        if not callable(install):
            responder._json(
                503,
                {"ok": False, "error": "installer_unavailable", "detail": "Instalador local no disponible."},
            )
        else:
            result = dict(install() or {})
            responder._json(200 if result.get("ok", True) else 503, result)
        return True
    if path == "/api/dictation/assistant":
        responder._json(
            200,
            _assistant_status(
                responder,
                responder.app.set_dictation_assistant(str(payload.get("provider") or "")),
            ),
        )
        return True
    if path == "/api/dictation/assist":
        responder._json(
            200,
            responder.app.dictation_assist(
                str(payload.get("text") or ""),
                draft=str(payload.get("draft") or ""),
                selection_start=int(payload.get("selection_start") or 0),
                selection_end=int(payload.get("selection_end") or 0),
            ),
        )
        return True
    if path == "/api/dictation/speak":
        responder._result(200, responder.app.dictation_speak(str(payload.get("text") or "")))
        return True
    if path != "/api/dictation/interpret":
        return False
    responder._json(
        200,
        responder.app.dictation_turn_text(
            str(payload.get("text") or ""),
            commands_enabled=bool(payload.get("commands_enabled", True)),
        ),
    )
    return True


__all__ = ["handle_dictation_get", "handle_dictation_post", "handle_dictation_raw_post"]
