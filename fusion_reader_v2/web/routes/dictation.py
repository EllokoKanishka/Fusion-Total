from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from fusion_reader_v2 import FusionReaderV2


class DictationResponder(Protocol):
    path: str
    headers: object

    @property
    def app(self) -> FusionReaderV2: ...

    def _json(self, status: int, payload: dict) -> None: ...

    def _result(self, status: int, payload: dict) -> None: ...

    def _read_body_to_temp(self, filename: str) -> Path: ...

    def _send(self, status: int, content_type: str, raw: bytes) -> None: ...


def _dictation_pdf(title: str, text: str, show_page_numbers: bool) -> bytes:
    """Create a clean, local A4 export without involving any cloud service."""
    from io import BytesIO
    from xml.sax.saxutils import escape

    source = str(text or "").strip()
    if not source:
        raise ValueError("dictation_empty")
    if len(source) > 500_000:
        raise ValueError("dictation_too_large")
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "DictationBody",
        parent=styles["BodyText"],
        fontName="Times-Roman",
        fontSize=11.5,
        leading=17,
        textColor=HexColor("#172a2e"),
        spaceAfter=10,
    )
    heading = ParagraphStyle(
        "DictationHeading",
        parent=styles["Title"],
        fontName="Times-Bold",
        fontSize=20,
        leading=25,
        alignment=TA_CENTER,
        textColor=HexColor("#123d45"),
        spaceAfter=20,
    )
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
        topMargin=2.4 * cm,
        bottomMargin=2.2 * cm,
        title=title,
        author="Panda Fusión",
    )

    def page_number(canvas, document) -> None:
        if not show_page_numbers:
            return
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(HexColor("#52666b"))
        canvas.drawCentredString(A4[0] / 2, 1.15 * cm, str(document.page))
        canvas.restoreState()

    story = [Paragraph(escape(title or "Dictado"), heading), Spacer(1, 0.15 * cm)]
    paragraphs = [part.strip() for part in source.replace("\r\n", "\n").split("\n\n") if part.strip()]
    for part in paragraphs:
        story.append(Paragraph(escape(part).replace("\n", "<br/>"), body))
    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return buffer.getvalue()


def _assistant_status(responder: DictationResponder, payload: dict) -> dict:
    out = dict(payload)
    status = getattr(getattr(responder, "context", None), "dictation_model_install_status", None)
    if callable(status):
        out["installation"] = status()
    warm_status = getattr(getattr(responder, "context", None), "dictation_model_warm_status", None)
    if callable(warm_status):
        out["warmup"] = warm_status()
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
    if path == "/api/dictation/export/pdf":
        title = str(payload.get("title") or "Dictado").strip()[:160] or "Dictado"
        try:
            raw = _dictation_pdf(title, str(payload.get("text") or ""), bool(payload.get("page_numbers", True)))
        except ValueError as exc:
            responder._json(400, {"ok": False, "error": str(exc), "detail": "No hay texto válido para exportar."})
        else:
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
