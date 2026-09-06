from __future__ import annotations

import html
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from .dialogue import TranscriptSegment
from .owned_subprocess import run_owned


SUPPORTED_MEDIA_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".avi",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".wma",
    ".wmv",
}


@dataclass(frozen=True)
class MediaProbe:
    duration_seconds: float
    format_name: str
    audio_codec: str


@dataclass
class MediaJob:
    job_id: str
    operation: str
    filename: str
    mime: str
    voice: str
    original_pdf_requested: bool = True
    translated_pdf_requested: bool = True
    spanish_audio_requested: bool = True
    state: str = "queued"
    stage: str = "queued"
    progress: int = 0
    detail: str = "Archivo recibido."
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_requested: bool = False
    detected_language: str = ""
    duration_seconds: float = 0.0
    transcript: str = ""
    translated_text: str = ""
    transcript_path: str = ""
    translated_path: str = ""
    pdf_path: str = ""
    translated_pdf_path: str = ""
    audio_path: str = ""
    pdf_download_url: str = ""
    translated_pdf_download_url: str = ""
    audio_download_url: str = ""
    mounted: bool = False
    provider: str = ""
    correction_requested: bool = False
    correction_completed: bool = False
    correction_model: str = ""
    correction_processed_paragraphs: int = 0
    correction_accepted_paragraphs: int = 0
    correction_unchanged_paragraphs: int = 0
    correction_changed_paragraphs: int = 0
    correction_rejected_paragraphs: int = 0
    media_format: str = ""
    audio_codec: str = ""
    timings: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    paragraph_count: int = 0
    audio_chunk_count: int = 0

    @property
    def terminal(self) -> bool:
        return self.state in {"done", "partial", "cancelled", "error"}

    def to_dict(self) -> dict:
        output = {}
        if self.pdf_path:
            output["pdf"] = {"filename": Path(self.pdf_path).name, "download_url": self.pdf_download_url}
        if self.translated_pdf_path:
            output["translated_pdf"] = {
                "filename": Path(self.translated_pdf_path).name,
                "download_url": self.translated_pdf_download_url,
            }
        if self.audio_path:
            output["audio"] = {"filename": Path(self.audio_path).name, "download_url": self.audio_download_url}
        elapsed = max(0.0, time.time() - self.created_at)
        eta = 0.0
        if self.state in {"queued", "running", "canceling"} and 0 < self.progress < 100:
            eta = elapsed * (100 - self.progress) / self.progress
        return {
            "ok": self.state != "error",
            "job_id": self.job_id,
            "id": self.job_id,
            "type": "media_processing",
            "operation": self.operation,
            "filename": self.filename,
            "voice": self.voice,
            "requested_outputs": {
                "original_pdf": self.original_pdf_requested,
                "translated_pdf": self.operation == "translate" and self.translated_pdf_requested,
                "spanish_audio": self.operation == "translate" and self.spanish_audio_requested,
            },
            "state": self.state,
            "stage": self.stage,
            "progress": self.progress,
            "detail": self.detail,
            "error": self.error,
            "terminal": self.terminal,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "detected_language": self.detected_language,
            "duration_seconds": round(self.duration_seconds, 3),
            "provider": self.provider,
            "correction": {
                "requested": self.correction_requested,
                "completed": self.correction_completed,
                "model": self.correction_model,
                "processed_paragraphs": self.correction_processed_paragraphs,
                "accepted_paragraphs": self.correction_accepted_paragraphs,
                "unchanged_paragraphs": self.correction_unchanged_paragraphs,
                "changed_paragraphs": self.correction_changed_paragraphs,
                "rejected_paragraphs": self.correction_rejected_paragraphs,
            },
            "media_format": self.media_format,
            "audio_codec": self.audio_codec,
            "timings": dict(self.timings),
            "warnings": list(self.warnings),
            "paragraph_count": self.paragraph_count,
            "audio_chunk_count": self.audio_chunk_count,
            "elapsed_seconds": round(elapsed, 1),
            "eta_seconds": round(eta, 1),
            "transcript_characters": len(self.transcript),
            "translated_characters": len(self.translated_text),
            "preview": (self.translated_text or self.transcript)[:500],
            "mounted": self.mounted,
            "output": output,
        }


