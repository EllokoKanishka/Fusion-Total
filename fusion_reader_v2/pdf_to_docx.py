from __future__ import annotations

import re
import os
import subprocess
import zipfile
import tempfile
import time
import shutil
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Iterable, Callable

from .config import environment_value
from .owned_subprocess import OwnedProcessError, run_owned


@dataclass(frozen=True)
class ConversionResult:
    ok: bool
    output_path: str = ""
    pages: int = 0
    warnings: list[str] = field(default_factory=list)
    engine: str = "pdftotext"
    text_blocks: int = 0
    notes_detected: int = 0
    error: str = ""
    noise_lines_removed: int = 0
    paragraphs_merged: int = 0
    headings_detected: int = 0
    low_confidence_pages: int = 0
    ocr_dpi: int = 200
    ocr_psm: int = 6
    cleanup_applied: bool = True


@dataclass
class JobStatus:
    job_id: str
    state: str = "queued"  # queued, running, done, error, cancelled
    stage: str = "preflight"
    current_page: int = 0
    total_pages: int = 0
    percent: int = 0
    message: str = ""
    filename: str = ""
    saved_path: str = ""
    download_url: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    cancelled: bool = False
    result: ConversionResult | None = None
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ParagraphBlock:
    style: str
    text: str


def safe_output_name(input_name: str) -> str:
    original = Path(str(input_name or "documento.pdf")).name
    stem = Path(original).stem or "documento"
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "documento"
    clean = clean[:120]
    return f"{clean}_convertido.docx"


def find_downloads_dir() -> Path:
    home = Path.home()
    preferred = home / "Descargas"
    if preferred.exists():
        return preferred
    alternate = home / "Downloads"
    if alternate.exists():
        return alternate
    return preferred


def is_pdf_textual(path: str | Path) -> bool:
    pdf_path = Path(path)
    for page_text in _extract_pages_text(pdf_path):
        if _has_meaningful_text(page_text):
            return True
    return False


def convert_pdf_to_docx(
    input_pdf: str | Path,
    output_docx: str | Path,
    status_callback: Callable[[JobStatus], None] | None = None,
    job: JobStatus | None = None,
) -> ConversionResult:
    pdf_path = Path(input_pdf)
    output_path = Path(output_docx)
    warnings: list[str] = []

    if job:
        job.state = "running"
        job.stage = "preflight"
        job.message = "Calculando páginas..."
        if status_callback:
            status_callback(job)

    pages = _page_count(pdf_path)
    if job:
        job.total_pages = pages
        job.stage = "preflight"
        job.message = "Verificando motor de conversión..."
        if status_callback:
            status_callback(job)

    # Motor priority:
    # 1. Docling GPU (if available) - Professional quality
    # 2. pdftotext (if textual) - High speed
    # 3. OCR Tesseract (last resort fallback for simple scans)

    if is_docling_gpu_available():
        if job:
            job.stage = "docling_gpu"
            job.message = "Procesando con Docling GPU (Acelerado)..."
            if status_callback:
                status_callback(job)

        try:
            return _convert_with_docling_gpu(pdf_path, output_path, job=job, status_callback=status_callback)
        except Exception as e:
            err_msg = f"Fallo en Docling GPU: {e}"
            if job:
                job.state = "error"
                job.error = err_msg
                if status_callback:
                    status_callback(job)
            return ConversionResult(False, error=err_msg, pages=pages, output_path=str(output_path))

    # Fallback to legacy engines if Docling GPU is not available
    # Initial fast check for textual content
    try:
        page_texts_sample = _extract_pages_text(pdf_path, limit=20)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        err_msg = f"No se pudo extraer texto del PDF: {exc}"
        if job:
            job.state = "error"
            job.error = err_msg
            if status_callback:
                status_callback(job)
        return ConversionResult(False, error=err_msg, pages=pages, output_path=str(output_path))
    meaningful_pages = [text for text in page_texts_sample if _has_meaningful_text(text)]

    if not meaningful_pages:
        # It's a scan and we don't have Docling GPU
        err_msg = "Motor Docling GPU no disponible. No se usó CPU fallback para evitar tiempos de conversión excesivos en este PDF escaneado."
        if job:
            job.state = "error"
            job.error = err_msg
            if status_callback:
                status_callback(job)
        return ConversionResult(False, error=err_msg, pages=pages, output_path=str(output_path))
    else:
        # It's textual, use pdftotext (Legacy fast path)
        if job:
            job.stage = "extract_text"
            job.message = "Extrayendo texto (pdftotext)..."
            if status_callback:
                status_callback(job)

        page_texts = _extract_pages_text(pdf_path)
        engine = "pdftotext"

        if job and job.cancelled:
            return ConversionResult(False, error="Operación cancelada por el usuario.")

        if job:
            job.stage = "build_docx"
            job.message = "Generando archivo DOCX..."
            if status_callback:
                status_callback(job)

        blocks, notes_detected, metrics = build_docx_from_pdf_structure(page_texts, output_path)

        res = ConversionResult(
            True,
            output_path=str(output_path),
            pages=pages or len(page_texts),
            warnings=warnings,
            engine=engine,
            text_blocks=blocks,
            notes_detected=notes_detected,
            noise_lines_removed=metrics.get("noise_removed", 0),
            paragraphs_merged=metrics.get("merged", 0),
            headings_detected=metrics.get("headings", 0),
            low_confidence_pages=metrics.get("low_confidence", 0),
        )
        if job:
            job.result = res
            job.warnings = list(warnings)
            job.state = "done"
        return res


