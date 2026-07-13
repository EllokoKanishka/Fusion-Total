from __future__ import annotations

import unittest

from fusion_reader_v2.web.routes.health import handle_health_get


class Context:
    runtime_info = {"commit": "abc"}

    def status(self) -> dict:
        return {"ok": True, "services": {}}


class Responder:
    def __init__(self) -> None:
        self.context = Context()
        self.responses: list[tuple[int, dict]] = []

    def _json(self, status: int, payload: dict) -> None:
        self.responses.append((status, payload))


class WebRouteModuleTests(unittest.TestCase):
    def test_health_route_module_dispatches_only_owned_routes(self) -> None:
        responder = Responder()
        self.assertTrue(handle_health_get(responder, "/health/live"))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["status"], "live")
        self.assertTrue(handle_health_get(responder, "/api/build"))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["commit"], "abc")
        self.assertFalse(handle_health_get(responder, "/api/library"))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
