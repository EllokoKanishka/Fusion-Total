from __future__ import annotations

import os
import re
import stat
from urllib.parse import quote
from pathlib import Path


class OutputValidationError(ValueError):
    pass


def validate_output_file(path: Path | str, root: Path | str, *, suffix: str) -> Path:
    requested = Path(path)
    allowed = Path(root).expanduser().resolve(strict=False)
    try:
        if requested.is_symlink():
            raise OutputValidationError("output_symlink_rejected")
        resolved = requested.expanduser().resolve(strict=True)
        if resolved != allowed and allowed not in resolved.parents:
            raise OutputValidationError("output_outside_root")
        if resolved.suffix.lower() != suffix.lower():
            raise OutputValidationError("output_type_invalid")
        mode = resolved.stat().st_mode
        if not stat.S_ISREG(mode):
            raise OutputValidationError("output_not_regular")
        return resolved
    except FileNotFoundError as exc:
        raise OutputValidationError("output_missing") from exc
    except OSError as exc:
        raise OutputValidationError("output_invalid") from exc


def stream_file(
    handler,
    path: Path,
    *,
    content_type: str,
    filename: str | None,
    chunk_size: int = 64 * 1024,
    range_header: str | None = None,
    head_only: bool = False,
) -> bool:
    size = os.stat(path).st_size
    start, end = 0, size - 1
    partial = False
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", range_header or "")
    if match and any(match.groups()) and not head_only:
        first, last = match.groups()
        try:
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                suffix = int(last)
                if suffix <= 0:
                    raise ValueError("empty_suffix")
                start = max(0, size - suffix)
            if start >= size or end < start:
                raise ValueError("unsatisfiable_range")
        except ValueError:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return False
        partial = True
    length = max(0, end - start + 1)
    handler.send_response(206 if partial else 200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(length))
    handler.send_header("X-Content-Type-Options", "nosniff")
    if getattr(handler, "request_id", None):
        handler.send_header("X-Request-ID", handler.request_id)
    if range_header is not None:
        handler.send_header("Accept-Ranges", "bytes")
    if partial:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    if filename is not None:
        safe_name = Path(filename).name.replace('"', "_").replace("\r", "_").replace("\n", "_")
        ascii_name = safe_name.encode("ascii", errors="replace").decode("ascii")
        encoded = quote(safe_name, safe="")
        handler.send_header("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}")
    handler.end_headers()
    if head_only:
        return True
    try:
        with path.open("rb") as source:
            source.seek(start)
            remaining = length
            while remaining > 0:
                chunk = source.read(min(chunk_size, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
        return True
    except (BrokenPipeError, ConnectionResetError, FileNotFoundError, OSError):
        return False


__all__ = ["OutputValidationError", "stream_file", "validate_output_file"]
