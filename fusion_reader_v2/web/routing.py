from __future__ import annotations

from dataclasses import dataclass

from fusion_reader_v2.web.routes.audio import handle_audio_get, handle_audio_post
from fusion_reader_v2.web.routes.dialogue import handle_dialogue_get, handle_dialogue_post
from fusion_reader_v2.web.routes.health import handle_health_get
from fusion_reader_v2.web.routes.notes import handle_notes_get, handle_notes_post
from fusion_reader_v2.web.routes.preparation import handle_preparation_get, handle_preparation_post
from fusion_reader_v2.web.routes.reading import handle_reading_post
from fusion_reader_v2.web.routes.tools import handle_tools_get, handle_tools_post
from fusion_reader_v2.web.routes.media import handle_media_get, handle_media_post


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    name: str
    prefix: bool = False

    def matches(self, method: str, path: str) -> bool:
        return self.method == method.upper() and (
            path.startswith(self.pattern) if self.prefix else path == self.pattern
        )


class Router:
    def __init__(self, routes: tuple[Route, ...]) -> None:
        self.routes = routes

    def resolve(self, method: str, path: str) -> Route | None:
        return next((route for route in self.routes if route.matches(method, path)), None)

    def dispatch_get(self, responder, path: str, raw_path: str) -> bool:
        handlers = (
            lambda: handle_health_get(responder, path),
            lambda: handle_audio_get(responder, path, raw_path),
            lambda: handle_preparation_get(responder, path),
            lambda: handle_notes_get(responder, path, raw_path),
            lambda: handle_dialogue_get(responder, path),
            lambda: handle_tools_get(responder, path, raw_path),
            lambda: handle_media_get(responder, path),
        )
        return any(handler() for handler in handlers)

    def dispatch_raw_post(self, responder, path: str) -> bool:
        return handle_tools_post(responder, path) or handle_media_post(responder, path)

    def dispatch_post(self, responder, path: str, payload: dict) -> bool:
        handlers = (
            lambda: handle_audio_post(responder, path, payload),
            lambda: handle_dialogue_post(responder, path, payload),
            lambda: handle_preparation_post(responder, path, payload),
            lambda: handle_reading_post(responder, path, payload),
            lambda: handle_notes_post(responder, path, payload),
            lambda: handle_media_post(responder, path, payload),
        )
        return any(handler() for handler in handlers)


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
        "/api/media/status",
    )
    get_prefixes = (
        "/static/",
        "/audio/",
        "/api/tools/pdf-to-docx/status/",
        "/api/tools/pdf-to-docx/download/",
        "/api/audio-export/status/",
        "/api/audio-export/download/",
        "/api/media/status/",
        "/api/media/download/",
    )
    post_exact = (
        "/api/tools/pdf-to-docx",
        "/api/import-file/start",
        "/api/import-file",
        "/api/load",
        "/api/quick-text",
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
        "/api/chat/provider",
        "/api/voice",
        "/api/laboratory/reset",
        "/api/chat/reset",
        "/api/dialogue/turn",
        "/api/voice/test",
        "/api/chat",
    )
    post_prefixes = (
        "/api/tools/pdf-to-docx/cancel/",
        "/api/audio-export/cancel/",
        "/api/media/cancel/",
        "/api/media/mount/",
    )
    raw_post_exact = ("/api/media/transcribe", "/api/media/translate", "/api/tools/pdf-to-docx")
    routes = [Route("GET", path, path) for path in get_exact]
    routes.extend(Route("GET", path, path, prefix=True) for path in get_prefixes)
    routes.extend(Route("HEAD", path, path) for path in ("/", "/health", "/api/status"))
    routes.extend(Route("HEAD", path, path, prefix=True) for path in ("/static/", "/audio/"))
    routes.extend(Route("POST", path, path) for path in post_exact)
    routes.extend(Route("POST", path, path) for path in raw_post_exact)
    routes.extend(Route("POST", path, path, prefix=True) for path in post_prefixes)
    return Router(tuple(routes))