def build_docx_from_pdf_structure(page_texts: Iterable[str], output_docx: str | Path) -> tuple[int, int, dict]:
    blocks: list[ParagraphBlock] = []
    notes_detected = 0
    total_metrics = {"noise_removed": 0, "merged": 0, "headings": 0, "low_confidence": 0}

    for index, page_text in enumerate(page_texts, start=1):
        clean_page = str(page_text or "").replace("\r", "")
        if not _has_meaningful_text(clean_page):
            total_metrics["low_confidence"] += 1
            continue

        blocks.append(ParagraphBlock("Normal", f"--- Página {index} ---"))
        page_blocks, page_notes, metrics = _page_paragraphs(clean_page)

        notes_detected += len(page_notes)
        blocks.extend(page_blocks)

        for k in total_metrics:
            total_metrics[k] += metrics.get(k, 0)

        if page_notes:
            blocks.append(ParagraphBlock("Heading2", f"[Notas de página {index}]"))
            blocks.extend(ParagraphBlock("Quote", note) for note in page_notes)

    _write_minimal_docx(Path(output_docx), blocks)
    return len(blocks), notes_detected, total_metrics


def _page_count(pdf_path: Path) -> int:
    tool = shutil.which("pdfinfo")
    if not tool:
        return 0
    proc = run_owned(
        [tool, str(pdf_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0:
        return 0
    match = re.search(r"^Pages:\s+(\d+)\s*$", proc.stdout, flags=re.MULTILINE)
    return int(match.group(1)) if match else 0


def _extract_pages_text(pdf_path: Path, limit: int | None = None) -> list[str]:
    pages = _page_count(pdf_path)
    if pages <= 0:
        raw = _pdftotext_page(pdf_path, None)
        return [segment for segment in raw.split("\f") if segment is not None]

    total = min(pages, limit) if limit else pages
    out: list[str] = []
    for page in range(1, total + 1):
        out.append(_pdftotext_page(pdf_path, page))
    return out


def _ocr_pdf_pages(
    pdf_path: Path,
    max_pages: int = 300,
    status_callback: Callable[[JobStatus], None] | None = None,
    job: JobStatus | None = None,
) -> list[str]:
    pages = _page_count(pdf_path)
    if pages > max_pages:
        raise ValueError(f"PDF escaneado demasiado largo para OCR v1: {pages} páginas. Límite actual: {max_pages}.")

    if job:
        job.total_pages = pages

    # Check for tesseract and languages
    langs = _tesseract_langs()
    lang_arg = "eng"
    if "spa" in langs:
        lang_arg = "spa+eng" if "eng" in langs else "spa"

    texts = []

    # Process page by page to allow progress and avoid memory/temp disk spikes
    for page_idx in range(1, pages + 1):
        if job and job.cancelled:
            break

        if job:
            job.current_page = page_idx
            job.percent = int((page_idx - 1) * 100 / pages)
            job.message = f"OCR página {page_idx} de {pages}..."
            if status_callback:
                status_callback(job)

        with tempfile.TemporaryDirectory(prefix=f"fusion_ocr_p{page_idx}_") as tmpdir:
            tmp_path = Path(tmpdir)
            prefix = tmp_path / "page"

            try:
                # Render ONLY this page at higher DPI (200 is good balance)
                run_owned(
                    [
                        "pdftoppm",
                        "-png",
                        "-r",
                        "200",
                        "-f",
                        str(page_idx),
                        "-l",
                        str(page_idx),
                        str(pdf_path),
                        str(prefix),
                    ],
                    check=True,
                    timeout=120,
                    capture_output=True,
                )
            except Exception as e:
                texts.append(f"[Error al renderizar página {page_idx}: {e}]")
                continue

            image_files = list(tmp_path.glob("page-*.png"))
            if not image_files:
                texts.append(f"[No se generó imagen para página {page_idx}]")
                continue

            img = image_files[0]

            # Pre-process image with ImageMagick if available
            _preprocess_image(img)

            try:
                # Use PSM 6 (uniform block of text) for better paragraph preservation
                proc = run_owned(
                    ["tesseract", str(img), "stdout", "-l", lang_arg, "--psm", "6"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=120,
                )
                texts.append(proc.stdout)
            except Exception as e:
                texts.append(f"[Error en OCR de página {page_idx}: {e}]")

    if job and job.cancelled:
        raise RuntimeError("Operación cancelada.")

    return texts


def _tesseract_langs() -> list[str]:
    try:
        proc = run_owned(
            ["tesseract", "--list-langs"], timeout=15, capture_output=True, text=True, check=True
        )
        return [line.strip() for line in proc.stdout.splitlines() if line.strip() and not line.startswith("List")]
    except (OSError, subprocess.SubprocessError):
        return ["eng"]


def _preprocess_image(image_path: Path) -> None:
    """Apply ImageMagick filters to improve OCR quality."""
    convert = shutil.which("convert")
    if not convert:
        return
    try:
        # Deskew, normalize, and sharpen
        run_owned(
            [convert, str(image_path), "-deskew", "40%", "-normalize", "-sharpen", "0x1", str(image_path)],
            check=True,
            timeout=30,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _pdftotext_page(pdf_path: Path, page: int | None) -> str:
    tool = shutil.which("pdftotext")
    if not tool:
        raise RuntimeError("pdftotext_not_found")
    cmd = [tool, "-layout"]
    if page is not None:
        cmd.extend(["-f", str(page), "-l", str(page)])
    cmd.extend([str(pdf_path), "-"])
    proc = run_owned(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "pdftotext_failed").strip())
    return proc.stdout


def _has_meaningful_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    alpha = re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñÜü0-9]", "", compact)
    return len(alpha) >= 12


def _page_paragraphs(page_text: str) -> tuple[list[ParagraphBlock], list[str], dict]:
    lines = [line.rstrip() for line in page_text.splitlines()]

    # 1. Clean and filter noise
    clean_lines: list[str] = []
    noise_removed = 0
    for raw_line in lines:
        line = _clean_ocr_line(raw_line)
        if not line:
            continue
        if _is_noise_line(line):
            noise_removed += 1
            continue
        clean_lines.append(line)

    # 2. Extract notes (last lines starting with digit)
    non_empty = [line for line in clean_lines if line.strip()]
    note_candidates: list[str] = []
    if non_empty:
        tail = non_empty[-3:]
        for candidate in tail:
            if re.match(r"^\d+[\])\.\-]?\s+\S+", candidate):
                note_candidates.append(candidate)
    note_set = set(note_candidates)
    filtered_lines = [line for line in clean_lines if line not in note_set]

    # 3. Merge lines into paragraphs
    paragraphs: list[tuple[str, str]] = []  # (style, text)
    current_style = "Normal"
    current_paragraph: list[str] = []
    headings_detected = 0

    for line in filtered_lines:
        heading_style = _detect_heading(line)
        if heading_style:
            # Flush current paragraph
            if current_paragraph:
                paragraphs.append((current_style, _join_paragraph_lines(current_paragraph)))
                current_paragraph = []

            paragraphs.append((heading_style, line))
            headings_detected += 1
            current_style = "Normal"
            continue

        # Decision: should we merge with current?
        if not current_paragraph:
            current_paragraph.append(line)
        else:
            if _should_merge_with_previous(current_paragraph[-1], line):
                current_paragraph.append(line)
            else:
                paragraphs.append((current_style, _join_paragraph_lines(current_paragraph)))
                current_paragraph = [line]

    if current_paragraph:
        paragraphs.append((current_style, _join_paragraph_lines(current_paragraph)))

    blocks = [ParagraphBlock(p[0], p[1]) for p in paragraphs if p[1]]
    metrics = {
        "noise_removed": noise_removed,
        "merged": len(filtered_lines) - len(paragraphs),
        "headings": headings_detected,
    }
    return blocks, note_candidates, metrics


def _clean_ocr_line(line: str) -> str:
    """Normalize whitespace and common OCR artifacts."""
    from fusion_reader_v2.md_to_docx import normalize_spanish_ocr_v4

    # Remove weird chars at start/end
    c = re.sub(r"^[^A-Za-z0-9ÁÉÍÓÚáéíóúÑñÜü\"'¿¡(]+", "", line)
    c = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚáéíóúÑñÜü\"'?!).\]]+$", "", c)
    # Normalize spaces
    c = re.sub(r"\s+", " ", c).strip()
    # Normalize dashes
    c = c.replace("—", "-").replace("–", "-")
    c, _metrics = normalize_spanish_ocr_v4(c)
    return c


def _is_noise_line(line: str) -> bool:
    """Detect lines that look like OCR garbage or scan artifacts."""
    if not line:
        return True
    # Too short with no letters
    if len(line) < 3 and not re.search(r"[A-Za-z0-9]", line):
        return True
    # Just symbols
    if not re.search(r"[A-Za-z0-9]", line):
        return True
    # Repetitive nonsense like "A E A E"
    if re.match(r"^([A-Z0-9]\s?){4,}$", line):
        return True
    # Very high symbol-to-letter ratio
    letters = sum(1 for c in line if c.isalpha())
    if len(line) > 5 and letters / len(line) < 0.3:
        return True
    return False


def _detect_heading(line: str) -> str | None:
    """Identify if a line looks like a title or chapter heading."""
    clean = line.strip()
    # "Capítulo X"
    if re.match(
        r"^(cap[ií]tulo|chapter|parte|secci[oó]n|libro|acto|escena)\s+(\d+|[IVXLCDM]+)\b", clean, flags=re.IGNORECASE
    ):
        return "Heading1"
    # Short uppercase lines
    if 3 < len(clean) <= 60 and clean == clean.upper() and re.search(r"[A-Z]", clean):
        return "Heading1"
    # Title Case short lines without ending punctuation
    if 3 < len(clean) <= 70 and not clean.endswith((".", ";", ":", ",")):
        words = clean.split()
        if len(words) > 0 and all(w[0].isupper() or not w[0].isalpha() for w in words):
            return "Heading2"
    return None


def _should_merge_with_previous(prev: str, curr: str) -> bool:
    """Heuristic to decide if current line continues the previous paragraph."""
    p = prev.strip()
    c = curr.strip()
    if not p or not c:
        return False
    # If previous ends with punctuation that usually ends a paragraph
    if p.endswith((".", "!", "?", '"', "»")):
        return False
    # If current starts with uppercase, maybe it's a new sentence but could be same paragraph.
    # But if previous ends in a lowercase or comma, definitely merge.
    if p[-1].islower() or p.endswith((",", ";", ":", "-")):
        return True
    # Default: merge if previous was long enough
    return len(p) > 40


def _join_paragraph_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    merged = lines[0]
    for line in lines[1:]:
        if merged.endswith("-"):
            merged = merged[:-1] + line.lstrip()
        else:
            merged = f"{merged} {line.lstrip()}"
    return re.sub(r"\s+", " ", merged).strip()


def _classify_style(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return "Normal"
    if re.match(r"^(cap[ií]tulo|chapter|parte|secci[oó]n)\b", clean, flags=re.IGNORECASE):
        return "Heading1"
    if re.match(r"^\d+(\.\d+)*\s+\S+", clean):
        return "Heading2"
    if len(clean) <= 70 and clean == clean.upper() and re.search(r"[A-ZÁÉÍÓÚÑÜ]", clean):
        return "Heading1"
    if len(clean) <= 80 and clean[:1].isupper() and not clean.endswith((".", ";", ":", ",")):
        words = clean.split()
        titled = sum(1 for word in words if word[:1].isupper())
        if titled >= max(1, len(words) - 1):
            return "Heading2"
    return "Normal"


def _write_minimal_docx(output_path: Path, blocks: list[ParagraphBlock]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml())
        zf.writestr("_rels/.rels", _rels_xml())
        zf.writestr("docProps/core.xml", _core_xml())
        zf.writestr("docProps/app.xml", _app_xml())
        zf.writestr("word/styles.xml", _styles_xml())
        zf.writestr("word/document.xml", _document_xml(blocks))


def _document_xml(blocks: list[ParagraphBlock]) -> str:
    paragraphs = []
    for block in blocks:
        text = escape(block.text or "")
        style = escape(block.style or "Normal")
        paragraphs.append(
            "<w:p>"
            '<w:pPr><w:pStyle w:val="%s"/></w:pPr>'
            '<w:r><w:t xml:space="preserve">%s</w:t></w:r>'
            "</w:p>" % (style, text)
        )
    body = "".join(paragraphs) or "<w:p><w:r><w:t></w:t></w:r></w:p>"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:w10="urn:schemas-microsoft-com:office:word" '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
        'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
        'xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" '
        'xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" mc:Ignorable="w14 wp14">'
        '<w:body>%s<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body>'
        "</w:document>" % body
    )


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:rPr><w:b/><w:sz w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:rPr><w:b/><w:sz w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Quote">
    <w:name w:val="Quote"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:ind w:left="720"/></w:pPr>
    <w:rPr><w:i/></w:rPr>
  </w:style>
</w:styles>
"""


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""


def _rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""


def _core_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>PDF convertido</dc:title>
  <dc:creator>Fusion Reader v2</dc:creator>
</cp:coreProperties>
"""


def _app_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Fusion Reader v2</Application>
</Properties>
"""


def is_docling_gpu_available() -> bool:
    venv = _get_docling_gpu_env()
    if not venv.exists():
        return False
    python_exe = venv / "bin" / "python3"
    if not python_exe.exists():
        return False

    # Check for CUDA availability inside the venv
    try:
        proc = run_owned(
            [str(python_exe), "-c", "import torch; print(torch.cuda.is_available())"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.stdout.strip() == "True"
    except (OSError, subprocess.SubprocessError):
        return False


def _get_docling_gpu_env() -> Path:
    configured = (environment_value("FUSION_READER_DOCLING_GPU_ENV", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path(__file__).resolve().parents[1]
        / "runtime"
        / "fusion_reader_v2"
        / "pdf_engine_benchmark"
        / "venvs"
        / "docling_gpu_venv"
    )


def _convert_with_docling_gpu(
    pdf_path: Path,
    output_path: Path,
    job: JobStatus | None = None,
    status_callback: Callable[[JobStatus], None] | None = None,
) -> ConversionResult:
    venv = _get_docling_gpu_env()
    docling_bin = venv / "bin" / "docling"
    python_exe = venv / "bin" / "python3"

    with tempfile.TemporaryDirectory(prefix="docling_gpu_job_") as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Run Docling GPU
        # We use --device cuda and output to the temp dir.
        # Docling will create a .md file with the same name as the PDF.
        try:
            if job:
                job.message = "Docling GPU: Ejecutando modelos de visión y OCR..."
                if status_callback:
                    status_callback(job)

            class JobCancellation:
                def is_set(self) -> bool:
                    return bool(job and job.cancelled)

            proc = run_owned(
                [
                    str(docling_bin),
                    "--device",
                    "cuda",
                    "--image-export-mode",
                    "placeholder",
                    str(pdf_path),
                    "--output",
                    str(tmp_path),
                ],
                text=True,
                timeout=float(environment_value("FUSION_READER_DOCLING_TIMEOUT", "1200") or "1200"),
                cancel_event=JobCancellation(),
            )
            if proc.returncode != 0:
                raise RuntimeError(f"Docling falló (code {proc.returncode}): {proc.stderr}")

        except OwnedProcessError as e:
            return ConversionResult(False, error=str(e), output_path=str(output_path))
        except Exception as e:
            raise RuntimeError(f"Error en fase Docling GPU: {e}")

        # Find the generated markdown file
        md_files = list(tmp_path.glob("*.md"))
        if not md_files:
            raise RuntimeError("Docling no generó ningún archivo Markdown.")

        md_file = md_files[0]

        # 2. Convert Markdown to DOCX using our helper script
        if job:
            job.stage = "build_docx"
            job.message = "Generando DOCX desde Markdown estructurado..."
            if status_callback:
                status_callback(job)

        helper_script = Path(__file__).parent / "md_to_docx.py"
        unpublished_docx = tmp_path / "document.docx"
        try:
            run_owned(
                [str(python_exe), str(helper_script), str(md_file), str(unpublished_docx)],
                check=True,
                capture_output=True,
                timeout=120,
                cancel_event=JobCancellation(),
            )
            if not unpublished_docx.is_file():
                raise RuntimeError("md_to_docx_output_missing")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(unpublished_docx, output_path)
        except OwnedProcessError as e:
            return ConversionResult(False, error=str(e), output_path=str(output_path))
        except Exception as e:
            raise RuntimeError(f"Error al convertir Markdown a DOCX: {e}")

        if job:
            job.percent = 100
            job.message = "Conversión completada con Docling GPU."
            if status_callback:
                status_callback(job)

        return ConversionResult(
            True,
            output_path=str(output_path),
            pages=_page_count(pdf_path),
            engine="docling_gpu",
            headings_detected=0,  # We don't count them here, docling did it
            cleanup_applied=False,  # Docling handles its own cleanup
        )
