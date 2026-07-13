from __future__ import annotations

import html
import csv
import functools
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree

from PIL import Image, ImageFilter, ImageOps

from .config import environment_value

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text", ".csv", ".log"}
HTML_SUFFIXES = {".html", ".htm"}
RTF_SUFFIXES = {".rtf"}
PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}
ODT_SUFFIXES = {".odt", ".ott"}
OFFICE_SUFFIXES = {".doc", ".docm", ".dot", ".dotx", ".odt", ".ott", ".sxw", ".pages"}
OFFICE_TEMP_SUFFIXES = {
    ".doc": ".doc",
    ".docm": ".docm",
    ".dot": ".dot",
    ".dotx": ".dotx",
    ".odt": ".odt",
    ".ott": ".ott",
    ".pages": ".pages",
    ".sxw": ".sxw",
}
SUPPORTED_SUFFIXES = (
    TEXT_SUFFIXES | HTML_SUFFIXES | RTF_SUFFIXES | PDF_SUFFIXES | DOCX_SUFFIXES | ODT_SUFFIXES | OFFICE_SUFFIXES
)
OCR_MIN_CONF = 45.0
OCR_WORD_MIN_CONF = 35.0
OCR_DPI = int(environment_value("FUSION_READER_OCR_DPI", "170") or "170")
OCR_WORKERS = max(1, int(environment_value("FUSION_READER_OCR_WORKERS", "4") or "4"))
OCR_STOPWORDS = set(
    "de la el en que y a los las un una se con no por para del al es me mi su lo como le mas más o si pero esta está fue ha he este nuestro señor".split()
)
OCR_SPACING_FIXES = {
    "L a": "La",
    "l a": "la",
    "Cuan do": "Cuando",
    "volun tad": "voluntad",
    "volu tad": "voluntad",
    "cai ga": "caiga",
    "porq ": "porque ",
    "porq\n": "porque\n",
    "he vivi do": "he vivido",
    "he vivi": "he vivido",
    "próxi ma": "próxima",
    "pro: ma": "próxima",
    "campamen to": "campamento",
    "mo nasterio": "monasterio",
    "nue vo": "nuevo",
    "fija mente": "fijamente",
    "compara da": "comparada",
    "ten gan": "tengan",
    "su bido": "subido",
    "pie dad": "piedad",
    "oscuri dad": "oscuridad",
    "seguramen: te": "seguramente",
    "seguramen te": "seguramente",
    "ma nera": "manera",
    "volt tad": "voluntad",
    "pró ma": "próxima",
    "pro ma": "próxima",
    "habitación": "habitación",
}
OCR_REGEX_SPACING_FIXES = (
    (re.compile(r"\bel\s*año\b", re.IGNORECASE), "el año"),
    (re.compile(r"\b([Ee])laño\b"), lambda m: f"{m.group(1)}l año"),
    (re.compile(r"\b([Ee])labad\b"), lambda m: f"{m.group(1)}l abad"),
    (re.compile(r"\b([Aa])lanciano\b"), lambda m: f"{m.group(1)}l anciano"),
    (re.compile(r"\b([Dd])elamor\b"), lambda m: f"{m.group(1)}el amor"),
    (re.compile(r"\b([Dd])elazul\b"), lambda m: f"{m.group(1)}el azul"),
    (re.compile(r"\b([Aa])labad\b"), lambda m: f"{m.group(1)}l abad"),
    (re.compile(r"\bvividodo\b", re.IGNORECASE), "vivido"),
    (re.compile(r"\bCiteaux\b", re.IGNORECASE), "Citeaux"),
    (re.compile(r"\bCíteaux\b"), "Citeaux"),
    (re.compile(r"\bco\s+ferencia\b", re.IGNORECASE), "conferencia"),
    (re.compile(r"\bRez\s+mos\b", re.IGNORECASE), "Rezamos"),
    (re.compile(r"\bempleam\s+el\b", re.IGNORECASE), "empleamos el"),
    (re.compile(r"\bsimplicida\b", re.IGNORECASE), "simplicidad"),
    (re.compile(r"\bpe\s+sólo\b", re.IGNORECASE), "pero sólo"),
    (re.compile(r"\bno\s+cal\s+en\b", re.IGNORECASE), "no caiga en"),
    (re.compile(r"\bE\s+I\s+tercer\b"), "El tercer"),
    (re.compile(r"\b1\s+L\s+ciento\b"), "mil ciento"),
    (re.compile(r"\bpicid\s+que\s+obecleciera\b", re.IGNORECASE), "pidió que obedeciera"),
    (re.compile(r"\beste\s+libr\s+y\b", re.IGNORECASE), "este libro y"),
)
ProgressCallback = Callable[[str, int, int, str], None]


