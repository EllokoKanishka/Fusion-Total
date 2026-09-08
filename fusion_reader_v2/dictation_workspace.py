from __future__ import annotations

import io
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from .services.persistence import AtomicJSONStore

MAX_PROJECTS = 200
MAX_PROJECT_TEXT_CHARS = 2_000_000
MAX_ACTIVITY_ITEMS = 50
MAX_ACTIVITY_CHARS = 800
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def _clean_title(value: object) -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    return title[:160] or "Dictado sin título"


def _clean_project_id(value: object) -> str:
    candidate = str(value or "").strip()
    return candidate if _PROJECT_ID_RE.fullmatch(candidate) else uuid.uuid4().hex


def _clean_activity(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:MAX_ACTIVITY_ITEMS]:
        clean = re.sub(r"\s+", " ", str(item or "")).strip()
        if clean:
            out.append(clean[:MAX_ACTIVITY_CHARS])
    return out


def _derived_project_title(title: str, text: str) -> str:
    clean = _clean_title(title)
    if clean.casefold() != "dictado sin título".casefold():
        return clean
    words = re.findall(r"\S+", str(text or "").strip())[:9]
    if not words:
        return clean
    label = " ".join(words)
    return f"{label[:72]}{'…' if len(label) > 72 else ''}"


@dataclass(frozen=True)
class DictationProject:
    project_id: str
    title: str
    text: str
    voice: str
    assistant: str
    commands_enabled: bool
    pdf_page_numbers: bool
    selection_start: int
    selection_end: int
    activity: list[str]
    created_ts: float
    updated_ts: float

    @classmethod
    def from_payload(cls, payload: dict, previous: DictationProject | None = None) -> DictationProject:
        text = str(payload.get("text") or "")
        if len(text) > MAX_PROJECT_TEXT_CHARS:
            raise ValueError("dictation_project_too_large")
        now = time.time()
        project_id = previous.project_id if previous else _clean_project_id(payload.get("project_id"))
        created_ts = previous.created_ts if previous else float(payload.get("created_ts") or now)
        start = max(0, min(int(payload.get("selection_start") or 0), len(text)))
        end = max(start, min(int(payload.get("selection_end") or start), len(text)))
        return cls(
            project_id=project_id,
            title=_clean_title(payload.get("title")),
            text=text,
            voice=str(payload.get("voice") or "").strip()[:240],
            assistant=str(payload.get("assistant") or "rules").strip()[:80] or "rules",
            commands_enabled=bool(payload.get("commands_enabled", True)),
            pdf_page_numbers=bool(payload.get("pdf_page_numbers", True)),
            selection_start=start,
            selection_end=end,
            activity=_clean_activity(payload.get("activity")),
            created_ts=created_ts,
            updated_ts=now,
        )

    @classmethod
    def from_dict(cls, data: dict) -> DictationProject:
        text = str(data.get("text") or "")[:MAX_PROJECT_TEXT_CHARS]
        start = max(0, min(int(data.get("selection_start") or 0), len(text)))
        end = max(start, min(int(data.get("selection_end") or start), len(text)))
        created_ts = float(data.get("created_ts") or time.time())
        return cls(
            project_id=_clean_project_id(data.get("project_id")),
            title=_clean_title(data.get("title")),
            text=text,
            voice=str(data.get("voice") or "").strip()[:240],
            assistant=str(data.get("assistant") or "rules").strip()[:80] or "rules",
            commands_enabled=bool(data.get("commands_enabled", True)),
            pdf_page_numbers=bool(data.get("pdf_page_numbers", True)),
            selection_start=start,
            selection_end=end,
            activity=_clean_activity(data.get("activity")),
            created_ts=created_ts,
            updated_ts=float(data.get("updated_ts") or created_ts),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        clean_text = self.text.strip()
        words = re.findall(r"\S+", clean_text)
        preview = re.sub(r"\s+", " ", clean_text)[:150]
        return {
            "project_id": self.project_id,
            "title": _derived_project_title(self.title, self.text),
            "raw_title": self.title,
            "preview": preview,
            "characters": len(self.text),
            "words": len(words),
            "voice": self.voice,
            "assistant": self.assistant,
            "commands_enabled": self.commands_enabled,
            "pdf_page_numbers": self.pdf_page_numbers,
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
        }


class DictationProjectStore:
    def __init__(self, path: Path | str | None = "runtime/fusion_reader_v2/dictation_projects.json") -> None:
        self.path = Path(path) if path is not None else None
        self._store = (
            AtomicJSONStore(self.path, schema_version=1, max_bytes=64 * 1024 * 1024)
            if self.path is not None
            else None
        )
        self._memory: dict = {"schema_version": 1, "projects": []}
        self._lock = threading.RLock()

    def list(self) -> list[dict]:
        with self._lock:
            projects = self._read()
        projects.sort(key=lambda item: (item.updated_ts, item.created_ts, item.project_id), reverse=True)
        return [item.summary() for item in projects]

    def load(self, project_id: str) -> dict:
        wanted = str(project_id or "").strip()
        with self._lock:
            for project in self._read():
                if project.project_id == wanted:
                    return project.to_dict()
        raise KeyError("dictation_project_not_found")

    def save(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("dictation_project_payload_must_be_object")
        requested_id = str(payload.get("project_id") or "").strip()
        with self._lock:
            projects = self._read()
            previous = next((item for item in projects if item.project_id == requested_id), None) if requested_id else None
            if previous is None and len(projects) >= MAX_PROJECTS:
                raise ValueError("dictation_project_limit_reached")
            project = DictationProject.from_payload(payload, previous=previous)
            projects = [item for item in projects if item.project_id != project.project_id]
            projects.append(project)
            self._write(projects)
        return {"project": project.to_dict(), "summary": project.summary()}

    def delete(self, project_id: str) -> dict:
        wanted = str(project_id or "").strip()
        with self._lock:
            projects = self._read()
            remaining = [item for item in projects if item.project_id != wanted]
            if len(remaining) == len(projects):
                raise KeyError("dictation_project_not_found")
            self._write(remaining)
        return {"project_id": wanted, "deleted": True}

    def _read(self) -> list[DictationProject]:
        payload = self._store.read({"projects": []}) if self._store is not None else dict(self._memory)
        raw = payload.get("projects") if isinstance(payload, dict) else []
        if not isinstance(raw, list):
            return []
        projects: list[DictationProject] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            project = DictationProject.from_dict(item)
            if project.project_id in seen:
                continue
            seen.add(project.project_id)
            projects.append(project)
        return projects

    def _write(self, projects: list[DictationProject]) -> None:
        payload = {"projects": [item.to_dict() for item in projects]}
        if self._store is not None:
            self._store.write(payload)
        else:
            self._memory = {"schema_version": 1, **payload}


def _register_pdf_fonts() -> tuple[str, str]:
    candidates = (
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf"),
        ),
    )
    for regular, bold in candidates:
        if not regular.exists() or not bold.exists():
            continue
        regular_name = "FusionDictationUnicode"
        bold_name = "FusionDictationUnicodeBold"
        if regular_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(regular_name, str(regular)))
        if bold_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(bold_name, str(bold)))
        return regular_name, bold_name
    return "Helvetica", "Helvetica-Bold"


