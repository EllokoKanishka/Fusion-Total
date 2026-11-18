import unittest
from dataclasses import replace
from unittest.mock import patch

from fusion_reader_v2.config import ConfigurationError, create_settings
from fusion_reader_v2.tts import AllTalkProvider


class ResourceOwnershipTests(unittest.TestCase):
    def test_reserved_voice_ports_cannot_be_reached_through_loopback_aliases(self):
        settings = create_settings(repository_root=__import__("pathlib").Path.cwd(), environ={})
        for host in (
            "127.1",
            "127.0.0.2",
            "2130706433",
            "0x7f000001",
            "0177.0.0.1",
            "localhost.",
            "[::ffff:127.0.0.1]",
            "0.0.0.0",
        ):
            for port in (7852, 7854):
                with self.subTest(host=host, port=port):
                    url = f"http://{host}:{port}"
                    with self.assertRaises(ConfigurationError):
                        replace(settings.providers, tts_url=url).validate(settings.ports)
                    with patch.object(AllTalkProvider, "_preferred_base_url", lambda self, value: value):
                        provider = AllTalkProvider(base_url=url)
                    self.assertFalse(provider._owner_guard()[0])

    def test_aliases_of_panda_port_still_require_an_owner(self):
        for host in ("127.1", "2130706433", "0x7f000001", "[::ffff:127.0.0.1]"):
            with (
                self.subTest(host=host),
                patch.object(AllTalkProvider, "_preferred_base_url", lambda self, value: value),
            ):
                provider = AllTalkProvider(base_url=f"http://{host}:7853", owner_file="/nonexistent/panda-owner")
                self.assertFalse(provider._owner_guard()[0])

    def test_generated_audio_reference_cannot_cross_reserved_voice_ports(self):
        with patch.object(AllTalkProvider, "_preferred_base_url", lambda self, value: value):
            provider = AllTalkProvider(base_url="http://127.0.0.1:7853")
        for reference in (
            "http://127.1:7854/audio.wav",
            "http://localhost:7852/audio.wav",
            "http://[::ffff:127.0.0.1]:7854/audio.wav",
        ):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                provider._audio_url(reference)