def safe_media_stem(filename: str) -> str:
    stem = Path(str(filename or "conferencia")).stem or "conferencia"
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "conferencia"
    return clean[:120]


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def probe_media(path: Path, *, timeout_seconds: float = 30.0, cancel_event=None) -> MediaProbe:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe_not_available")
    proc = run_owned(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        cancel_event=cancel_event,
    )
    if proc.returncode != 0:
        raise ValueError("media_unreadable")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("media_probe_invalid") from exc
    streams = [item for item in data.get("streams") or [] if isinstance(item, dict)]
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not audio:
        raise ValueError("media_without_audio")
    format_info = data.get("format") if isinstance(data.get("format"), dict) else {}
    try:
        duration_seconds = float(format_info.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration_seconds = 0.0
    return MediaProbe(
        duration_seconds=duration_seconds,
        format_name=str(format_info.get("format_name") or ""),
        audio_codec=str(audio.get("codec_name") or ""),
    )


def normalize_media_audio(source: Path, target: Path, *, timeout_seconds: float, cancel_event=None) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg_not_available")
    proc = run_owned(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "flac",
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        cancel_event=cancel_event,
    )
    if proc.returncode != 0 or not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError("media_audio_conversion_failed")


def transcript_paragraphs(segments: tuple[TranscriptSegment, ...], text: str) -> list[tuple[float, str]]:
    if not segments:
        return [(0.0, item) for item in _split_plain_transcript(text)]
    paragraphs: list[tuple[float, str]] = []
    current: list[str] = []
    start = 0.0
    for segment in segments:
        clean = clean_text(segment.text)
        if not clean:
            continue
        if not current:
            start = segment.start
        current.append(clean)
        joined = " ".join(current)
        if len(joined) >= 520 or segment.end - start >= 35:
            paragraphs.append((start, joined))
            current = []
    if current:
        paragraphs.append((start, " ".join(current)))
    return paragraphs


def transcript_body_text(paragraphs: list[tuple[float, str]]) -> str:
    return "\n\n".join(f"[{format_timestamp(start)}] {text}" for start, text in paragraphs if text).strip()


def transcript_document_text(title: str, language: str, paragraphs: list[tuple[float, str]]) -> str:
    heading = [title.strip() or "Transcripción", f"Idioma detectado: {language or 'desconocido'}"]
    body = transcript_body_text(paragraphs)
    return "\n\n".join([*heading, body] if body else heading).strip()


def clean_text(text: str) -> str:
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split()).strip()


def _split_plain_transcript(text: str, max_chars: int = 900) -> list[str]:
    clean = clean_text(text)
    if not clean:
        return []
    sentences = [item.strip() for item in re.split(r"(?<=[.!?…])\s+", clean) if item.strip()]
    paragraphs: list[str] = []
    current = ""
    for sentence in sentences or [clean]:
        if len(sentence) > max_chars:
            words = sentence.split()
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and len(candidate) > max_chars:
                    paragraphs.append(current)
                    current = word
                else:
                    current = candidate
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            paragraphs.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        paragraphs.append(current)
    return paragraphs


def write_transcript_pdf(
    path: Path,
    *,
    title: str,
    subtitle: str,
    paragraphs: list[tuple[float, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    regular, bold = _register_pdf_fonts()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "FusionTitle",
        parent=styles["Title"],
        fontName=bold,
        fontSize=18,
        leading=23,
        textColor=colors.HexColor("#13221a"),
        alignment=TA_CENTER,
        spaceAfter=8 * mm,
    )
    subtitle_style = ParagraphStyle(
        "FusionSubtitle",
        parent=styles["Normal"],
        fontName=regular,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#52615a"),
        alignment=TA_CENTER,
        spaceAfter=8 * mm,
    )
    time_style = ParagraphStyle(
        "FusionTime",
        parent=styles["Normal"],
        fontName=bold,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#16734a"),
        spaceAfter=1.5 * mm,
    )
    body_style = ParagraphStyle(
        "FusionBody",
        parent=styles["BodyText"],
        fontName=regular,
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor("#17201c"),
        spaceAfter=5 * mm,
    )
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Fusion Reader v2",
    )
    story = [Paragraph(html.escape(title), title_style), Paragraph(html.escape(subtitle), subtitle_style)]
    for index, (start, text) in enumerate(paragraphs):
        if index and index % 50 == 0:
            story.append(PageBreak())
        story.append(Paragraph(html.escape(format_timestamp(start)), time_style))
        story.append(Paragraph(html.escape(text), body_style))
    if not paragraphs:
        story.extend([Spacer(1, 10 * mm), Paragraph("No se obtuvo texto legible.", body_style)])
    document.build(story)


def _register_pdf_fonts() -> tuple[str, str]:
    candidates = [
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if not regular.exists() or not bold.exists():
            continue
        if "FusionUnicode" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("FusionUnicode", str(regular)))
        if "FusionUnicodeBold" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("FusionUnicodeBold", str(bold)))
        return "FusionUnicode", "FusionUnicodeBold"
    return "Helvetica", "Helvetica-Bold"


__all__ = [
    "MediaJob",
    "MediaProbe",
    "SUPPORTED_MEDIA_EXTENSIONS",
    "clean_text",
    "format_timestamp",
    "normalize_media_audio",
    "probe_media",
    "safe_media_stem",
    "transcript_document_text",
    "transcript_paragraphs",
    "write_transcript_pdf",
]
