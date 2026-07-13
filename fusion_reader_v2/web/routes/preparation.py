from __future__ import annotations

from typing import Protocol

from fusion_reader_v2 import FusionReaderV2


class PreparationResponder(Protocol):
    @property
    def app(self) -> FusionReaderV2: ...

    def _json(self, status: int, payload: dict) -> None: ...


def handle_preparation_get(responder: PreparationResponder, path: str) -> bool:
    if path != "/api/prepare/status":
        return False
    responder._json(200, responder.app.prepare_status())
    return True


def handle_preparation_post(responder: PreparationResponder, path: str, payload: dict) -> bool:
    if path == "/api/prepare/start":
        responder._json(200, responder.app.prepare_document(start=str(payload.get("start") or "cursor")))
        return True
    if path == "/api/prepare/cancel":
        responder._json(200, responder.app.cancel_prepare())
        return True
    return False


__all__ = ["handle_preparation_get", "handle_preparation_post"]
