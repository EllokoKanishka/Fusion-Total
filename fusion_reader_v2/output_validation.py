from __future__ import annotations

import os
import stat
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


def stream_file(handler, path: Path, *, content_type: str, filename: str, chunk_size: int = 64 * 1024) -> bool:
    size = os.stat(path).st_size
    safe_name = Path(filename).name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(size))
    handler.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
    handler.end_headers()
    try:
        with path.open("rb") as source:
            while True:
                chunk = source.read(chunk_size)
                if not chunk:
                    break
                handler.wfile.write(chunk)
        return True
    except (BrokenPipeError, ConnectionResetError, FileNotFoundError, OSError):
        return False


__all__ = ["OutputValidationError", "stream_file", "validate_output_file"]
