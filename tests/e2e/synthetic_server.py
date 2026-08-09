from __future__ import annotations

import signal
import tempfile
from dataclasses import replace
from pathlib import Path

from fusion_reader_v2 import DictationAssistant, NullChatProvider
from fusion_reader_v2.composition import create_http_server
from fusion_reader_v2.config import create_settings
from tests.helpers import SyntheticWavTTSProvider, test_app


class InstallableNullChatProvider(NullChatProvider):
    default_model = "qwen3:4b"

    def __init__(self, answer: str) -> None:
        super().__init__(answer)
        self.installed = False

    def health(self) -> dict:
        return {
            "ok": True,
            "provider": "ollama",
            "model": self.default_model,
            "model_present": self.installed,
        }

    def install_model(self, model: str = "", *, cancel_event=None) -> dict:
        self.installed = model == self.default_model and not cancel_event.is_set()
        return {"ok": self.installed, "model": model, "detail": "installed"}

    def preload_model(self, model: str = "", *, keep_alive="10m") -> dict:
        return {
            "ok": self.installed and model == self.default_model,
            "model": model,
            "keep_alive": keep_alive,
            "load_duration_ms": 12,
            "duration_ms": 20,
        }


def main() -> None:
    tempdir = tempfile.TemporaryDirectory(prefix="fusion_reader_e2e_")
    root = Path(tempdir.name)
    app = test_app(
        tts=SyntheticWavTTSProvider(output_root=root / "tts"),
        dictation_assistant=DictationAssistant(
            {
                "local": InstallableNullChatProvider(
                    '{"kind":"delete_from","text":"","target":"Buenos Aires","scope":"","number":0,"all_matches":false}'
                ),
                "openai": NullChatProvider(
                    '{"kind":"replace_selection","text":"Versión de nube.","target":"",'
                    '"scope":"","number":0,"all_matches":false}'
                ),
            }
        ),
        root=root,
        register_cleanup=False,
    )
    settings = create_settings(
        environ={
            "HOME": str(root),
            "FUSION_READER_RUNTIME_ROOT": str(root / "runtime"),
            "FUSION_READER_LIBRARY_ROOT": str(root / "library"),
            "FUSION_READER_DOWNLOADS_ROOT": str(root / "downloads"),
        }
    )
    settings = replace(settings, ports=replace(settings.ports, api=0))
    server = create_http_server(app, settings)

    def stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"READY {server.server_address[1]}", flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.shutdown_background_work(timeout=10.0)
        tempdir.cleanup()


if __name__ == "__main__":
    main()
