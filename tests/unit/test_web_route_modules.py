from __future__ import annotations

import unittest

from fusion_reader_v2.web.routes.health import handle_health_get
from fusion_reader_v2.web.routes.audio import handle_audio_get, handle_audio_post
from fusion_reader_v2.web.routes.notes import handle_notes_get, handle_notes_post
from fusion_reader_v2.web.routes.preparation import handle_preparation_get, handle_preparation_post
from fusion_reader_v2.web.routes.tools import handle_tools_get
from fusion_reader_v2.web.routes.dialogue import handle_dialogue_post
from fusion_reader_v2.web.routes.reading import handle_reading_post


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


class App:
    def get_voice_catalog(self) -> dict:
        return {"ok": True, "voices": ["voice.wav"]}

    def list_notes(self, **filters) -> dict:
        return {"ok": True, "filters": filters}

    def create_note(self, text: str, chunk_index=None) -> dict:
        return {"ok": True, "text": text, "chunk_index": chunk_index}

    def next(self) -> dict:
        return {"ok": True, "current": 2}

    def set_voice(self, voice: str) -> dict:
        return {"ok": True, "voice": voice}

    def set_profile(self, mode: str) -> dict:
        return {"ok": True, "mode": mode}

    def prepare_status(self) -> dict:
        return {"ok": True, "state": "idle"}

    def prepare_document(self, start: str) -> dict:
        return {"ok": True, "start": start}

    def cancel_prepare(self) -> dict:
        return {"ok": True, "state": "cancelled"}


class DomainResponder(Responder):
    def __init__(self) -> None:
        super().__init__()
        self.app = App()

    def _result(self, status: int, payload: dict) -> None:
        self._json(status, payload)


class WebRouteModuleTests(unittest.TestCase):
    def test_health_route_module_dispatches_only_owned_routes(self) -> None:
        responder = Responder()
        self.assertTrue(handle_health_get(responder, "/health/live"))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["status"], "live")
        self.assertTrue(handle_health_get(responder, "/api/build"))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["commit"], "abc")
        self.assertFalse(handle_health_get(responder, "/api/library"))  # type: ignore[arg-type]

    def test_audio_and_notes_modules_dispatch_their_routes(self) -> None:
        responder = DomainResponder()
        self.assertTrue(handle_audio_get(responder, "/api/voices", "/api/voices"))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["voices"], ["voice.wav"])
        self.assertTrue(
            handle_notes_get(responder, "/api/notes", "/api/notes?doc_id=book&chunk_index=2&current_only=1")  # type: ignore[arg-type]
        )
        self.assertEqual(responder.responses[-1][1]["filters"]["chunk_index"], 2)

    def test_tools_module_rejects_missing_import_job(self) -> None:
        responder = DomainResponder()
        responder.context.import_jobs = type("Jobs", (), {"get": lambda self, key: None})()  # type: ignore[attr-defined]
        self.assertTrue(
            handle_tools_get(responder, "/api/import-status", "/api/import-status?id=missing")  # type: ignore[arg-type]
        )
        self.assertEqual(responder.responses[-1][0], 404)

    def test_post_modules_dispatch_reading_audio_notes_and_dialogue(self) -> None:
        responder = DomainResponder()
        self.assertTrue(handle_reading_post(responder, "/api/next", {}))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["current"], 2)
        self.assertTrue(handle_audio_post(responder, "/api/voice", {"voice": "female.wav"}))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["voice"], "female.wav")
        self.assertTrue(handle_notes_post(responder, "/api/notes/create", {"text": "nota"}))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["text"], "nota")
        self.assertTrue(handle_dialogue_post(responder, "/api/profile", {"mode": "bohemia"}))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["mode"], "bohemia")

    def test_preparation_module_owns_status_start_and_cancel(self) -> None:
        responder = DomainResponder()
        self.assertTrue(handle_preparation_get(responder, "/api/prepare/status"))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["state"], "idle")
        self.assertTrue(handle_preparation_post(responder, "/api/prepare/start", {"start": "beginning"}))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["start"], "beginning")
        self.assertTrue(handle_preparation_post(responder, "/api/prepare/cancel", {}))  # type: ignore[arg-type]
        self.assertEqual(responder.responses[-1][1]["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
