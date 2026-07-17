from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from fusion_reader_v2.output_validation import OutputValidationError, stream_file, validate_output_file
from fusion_reader_v2.web.context import WebContext


def audio_url_for(context: WebContext, path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value).resolve()
    cache_root = context.app.cache.root.resolve()
    if path.parent != cache_root or not path.exists():
        return ""
    return f"/audio/{path.name}"


def cached_audio_path(context: WebContext, url_path: str) -> Path | None:
    filename = Path(unquote(url_path.removeprefix("/audio/"))).name
    audio_path = (context.app.cache.root / filename).resolve()
    cache_root = context.app.cache.root.resolve()
    if audio_path.parent != cache_root or not audio_path.exists():
        return None
    return audio_path


def unique_download_target(context: WebContext, filename: str) -> Path:
    downloads_dir = context.settings.paths.downloads
    downloads_dir.mkdir(parents=True, exist_ok=True)
    candidate = downloads_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix or ".docx"
    for index in range(2, 1000):
        alt = downloads_dir / f"{stem}_{index}{suffix}"
        if not alt.exists():
            return alt
    raise RuntimeError("no_safe_output_slot")


__all__ = [
    "OutputValidationError",
    "audio_url_for",
    "cached_audio_path",
    "stream_file",
    "unique_download_target",
    "validate_output_file",
]