@dataclass(frozen=True)
class ImportedDocument:
    doc_id: str
    title: str
    text: str
    source_type: str
    detail: str = ""
    raw_text: str = ""


def report_progress(
    progress: ProgressCallback | None, stage: str, current: int = 0, total: int = 0, message: str = ""
) -> None:
    if not progress:
        return
    try:
        progress(stage, current, total, message)
    except Exception:
        return


def import_document_bytes(
    filename: str, data: bytes, mime: str = "", progress: ProgressCallback | None = None
) -> ImportedDocument:
    with tempfile.TemporaryDirectory(prefix="fusion_import_bytes_") as tmp:
        safe_name = safe_filename(filename)
        # The original name is metadata only. Keeping it out of the temporary
        # path makes the filesystem boundary independent from caller input.
        path = Path(tmp) / "document.upload"
        path.write_bytes(data)
        return import_document_path(safe_name, path, mime=mime, progress=progress)


def import_document_path(
    filename: str, path: Path | str, mime: str = "", progress: ProgressCallback | None = None
) -> ImportedDocument:
    safe_name = safe_filename(filename)
    source = Path(path)
    suffix = Path(safe_name).suffix.lower()
    if not suffix and mime:
        suffix = suffix_from_mime(mime)
        safe_name = f"{safe_name}{suffix}" if suffix else safe_name

    data: bytes | None = None

    def read_data() -> bytes:
        nonlocal data
        if data is None:
            report_progress(progress, "reading", 0, 0, "Leyendo archivo...")
            data = source.read_bytes()
        return data

    if suffix in TEXT_SUFFIXES or (not suffix and looks_like_text(read_data())):
        report_progress(progress, "converting", 0, 0, "Leyendo texto directo...")
        text = decode_text(read_data())
        report_progress(progress, "converted", 1, 1, "Texto directo listo.")
        return imported(safe_name, text, "text", "texto directo")

    if suffix in HTML_SUFFIXES:
        report_progress(progress, "converting", 0, 0, "Convirtiendo HTML a texto...")
        return imported(safe_name, html_to_text(decode_text(read_data())), "html", "html convertido a texto")

    if suffix in RTF_SUFFIXES:
        report_progress(progress, "converting", 0, 0, "Convirtiendo RTF a texto...")
        return imported(safe_name, rtf_to_text(decode_text(read_data())), "rtf", "rtf convertido a texto")

    if suffix in DOCX_SUFFIXES:
        report_progress(progress, "converting", 0, 0, "Extrayendo DOCX...")
        return imported(safe_name, docx_to_text(read_data()), "docx", "docx extraido")

    if suffix in ODT_SUFFIXES:
        report_progress(progress, "converting", 0, 0, "Extrayendo ODT...")
        return imported(safe_name, odt_to_text(read_data()), "odt", "odt extraido")

    if suffix in PDF_SUFFIXES:
        text, detail, raw_text = pdf_to_text(safe_name, source, progress=progress)
        return imported(safe_name, text, "pdf", detail, raw_text=raw_text)

    if suffix in OFFICE_SUFFIXES:
        report_progress(progress, "converting", 0, 0, "Convirtiendo documento de oficina con LibreOffice...")
        return imported(safe_name, office_to_text(safe_name, read_data()), "office", "convertido con LibreOffice")

    if looks_like_text(read_data()):
        report_progress(progress, "converting", 0, 0, "Texto detectado...")
        return imported(safe_name, decode_text(read_data()), "text", "texto detectado")

    try:
        report_progress(progress, "converting", 0, 0, "Probando conversión con LibreOffice...")
        return imported(safe_name, office_to_text(safe_name, read_data()), "office", "convertido con LibreOffice")
    except Exception as exc:
        raise ValueError(f"unsupported_document_type:{suffix or 'sin_extension'}:{exc}") from exc


