from __future__ import annotations

import unittest

from fusion_reader_v2.services.dialogue import DialogueService


class Conversation:
    def reasoning_status(self, mode: str = "") -> dict:
        return {"mode": mode or "thinking"}


class DialogueServiceTests(unittest.TestCase):
    def test_service_owns_history_and_modes_without_facade(self) -> None:
        events: list[dict] = []
        persists: list[bool] = []
        service = DialogueService(
            conversation=Conversation(),
            persist=lambda: persists.append(True),
            trace=events.append,
            services_status=lambda: {
                "stt": {},
                "tts": {},
                "chat": {},
                "external_research": {},
            },
        )
        service.remember_chat_turn("hola", "respuesta")
        self.assertEqual(len(service.chat_history), 2)
        self.assertEqual(service.set_profile("bohemia")["mode"], "bohemia")
        self.assertEqual(service.set_laboratory_mode("free")["mode"], "free")
        self.assertEqual(len(persists), 2)
        self.assertEqual([event["event"] for event in events], ["profile_changed", "laboratory_mode_changed"])
        self.assertEqual(service.clear_history()["chat_items"], 2)


if __name__ == "__main__":
    unittest.main()
