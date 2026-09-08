from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from fusion_reader_v2 import FusionReaderV2
from fusion_reader_v2.dictation_workspace import DictationProjectStore, render_dictation_pdf


class DictationResponder(Protocol):
    path: str
    headers: object
    context: object

    @property
    def app(self) -> FusionReaderV2: ...

    def _json(self, status: int, payload: dict) -> None: ...

    def _result(self, status: int, payload: dict) -> None: ...

    def _send(self, status: int, content_type: str, raw: bytes) -> None: ...

    def _read_body_to_temp(self, filename: str) -> Path: ...


def _assistant_status(responder: DictationResponder, payload: dict) -> dict:
    out = dict(payload)
    status = getattr(getattr(responder, "context", None), "dictation_model_install_status", None)
    if callable(status):
        out["installation"] = status()
    warm_status = getattr(getattr(responder, "context", None), "dictation_model_warm_status", None)
    if callable(warm_status):
        out["warmup"] = warm_status()
    return out


def _project_store(responder: DictationResponder) -> DictationProjectStore:
    store = getattr(getattr(responder, "context", None), "dictation_projects", None)
    if not isinstance(store, DictationProjectStore):
        raise RuntimeError("dictation_projects_unavailable")
    return store


def handle_dictation_get(responder: DictationResponder, path: str) -> bool:
    if path == "/api/dictation/projects":
        try:
            responder._json(200, {"ok": True, "projects": _project_store(responder).list()})
        except RuntimeError as exc:
            responder._json(503, {"ok": False, "error": str(exc)})
        return True
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
    if path == "/api/dictation/projects":
        action = str(payload.get("action") or "save").strip().lower()
        try:
            store = _project_store(responder)
            if action == "save":
                project_payload = payload.get("project") if isinstance(payload.get("project"), dict) else payload
                responder._json(200, {"ok": True, **store.save(dict(project_payload))})
            elif action == "load":
                responder._json(200, {"ok": True, "project": store.load(str(payload.get("project_id") or ""))})
            elif action == "delete":
                responder._json(200, {"ok": True, **store.delete(str(payload.get("project_id") or ""))})
            else:
                responder._json(400, {"ok": False, "error": "invalid_dictation_project_action"})
        except KeyError as exc:
            responder._json(404, {"ok": False, "error": str(exc.args[0] if exc.args else exc)})
        except (RuntimeError, TypeError, ValueError) as exc:
            responder._json(400 if not isinstance(exc, RuntimeError) else 503, {"ok": False, "error": str(exc)})
        return True
    if path == "/api/dictation/export/pdf":
        try:
            raw = render_dictation_pdf(
                str(payload.get("title") or "Dictado"),
                str(payload.get("text") or ""),
                page_numbers=bool(payload.get("page_numbers", True)),
            )
        except ValueError as exc:
            responder._json(400, {"ok": False, "error": str(exc)})
            return True
        responder._send(200, "application/pdf", raw)
        return True
    if path == "/api/dictation/assistant/warm":
        warm = getattr(getattr(responder, "context", None), "warm_dictation_model", None)
        if not callable(warm):
            result = {"ok": False, "error": "warmup_unavailable", "detail": "Precarga local no disponible."}
        else:
            result = dict(warm() or {})
        responder._json(200 if result.get("ok") else 503, result)
        return True
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
    if path == "/api/dictation/proofread":
        responder._json(200, responder.app.dictation_proofread(str(payload.get("text") or "")))
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
            require_wake_word=bool(payload.get("require_wake_word", False)),
        ),
    )
    return True


__all__ = ["handle_dictation_get", "handle_dictation_post", "handle_dictation_raw_post"]
