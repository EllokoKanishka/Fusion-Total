from __future__ import annotations

import re
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

from .owned_subprocess import run_owned

from .pdf_to_docx import find_downloads_dir


@dataclass(frozen=True)
class AudioExportRequest:
    mode: str
    block: int | None = None
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class AudioExportSnapshot:
    doc_id: str
    title: str
    voice: str
    language: str
    total_blocks: int
    blocks: list[tuple[int, str]]


@dataclass
class AudioExportJob:
    job_id: str
    state: str = "queued"
    title: str = ""
    start_block: int = 0
    end_block: int = 0
    total_blocks: int = 0
    completed_blocks: int = 0
    cached_blocks: int = 0
    generated_blocks: int = 0
    current_block: int = 0
    output_path: str = ""
    filename: str = ""
    detail: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    download_url: str = ""
    doc_id: str = ""
    voice: str = ""
    language: str = ""
    concat_method: str = ""
    error: str = ""
    snapshot: AudioExportSnapshot | None = field(default=None, repr=False)

    @property
    def terminal(self) -> bool:
        return self.state in {"done", "cancelled", "error"}

    @property
    def updated_at(self) -> float:
        return float(self.finished_at or self.started_at)

    def to_dict(self) -> dict:
        progress = int(self.completed_blocks * 100 / self.total_blocks) if self.total_blocks else 0
        return {
            "ok": self.state in {"queued", "running", "done", "cancelled"},
            "job_id": self.job_id,
            "id": self.job_id,
            "type": "audio_export",
            "state": self.state,
            "created_at": self.started_at,
            "updated_at": self.updated_at,
            "progress": progress,
            "terminal": self.terminal,
            "cancel_requested": self.state in {"canceling", "cancelled"},
            "error_code": self.error,
            "error_detail": self.detail if self.state == "error" else "",
            "output": {"path": self.output_path, "filename": self.filename} if self.output_path else {},
            "title": self.title,
            "start_block": self.start_block,
            "end_block": self.end_block,
            "total_blocks": self.total_blocks,
            "completed_blocks": self.completed_blocks,
            "cached_blocks": self.cached_blocks,
            "generated_blocks": self.generated_blocks,
            "current_block": self.current_block,
            "output_path": self.output_path,
            "filename": self.filename,
            "detail": self.detail,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "download_url": self.download_url,
            "doc_id": self.doc_id,
            "voice": self.voice,
            "language": self.language,
            "concat_method": self.concat_method,
            "error": self.error,
        }


def sanitize_audio_title(title: str) -> str:
    stem = Path(str(title or "documento")).stem or "documento"
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "documento"
    return clean[:120]


def build_audio_export_filename(title: str, start_block: int, end_block: int, total_blocks: int) -> str:
    safe = sanitize_audio_title(title)
    if start_block == 1 and end_block == total_blocks:
        return f"{safe}_completo.wav"
    if start_block == end_block:
        return f"{safe}_bloque_{start_block:03d}.wav"
    return f"{safe}_bloques_{start_block:03d}-{end_block:03d}.wav"


def unique_audio_download_target(filename: str, root: Path | str | None = None) -> Path:
    downloads_dir = Path(root) if root is not None else find_downloads_dir()
    downloads_dir = downloads_dir.expanduser()
    downloads_dir.mkdir(parents=True, exist_ok=True)
    candidate = downloads_dir / Path(filename).name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix or ".wav"
    for index in range(2, 1000):
        alt = downloads_dir / f"{stem}_{index}{suffix}"
        if not alt.exists():
            return alt
    raise RuntimeError("no_safe_audio_export_slot")


def _concat_wav_with_wave(inputs: list[Path], output: Path) -> str:
    if not inputs:
        raise ValueError("no_input_wavs")
    with wave.open(str(inputs[0]), "rb") as first:
        params = first.getparams()
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as target:
            target.setparams(params)
            target.writeframes(first.readframes(first.getnframes()))
            for item in inputs[1:]:
                with wave.open(str(item), "rb") as source:
                    if source.getparams() != params:
                        raise ValueError("wav_params_mismatch")
                    target.writeframes(source.readframes(source.getnframes()))
    return "wave"


def _concat_wav_with_ffmpeg(inputs: list[Path], output: Path) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg_not_available")
    list_path = output.parent / f".audio_export_{int(time.time() * 1000)}.txt"
    entries = []
    for path in inputs:
        escaped = str(path).replace("'", "'\\''")
        entries.append(f"file '{escaped}'\n")
    list_path.write_text(
        "".join(entries),
        encoding="utf-8",
    )
    try:
        run_owned(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output)],
            timeout=180,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        list_path.unlink(missing_ok=True)
    return "ffmpeg"


def concat_wav_files(inputs: list[Path], output: Path) -> str:
    try:
        return _concat_wav_with_wave(inputs, output)
    except ValueError as exc:
        if str(exc) != "wav_params_mismatch":
            raise
    try:
        return _concat_wav_with_ffmpeg(inputs, output)
    except RuntimeError as exc:
        if str(exc) == "ffmpeg_not_available":
            raise RuntimeError("wav_params_incompatible")
        raise
