from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    name: str
    prefix: bool = False

    def matches(self, method: str, path: str) -> bool:
        return self.method == method.upper() and (path.startswith(self.pattern) if self.prefix else path == self.pattern)


class Router:
    def __init__(self, routes: tuple[Route, ...]) -> None:
        self.routes = routes

    def resolve(self, method: str, path: str) -> Route | None:
        return next((route for route in self.routes if route.matches(method, path)), None)


def create_router() -> Router:
    get_exact = (
        "/",
        "/health",
        "/health/live",
        "/health/ready",
        "/api/status",
        "/api/build",
        "/api/library",
        "/api/voice/voices",
        "/api/voices",
        "/api/voice/metrics",
        "/api/voice/metrics/summary",
        "/api/voice/metrics/documents",
        "/api/voice/metrics/chunks",
        "/api/prepare/status",
        "/api/audio-export/status",
        "/api/references",
        "/api/notes",
        "/api/dialogue/status",
        "/api/import-status",
    )
    get_prefixes = (
        "/static/",
        "/audio/",
        "/api/tools/pdf-to-docx/status/",
        "/api/tools/pdf-to-docx/download/",
        "/api/audio-export/status/",
        "/api/audio-export/download/",
    )
    post_exact = (
        "/api/tools/pdf-to-docx",
        "/api/import-file/start",
        "/api/import-file",
        "/api/load",
        "/api/import",
        "/api/reference/promote",
        "/api/reference/remove",
        "/api/document/clear",
        "/api/read",
        "/api/next",
        "/api/previous",
        "/api/jump",
        "/api/prepare/start",
        "/api/prepare/cancel",
        "/api/audio-export",
        "/api/notes/create",
        "/api/notes/update",
        "/api/notes/rename",
        "/api/notes/delete",
        "/api/dialogue/reset",
        "/api/reasoning/mode",
        "/api/laboratory/mode",
        "/api/profile",
        "/api/veil",
        "/api/voice",
        "/api/laboratory/reset",
        "/api/chat/reset",
        "/api/dialogue/turn",
        "/api/voice/test",
        "/api/chat",
    )
    post_prefixes = ("/api/tools/pdf-to-docx/cancel/", "/api/audio-export/cancel/")
    routes = [Route("GET", path, path) for path in get_exact]
    routes.extend(Route("GET", path, path, prefix=True) for path in get_prefixes)
    routes.extend(Route("HEAD", path, path) for path in ("/", "/health", "/api/status"))
    routes.extend(Route("HEAD", path, path, prefix=True) for path in ("/static/", "/audio/"))
    routes.extend(Route("POST", path, path) for path in post_exact)
    routes.extend(Route("POST", path, path, prefix=True) for path in post_prefixes)
    return Router(tuple(routes))