def render_dictation_pdf(title: str, text: str, *, page_numbers: bool = True) -> bytes:
    clean_text = str(text or "").strip()
    if not clean_text:
        raise ValueError("empty_dictation")
    if len(clean_text) > MAX_PROJECT_TEXT_CHARS:
        raise ValueError("dictation_project_too_large")

    regular_font, bold_font = _register_pdf_fonts()
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=25.4 * mm,
        leftMargin=25.4 * mm,
        topMargin=25.4 * mm,
        bottomMargin=25.4 * mm,
        title=_clean_title(title),
        author="Panda Fusión",
        pageCompression=1,
    )
    title_style = ParagraphStyle(
        "DictationTitle",
        fontName=bold_font,
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#17201c"),
        spaceAfter=7 * mm,
    )
    body_style = ParagraphStyle(
        "DictationBody",
        fontName=regular_font,
        fontSize=11,
        leading=22,
        alignment=TA_LEFT,
        firstLineIndent=12.7 * mm,
        textColor=colors.HexColor("#17201c"),
        spaceAfter=4 * mm,
        allowWidows=1,
        allowOrphans=1,
    )
    story = [
        Paragraph(escape(_clean_title(title)), title_style),
        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#9ca79f"), spaceAfter=7 * mm),
    ]
    blocks = re.split(r"\n\s*\n+", clean_text)
    for block in blocks:
        clean = block.strip()
        if not clean:
            continue
        safe = escape(clean).replace("\n", "<br/>")
        story.append(Paragraph(safe, body_style))
        story.append(Spacer(1, 1.5 * mm))

    def draw_page_number(canvas, doc) -> None:
        if not page_numbers:
            return
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#5f6a64"))
        canvas.setFont(regular_font, 9)
        canvas.drawRightString(A4[0] - 25.4 * mm, A4[1] - 14 * mm, str(doc.page))
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page_number, onLaterPages=draw_page_number)
    return output.getvalue()


__all__ = [
    "DictationProject",
    "DictationProjectStore",
    "MAX_ACTIVITY_ITEMS",
    "MAX_PROJECTS",
    "MAX_PROJECT_TEXT_CHARS",
    "render_dictation_pdf",
]
