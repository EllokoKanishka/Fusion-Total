from __future__ import annotations

import re
from dataclasses import dataclass


_STABLE_CODE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")


@dataclass(frozen=True)
class APIError(Exception):
    code: str
    detail: str
    status: int = 400


def error_response(exc: Exception, request_id: str) -> tuple[int, dict]:
    if isinstance(exc, APIError):
        status = exc.status
        code = exc.code
        detail = exc.detail
    elif isinstance(exc, FileNotFoundError):
        status = 404
        code = _safe_code(str(exc), "not_found")
        detail = "El recurso solicitado no existe."
    elif isinstance(exc, KeyError):
        status = 404
        code = _safe_code(str(exc).strip("'"), "not_found")
        detail = "El recurso solicitado no existe."
    elif isinstance(exc, ValueError):
        code = _safe_code(str(exc), "invalid_request")
        status = 413 if code in {"request_body_too_large", "upload_too_large", "pdf_too_large", "base64_upload_too_large"} else 400
        detail = "La solicitud no cumple el contrato de la API."
    else:
        status = 500
        code = "internal_server_error"
        detail = "La operación falló de forma inesperada."
    return status, {
        "ok": False,
        "error": code,
        "detail": detail,
        "request_id": request_id,
    }


def _safe_code(value: str, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized if _STABLE_CODE.fullmatch(normalized) else fallback
