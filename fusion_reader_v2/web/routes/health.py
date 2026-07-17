from __future__ import annotations

import os
from typing import Protocol

from fusion_reader_v2.web.context import WebContext


class HealthResponder(Protocol):
    @property
    def context(self) -> WebContext: ...

    def _json(self, status: int, payload: dict) -> None: ...


def handle_health_get(responder: HealthResponder, path: str) -> bool:
    if path == "/health/live":
        responder._json(200, {"ok": True, "status": "live", "pid": os.getpid()})
        return True
    if path == "/health/ready":
        status = responder.context.status()
        services = status.get("services", {})
        degradations = [
            name
            for name in ("tts", "stt", "chat", "external_research")
            if not bool((services.get(name) or {}).get("ready", (services.get(name) or {}).get("ok")))
        ]
        responder._json(
            200,
            {
                "ok": True,
                "status": "ready",
                "reader_ready": True,
                "services": services,
                "degradations": degradations,
            },
        )
        return True
    if path in ("/health", "/api/status"):
        responder._json(200, responder.context.status())
        return True
    if path == "/api/build":
        responder._json(200, {"ok": True, **responder.context.runtime_info})
        return True
    return False


__all__ = ["handle_health_get"]