def imported(filename: str, text: str, source_type: str, detail: str, raw_text: str = "") -> ImportedDocument:
    clean = normalize_text(text)
    if not clean:
        raise ValueError(f"empty_extracted_text:{source_type}")
    return ImportedDocument(
        doc_id=doc_id_for_filename(filename),
        title=filename,
        text=clean,
        source_type=source_type,
        detail=detail,
        raw_text=normalize_raw_text(raw_text),
    )


def safe_filename(filename: str) -> str:
    raw = str(filename or "documento").replace("\\", "/")
    name = os.path.basename(raw).strip() or "documento"
    return re.sub(r"[\x00-\x1f]+", "_", name)


def doc_id_for_filename(filename: str) -> str:
    stem = Path(filename).stem or "documento"
    return re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._") or "documento"


def suffix_from_mime(mime: str) -> str:
    table = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "text/html": ".html",
        "application/rtf": ".rtf",
        "application/vnd.oasis.opendocument.text": ".odt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
    }
    return table.get(str(mime).split(";")[0].strip().lower(), "")


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def looks_like_text(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    decoded = sample.decode("utf-8", errors="ignore")
    if not decoded.strip():
        return False
    printable = sum(1 for ch in decoded if ch.isprintable() or ch in "\n\r\t")
    return printable / max(1, len(decoded)) > 0.85


def normalize_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def normalize_raw_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def html_to_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


def rtf_to_text(text: str) -> str:
    text = re.sub(r"\\par[d]?", "\n", text)
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\d* ?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    return text


def docx_to_text(data: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix="fusion_docx_") as tmp:
        path = Path(tmp) / "document.docx"
        path.write_bytes(data)
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    lines: list[str] = []
    for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        parts = [
            node.text or ""
            for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
        ]
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n\n".join(lines)


def odt_to_text(data: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix="fusion_odt_") as tmp:
        path = Path(tmp) / "document.odt"
        path.write_bytes(data)
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("content.xml")
    root = ElementTree.fromstring(xml)
    lines: list[str] = []
    text_ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    for node in root.iter():
        if node.tag in {f"{text_ns}p", f"{text_ns}h"}:
            line = "".join(node.itertext()).strip()
            if line:
                lines.append(line)
    return "\n\n".join(lines)


def pdf_to_text(filename: str, path: Path, progress: ProgressCallback | None = None) -> tuple[str, str, str]:
    tool = shutil.which("pdftotext")
    if not tool:
        raise ValueError("pdftotext_not_found")
    report_progress(progress, "pdf_text", 0, 0, "Buscando texto interno del PDF...")
    with tempfile.TemporaryDirectory(prefix="fusion_pdf_text_") as tmp:
        out = Path(tmp) / "document.txt"
        result = subprocess.run(
            [tool, "-layout", "-enc", "UTF-8", str(path), str(out)], capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "pdftotext_failed").strip()
            raise ValueError(detail)
        extracted = out.read_text(encoding="utf-8", errors="replace")
    raw_marked = mark_pdf_pages(extracted, clean=False)
    marked = clean_pdf_text(raw_marked)
    if meaningful_chars(marked) >= 40:
        report_progress(progress, "converted", 1, 1, "PDF con texto interno listo.")
        return marked, "pdf convertido con pdftotext y marcas de pagina", raw_marked
    report_progress(progress, "ocr_start", 0, 0, "PDF escaneado detectado. Iniciando OCR...")
    ocr_text = ocr_pdf_to_text(path, progress=progress)
    report_progress(progress, "converted", 1, 1, "OCR terminado.")
    return ocr_text, "pdf escaneado: OCR con Tesseract y marcas de pagina", ocr_text


def mark_pdf_pages(text: str, *, clean: bool = True) -> str:
    pages = str(text or "").split("\f")
    marked: list[str] = []
    for idx, page in enumerate(pages, start=1):
        page_text = normalize_text(page) if clean else normalize_raw_text(page)
        if page_text:
            marked.append(f"[Pagina {idx}]\n{page_text}")
    return "\n\n".join(marked)


PDF_WORD_RE = r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+"
PDF_PAGE_MARKER_RE = re.compile(r"^\[Pagina\s+\d+\]$", re.IGNORECASE)
PDF_JOINABLE_RE = re.compile(PDF_WORD_RE)


@functools.lru_cache(maxsize=1)
def _spanish_wordlist() -> set[str]:
    candidates = (
        Path("/usr/share/dict/spanish"),
        Path("/usr/share/hunspell/es_AR.dic"),
        Path("/usr/share/hunspell/es_ES.dic"),
    )
    words: set[str] = set()
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for idx, raw_line in enumerate(handle):
                    line = raw_line.strip()
                    if not line:
                        continue
                    if idx == 0 and path.suffix == ".dic" and line.isdigit():
                        continue
                    word = line.split("/", 1)[0].strip().lower()
                    if len(word) < 4 or not re.fullmatch(PDF_WORD_RE, word):
                        continue
                    words.add(word)
        except Exception:
            continue
        if words:
            break
    return words


def _looks_like_sparse_upper_header(line: str) -> bool:
    compact = re.sub(r"[^A-ZÁÉÍÓÚÜÑ0-9]", "", line)
    if len(compact) < 6:
        return False
    tokens = re.findall(r"[A-ZÁÉÍÓÚÜÑ]+|[0-9IVXLCDM]+", line)
    if len(tokens) < 4:
        return False
    short_tokens = sum(1 for token in tokens if len(token) <= 3)
    return short_tokens >= max(3, len(tokens) - 1)


def _is_mechanical_pdf_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if PDF_PAGE_MARKER_RE.fullmatch(stripped):
        return False
    if re.fullmatch(r"[0-9]{1,4}(?:[.·•-]{1,3})?", stripped):
        return True
    if _looks_like_sparse_upper_header(stripped):
        return True
    return False


def _normalize_pdf_line(line: str) -> str:
    line = str(line or "").replace("\t", " ")
    line = re.sub(r"[ ]{2,}", " ", line)
    return line.strip()


def _should_join_pdf_lines(previous: str, current: str) -> bool:
    prev = previous.rstrip()
    curr = current.lstrip()
    if not prev or not curr:
        return False
    if PDF_PAGE_MARKER_RE.fullmatch(prev) or PDF_PAGE_MARKER_RE.fullmatch(curr):
        return False
    if prev.endswith("-"):
        return True
    if prev.endswith((".", "!", "?")):
        return False
    return curr[:1].islower() or curr.startswith((",", ";", ":", ")", "]", "»", "—", "-", "y ", "o "))


def _join_fragments_if_known(parts: tuple[str, ...]) -> str | None:
    candidate = "".join(parts)
    dictionary = _spanish_wordlist()
    if candidate.lower() not in dictionary:
        return None
    return candidate


def _repair_split_words(text: str) -> str:
    triple_pattern = re.compile(rf"\b({PDF_WORD_RE}) ({PDF_WORD_RE}) ({PDF_WORD_RE})\b")
    pair_pattern = re.compile(rf"\b({PDF_WORD_RE}) ({PDF_WORD_RE})\b")
    patterns = (triple_pattern, pair_pattern)
    suspicious_pair_count = len(pair_pattern.findall(str(text or "")))
    allow_fallback_pairs = suspicious_pair_count >= 2

    def likely_split_pair(parts: tuple[str, ...]) -> bool:
        if len(parts) != 2:
            return False
        left, right = parts
        left_lower = left.lower()
        right_lower = right.lower()
        total = len(left_lower) + len(right_lower)
        if total < 7:
            return False
        if left_lower in OCR_STOPWORDS or right_lower in OCR_STOPWORDS:
            return False
        return (len(left_lower) <= 3 and len(right_lower) >= 4) or (len(left_lower) >= 4 and len(right_lower) <= 3)

    def replace(match: re.Match[str]) -> str:
        parts = tuple(match.groups())
        if sum(len(part) for part in parts) < 6:
            return match.group(0)
        merged = _join_fragments_if_known(parts)
        if merged:
            return merged
        if not any(len(part) <= 3 for part in parts):
            return match.group(0)
        if allow_fallback_pairs and likely_split_pair(parts):
            return "".join(parts)
        return match.group(0)

    repaired = str(text or "")
    for pattern in patterns:
        previous = None
        while repaired != previous:
            previous = repaired
            repaired = pattern.sub(replace, repaired)
    return repaired


def clean_pdf_text(text: str) -> str:
    body = normalize_raw_text(text)
    if not body:
        return ""
    body = re.sub(r"(?<=\w)-\n(?=\w)", "", body)
    lines = body.split("\n")
    paragraphs: list[str] = []
    current = ""
    for raw_line in lines:
        stripped = _normalize_pdf_line(raw_line)
        if not stripped:
            if current:
                paragraphs.append(current.strip())
                current = ""
            continue
        if _is_mechanical_pdf_line(stripped):
            continue
        if PDF_PAGE_MARKER_RE.fullmatch(stripped):
            if current:
                paragraphs.append(current.strip())
                current = ""
            paragraphs.append(stripped)
            continue
        if current and _should_join_pdf_lines(current, stripped):
            current = f"{current.rstrip('-').rstrip()} {stripped}".strip()
        else:
            if current:
                paragraphs.append(current.strip())
            current = stripped
    if current:
        paragraphs.append(current.strip())
    cleaned = "\n\n".join(
        _repair_split_words(paragraph) if not PDF_PAGE_MARKER_RE.fullmatch(paragraph) else paragraph
        for paragraph in paragraphs
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return normalize_text(cleaned)


def meaningful_chars(text: str) -> int:
    return sum(1 for ch in str(text or "") if ch.isalnum())


def pdf_page_count(path: Path) -> int:
    tool = shutil.which("pdfinfo")
    if not tool:
        return 0
    result = subprocess.run([tool, str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return 0
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, flags=re.MULTILINE)
    return int(match.group(1)) if match else 0


def ocr_pdf_to_text(path: Path, progress: ProgressCallback | None = None) -> str:
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not pdftoppm or not tesseract:
        raise ValueError("ocr_tools_not_found")
    pages = pdf_page_count(path)
    if pages <= 0:
        raise ValueError("pdf_page_count_not_found")
    output: list[str] = []
    completed = 0
    report_progress(progress, "ocr", completed, pages, f"OCR preparado: {pages} páginas.")
    worker_count = min(OCR_WORKERS, pages)
    if worker_count <= 1:
        page_items = []
        for page in range(1, pages + 1):
            item = ocr_pdf_page_to_text(path, page, pdftoppm, tesseract)
            page_items.append(item)
            completed += 1
            report_progress(progress, "ocr", completed, pages, f"OCR página {completed} de {pages}.")
    else:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="fusion-reader-ocr") as executor:
            futures = [
                executor.submit(ocr_pdf_page_to_text, path, page, pdftoppm, tesseract) for page in range(1, pages + 1)
            ]
            page_items = []
            for future in as_completed(futures):
                page_items.append(future.result())
                completed += 1
                report_progress(progress, "ocr", completed, pages, f"OCR página {completed} de {pages}.")
            page_items.sort(key=lambda item: item[0])
    for page, page_text in page_items:
        if page_text:
            output.append(f"[Pagina {page}]\n{page_text}")
    return "\n\n".join(output)


def ocr_pdf_page_to_text(path: Path, page: int, pdftoppm: str, tesseract: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix=f"fusion_pdf_ocr_{page}_") as tmp:
        root = Path(tmp)
        prefix = root / "page"
        render = subprocess.run(
            [pdftoppm, "-f", str(page), "-l", str(page), "-r", str(OCR_DPI), "-png", str(path), str(prefix)],
            capture_output=True,
            text=True,
            timeout=160,
        )
        if render.returncode != 0:
            raise ValueError((render.stderr or render.stdout or "pdftoppm_failed").strip())
        images = sorted(root.glob("page-*.png"))
        if not images:
            return page, ""
        return page, structured_ocr_image(images[-1], tesseract)


def structured_ocr_image(image_path: Path, tesseract: str) -> str:
    image = Image.open(image_path)
    width, height = image.size
    top_cut = max(420, int(height * 0.26))
    gutter = max(10, int(width * 0.015))

    with tempfile.TemporaryDirectory(prefix="fusion_ocr_crops_") as tmp:
        root = Path(tmp)
        top = root / "top.png"
        left = root / "left.png"
        right = root / "right.png"
        save_ocr_crop(image, (0, 0, width, min(height, top_cut)), top, upscale=1.2)
        save_ocr_crop(image, (0, top_cut, max(1, width // 2 - gutter), height), left, upscale=1.35)
        save_ocr_crop(image, (min(width - 1, width // 2 + gutter), top_cut, width, height), right, upscale=1.35)

        heading_text = structured_plain_ocr_text(run_tesseract_plain(tesseract, top), headings_only=True)
        left_text = structured_plain_ocr_text(run_tesseract_plain(tesseract, left))
        right_text = structured_plain_ocr_text(run_tesseract_plain(tesseract, right))

    pieces = [piece for piece in (heading_text, left_text, right_text) if piece.strip()]
    text = "\n\n".join(pieces)
    if not enough_plain_page_signal(text):
        return ""
    return text


def save_ocr_crop(image: Image.Image, box: tuple[int, int, int, int], target: Path, upscale: float = 1.0) -> None:
    crop = image.crop(box).convert("L")
    crop = ImageOps.autocontrast(crop)
    crop = crop.filter(ImageFilter.SHARPEN)
    if upscale > 1.0:
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        crop = crop.resize((int(crop.width * upscale), int(crop.height * upscale)), resample=resample)
    crop.save(target)


def run_tesseract_plain(tesseract: str, image_path: Path) -> str:
    result = subprocess.run(
        [tesseract, str(image_path), "stdout", "-l", "spa+eng", "--oem", "1", "--psm", "6"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise ValueError((result.stderr or result.stdout or "tesseract_failed").strip())
    return result.stdout


def structured_plain_ocr_text(text: str, headings_only: bool = False) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    last_was_chapter = False
    for raw in str(text or "").splitlines():
        line = clean_ocr_line(raw)
        if not line:
            flush_paragraph(paragraphs, current)
            last_was_chapter = False
            continue
        heading = heading_level(line, previous_was_chapter=last_was_chapter)
        if headings_only and not heading:
            continue
        if not heading and not keep_ocr_line(line, 70.0):
            continue
        if not heading and looks_like_numeric_artifact(line):
            continue
        if heading:
            flush_paragraph(paragraphs, current)
            clean = clean_heading(line)
            if clean and clean not in {p.lstrip("# ").strip() for p in paragraphs[-2:]}:
                paragraphs.append(f"{heading} {clean}")
            last_was_chapter = heading == "#"
            continue
        current.append(line)
        last_was_chapter = False
    flush_paragraph(paragraphs, current)
    return postprocess_ocr_text("\n\n".join(p for p in paragraphs if p.strip()))


def enough_plain_page_signal(text: str) -> bool:
    if meaningful_chars(text) < 120:
        return False
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if len(lines) < 3:
        return False
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", text)
    if len(words) < 18:
        return False
    if looks_like_noisy_index_page(text):
        return False
    if re.search(r"(?i)\b(cap[ií]tulo|[ií]ndice|introducci[oó]n|ap[eé]ndice)\b", text):
        return True
    if stopword_ratio(text) < 0.18:
        return False
    vowel_words = [word for word in words if re.search(r"[aeiouáéíóúüAEIOUÁÉÍÓÚÜ]", word)]
    return len(vowel_words) / max(1, len(words)) >= 0.72


def looks_like_numeric_artifact(line: str) -> bool:
    clean = str(line or "").strip()
    if not clean:
        return False
    if re.fullmatch(r"[\d\s.,;:|/\\\-]+", clean):
        return True
    return bool(re.fullmatch(r"(?:\d+\s*){3,}", clean))


def looks_like_noisy_index_page(text: str) -> bool:
    clean = str(text or "")
    chapter_refs = len(re.findall(r"(?i)\bcap[ií]tulo\b", clean))
    has_index = bool(re.search(r"(?i)\b[ií]ndice\b", clean))
    if not has_index or chapter_refs < 5:
        return False
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    short_lines = sum(1 for line in lines if len(line) <= 45)
    return stopword_ratio(clean) < 0.16 and short_lines / max(1, len(lines)) > 0.65


def postprocess_ocr_text(text: str) -> str:
    out = repair_ocr_spacing(text)
    out = re.sub(r"\bme fue entrega\s*\n+\s*va por\b", "me fue entregado por", out, flags=re.IGNORECASE)
    out = re.sub(r"\bme fue entrega\s*\n+\s*a por\b", "me fue entregado por", out, flags=re.IGNORECASE)
    out = re.sub(r"\bpo\s*\n+\s*bre lat[ií]n\b", "pobre latín", out, flags=re.IGNORECASE)
    out = re.sub(r"\bOrden\.\s+visitado este monasterio\b", "Orden. Ha visitado este monasterio", out)
    out = re.sub(r"\binter[eé]s\s+m[ií]\b", "interés en mí", out, flags=re.IGNORECASE)
    out = re.sub(r"\bestudio de Sagrada Biblia\b", "estudio de la Sagrada Biblia", out, flags=re.IGNORECASE)
    out = re.sub(r"(?m)^a Fiesta\b", "La Fiesta", out)
    out = re.sub(r"\bla Sas\s*\n+\s*A Virgen María\b", "la Santa Virgen María", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def stopword_ratio(text: str) -> float:
    words = [word.lower().strip('.,;:!?¡¿()[]"“”‘’') for word in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}", text)]
    if not words:
        return 0.0
    return sum(word in OCR_STOPWORDS for word in words) / len(words)


def structured_ocr_page(tsv: str) -> str:
    lines = ocr_lines_from_tsv(tsv)
    lines = [line for line in lines if keep_ocr_line(line["text"], line["conf"])]
    if not enough_page_signal(lines):
        return ""
    return format_ocr_lines(lines)


def ocr_lines_from_tsv(tsv: str) -> list[dict]:
    reader = csv.DictReader(tsv.splitlines(), delimiter="\t")
    groups: dict[tuple[int, int, int, int], dict] = {}
    for row in reader:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            conf = -1
        if conf < OCR_WORD_MIN_CONF:
            continue
        if not keep_ocr_word(text):
            continue
        try:
            key = (
                int(row.get("block_num") or 0),
                int(row.get("par_num") or 0),
                int(row.get("line_num") or 0),
                int(row.get("top") or 0),
            )
            left = int(row.get("left") or 0)
        except ValueError:
            continue
        item = groups.setdefault(
            key, {"block": key[0], "par": key[1], "line": key[2], "top": key[3], "words": [], "confs": []}
        )
        item["words"].append((left, text))
        item["confs"].append(conf)

    out: list[dict] = []
    for item in sorted(groups.values(), key=lambda x: (x["block"], x["par"], x["line"], x["top"])):
        words = [word for _, word in sorted(item["words"], key=lambda x: x[0])]
        text = clean_ocr_line(" ".join(words))
        if not text:
            continue
        confs = item["confs"]
        out.append(
            {
                "block": item["block"],
                "par": item["par"],
                "line": item["line"],
                "text": text,
                "conf": sum(confs) / max(1, len(confs)),
            }
        )
    return out


def keep_ocr_word(word: str) -> bool:
    clean = word.strip()
    if not clean:
        return False
    if re.fullmatch(r"[|_~`.,;:!¡¿?\"'“”‘’(){}\[\]<>/\\=-]+", clean):
        return False
    if len(clean) == 1 and not clean.isalnum():
        return False
    return any(ch.isalnum() for ch in clean)


def clean_ocr_line(line: str) -> str:
    line = html.unescape(line)
    line = line.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("¢", "")
    line = re.sub(r"\s+", " ", line).strip()
    line = re.sub(r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ])- ([a-záéíóúüñ])", r"\1\2", line)
    line = re.sub(r"\s+([,.;:!?])", r"\1", line)
    line = re.sub(r"^[|/\\_~`.,;: -]+", "", line)
    line = re.sub(r"[|/\\_~` -]+$", "", line)
    return repair_ocr_spacing(line.strip())


def repair_ocr_spacing(text: str) -> str:
    out = str(text or "")
    for bad, good in OCR_SPACING_FIXES.items():
        out = out.replace(bad, good)
    for pattern, replacement in OCR_REGEX_SPACING_FIXES:
        out = pattern.sub(replacement, out)
    out = re.sub(r"\b([Ll])\s+a\b", lambda m: "La" if m.group(1) == "L" else "la", out)
    out = re.sub(r"\b([Ee])\s+l\b", lambda m: "El" if m.group(1) == "E" else "el", out)
    out = re.sub(r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ])- ([a-záéíóúüñ])", r"\1\2", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def keep_ocr_line(line: str, conf: float) -> bool:
    if conf < OCR_MIN_CONF:
        return False
    letters = sum(1 for ch in line if ch.isalpha())
    alnum = sum(1 for ch in line if ch.isalnum())
    if alnum < 3:
        return False
    if letters / max(1, len(line)) < 0.35 and not re.search(r"\d", line):
        return False
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}", line)
    if len(line) > 25 and len(tokens) < 2:
        return False
    weird = sum(1 for ch in line if not (ch.isalnum() or ch.isspace() or ch in ".,;:!?¡¿()[]'\"“”‘’-/áéíóúÁÉÍÓÚüÜñÑ"))
    if weird / max(1, len(line)) > 0.22:
        return False
    return True


def enough_page_signal(lines: list[dict]) -> bool:
    text = " ".join(line["text"] for line in lines)
    if meaningful_chars(text) < 120:
        return False
    longish = [line for line in lines if len(line["text"]) >= 18]
    if len(longish) < 3:
        return False
    avg_conf = sum(line["conf"] for line in lines) / max(1, len(lines))
    return avg_conf >= OCR_MIN_CONF


def format_ocr_lines(lines: list[dict]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    previous_key: tuple[int, int] | None = None
    last_was_chapter = False

    for line in lines:
        text = line["text"]
        key = (int(line["block"]), int(line["par"]))
        heading = heading_level(text, previous_was_chapter=last_was_chapter)
        if heading:
            flush_paragraph(paragraphs, current)
            paragraphs.append(f"{heading} {clean_heading(text)}")
            previous_key = key
            last_was_chapter = heading == "#"
            continue
        if previous_key is not None and key != previous_key:
            flush_paragraph(paragraphs, current)
        current.append(text)
        previous_key = key
        last_was_chapter = False

    flush_paragraph(paragraphs, current)
    return "\n\n".join(p for p in paragraphs if p.strip())


def flush_paragraph(paragraphs: list[str], current: list[str]) -> None:
    if not current:
        return
    text = " ".join(current)
    text = re.sub(r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ])- ([a-záéíóúüñ])", r"\1\2", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text:
        paragraphs.append(repair_ocr_spacing(text))
    current.clear()


def heading_level(line: str, previous_was_chapter: bool = False) -> str:
    clean = clean_heading(line)
    if re.search(r"(?i)\bcap[ií]tulo\b", clean):
        return "#"
    if re.search(r"(?i)\b(ap[eé]ndice|introducci[oó]n|[ií]ndice)\b", clean) and len(clean) <= 80:
        return "##"
    if previous_was_chapter and 3 <= len(clean) <= 80 and len(clean.split()) <= 8:
        return "##"
    return ""


def clean_heading(line: str) -> str:
    line = clean_ocr_line(line)
    line = re.sub(r"^[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", "", line)
    line = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+$", "", line)
    if re.search(r"(?i)\bcap[ií]tulo\b", line):
        match = re.search(r"(?i)\b(cap[ií]tulo\s+(?:[IVXLCDM]+|\d+))\b", line)
        if match:
            return normalize_heading_case(match.group(1).strip())
        return line.strip(" .:-")
    line = re.sub(r"\.{2,}\s*\d+\s*$", "", line)
    line = re.sub(r"\s+\d+\s*$", "", line)
    return normalize_heading_case(line.strip(" .:-"))


def normalize_heading_case(line: str) -> str:
    if not line:
        return line
    words = line.split()
    if not words:
        return line
    small = {"de", "del", "la", "las", "el", "los", "y", "o", "en", "a"}
    fixed = []
    for idx, word in enumerate(words):
        lower = word.lower()
        if idx > 0 and lower in small:
            fixed.append(lower)
        elif word.isupper() and len(word) > 2:
            fixed.append(word.capitalize())
        else:
            fixed.append(word[:1].upper() + word[1:])
    return " ".join(fixed)


def office_to_text(filename: str, data: bytes) -> str:
    tool = shutil.which("libreoffice") or shutil.which("soffice")
    if not tool:
        raise ValueError("libreoffice_not_found")
    with tempfile.TemporaryDirectory(prefix="fusion_office_") as tmp:
        root = Path(tmp)
        requested_suffix = Path(safe_filename(filename)).suffix.lower()
        suffix = OFFICE_TEMP_SUFFIXES.get(requested_suffix, ".doc")
        src = root / f"document{suffix}"
        src.write_bytes(data)
        result = subprocess.run(
            [tool, "--headless", "--convert-to", "txt:Text", "--outdir", str(root), str(src)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "libreoffice_convert_failed").strip()
            raise ValueError(detail)
        candidates = [p for p in root.glob("*.txt") if p.name != src.name]
        if not candidates:
            candidates = list(root.glob(f"{src.stem}*.txt"))
        if not candidates:
            raise ValueError("libreoffice_output_not_found")
        return candidates[0].read_text(encoding="utf-8", errors="replace")
