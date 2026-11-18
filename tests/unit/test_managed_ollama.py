import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class ManagedOllamaTests(unittest.TestCase):
    def test_owned_endpoint_can_restart_after_time_wait_but_not_adopt_live_listener(self):
        import socket
        from fusion_reader_v2.managed_ollama import ManagedOllama

        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = listener.getsockname()[1]
                manager = ManagedOllama(models_dir=Path(folder), port=port)
                self.assertFalse(manager._port_available())
                with socket.create_connection(("127.0.0.1", port)) as client:
                    connection, _ = listener.accept()
                    connection.sendall(b"fixture")
                    connection.close()
                    client.recv(7)
            self.assertTrue(manager._port_available())

    def test_shared_models_are_never_unloaded_by_usage_or_default_name(self):
        from fusion_reader_v2.desktop_lifetime import release_gpu_resources
        from fusion_reader_v2.config import create_settings

        root = Path(tempfile.mkdtemp())
        settings = create_settings(
            repository_root=Path.cwd(), environ={"HOME": str(root), "FUSION_READER_RUNTIME_ROOT": str(root / "runtime")}
        )
        settings.providers.tts_owner_file.parent.mkdir(parents=True, exist_ok=True)
        local = SimpleNamespace(
            name="ollama", base_url="http://127.0.0.1:11434", default_model="test:14b", used_models={"test:14b"}
        )
        app = SimpleNamespace(
            conversation=SimpleNamespace(provider=SimpleNamespace(providers={"local": local})),
            dictation_assistant=None,
            stt=SimpleNamespace(name="none"),
            tts=SimpleNamespace(base_url="http://127.0.0.1:7853"),
        )
        writes = []

        def request(url, **kwargs):
            if not isinstance(url, str):
                writes.append(url)
            return io.BytesIO(json.dumps({"models": [{"name": "test:14b"}]}).encode())

        with (
            patch("fusion_reader_v2.desktop_lifetime.urlopen", side_effect=request, create=True),
            patch("fusion_reader_v2.desktop_lifetime.run_owned", return_value=SimpleNamespace(stdout="")),
        ):
            out = release_gpu_resources(app, settings)
        self.assertEqual(writes, [])
        self.assertTrue(out["pending"])
        self.assertEqual(out["release_scope"], "panda_owned_resources_only")

    def test_dedicated_manager_does_not_adopt_an_occupied_endpoint(self):
        from fusion_reader_v2.managed_ollama import ManagedOllama

        root = Path(tempfile.mkdtemp())
        manager = ManagedOllama(models_dir=root, port=11435, port_available=lambda: False)
        with patch("fusion_reader_v2.managed_ollama.run_owned") as run:
            status = manager.start()
        self.assertFalse(status["ok"])
        self.assertIn("occupied", status["detail"])
        run.assert_not_called()
        self.assertFalse(manager.ready())

    def test_dedicated_child_is_owned_and_reaped_on_stop(self):
        from fusion_reader_v2.managed_ollama import ManagedOllama

        root = Path(tempfile.mkdtemp())
        manager = ManagedOllama(
            models_dir=root,
            port=11435,
            port_available=lambda: True,
            listener_owned=lambda process, port: process.poll() is None,
            command=[sys.executable, "-c", "import time; time.sleep(1.2)"],
        )
        try:
            self.assertTrue(manager.start()["ok"])
            self.assertTrue(manager.ready())
            process = manager.process
            result = manager.stop()
            self.assertTrue(result["ok"])
            self.assertIsNotNone(process.poll())
            self.assertFalse(manager.ready())
        finally:
            manager.stop()

    def test_consumer_inventory_includes_correctors_and_stt(self):
        from fusion_reader_v2.resources import consumer_inventory
        from tests.helpers import test_app

        app = test_app()
        inventory = consumer_inventory(app)
        roles = {item["role"] for item in inventory}
        self.assertTrue(
            {
                "reader_tts",
                "dialogue_stt",
                "dictation_correction",
                "media_correction",
                "media_translation",
                "chat_local",
                "dictation_local",
                "dictation_local14b",
                "reading_alignment",
            }
            <= roles
        )

    def test_unowned_private_endpoint_is_never_called_even_if_alive(self):
        from fusion_reader_v2.conversation import OllamaChatProvider
        from fusion_reader_v2.transcript_correction import OllamaTranscriptCorrector

        provider = OllamaChatProvider(base_url="http://127.0.0.1:11435")
        provider.availability_guard = lambda: False
        corrector = OllamaTranscriptCorrector(base_url=provider.base_url)
        corrector.availability_guard = lambda: False
        with patch("urllib.request.urlopen") as request:
            self.assertFalse(provider.health()["ok"])
            self.assertFalse(provider.chat([{"role": "user", "content": "Hola"}]).ok)
            self.assertFalse(provider.install_model()["ok"])
            self.assertFalse(corrector.health()["ok"])
            self.assertFalse(corrector.correct("Texto original.").accepted)
        request.assert_not_called()

    def test_listener_proof_matches_only_the_owned_child_socket(self):
        import threading
        import time
        from fusion_reader_v2.managed_ollama import process_owns_listener
        from fusion_reader_v2.owned_subprocess import run_owned

        root = Path(tempfile.mkdtemp())
        port_file = root / "port.txt"
        cancel = threading.Event()
        processes, errors = [], []
        command = [
            sys.executable,
            "-c",
            "import socket,time,sys; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); open(sys.argv[1],'w').write(str(s.getsockname()[1])); time.sleep(1.2)",
            str(port_file),
        ]

        def child():
            try:
                run_owned(command, timeout=3, cancel_event=cancel, on_start=processes.append)
            except Exception as exc:
                if not cancel.is_set():
                    errors.append(exc)

        thread = threading.Thread(target=child)
        thread.start()
        try:
            deadline = time.monotonic() + 2
            while (not port_file.exists() or not port_file.read_text()) and time.monotonic() < deadline:
                time.sleep(0.01)
            port = int(port_file.read_text())
            self.assertTrue(process_owns_listener(processes[0], port))
            self.assertFalse(process_owns_listener(processes[0], port + 1))
        finally:
            cancel.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_binding_routes_all_local_consumers_to_private_ollama(self):
        from tests.helpers import test_app
        from fusion_reader_v2.config import create_settings
        from fusion_reader_v2.resources import ResourceController, dedicated_settings
        from fusion_reader_v2.conversation import ConversationCore, OllamaChatProvider

        app = test_app()
        app.conversation = ConversationCore(OllamaChatProvider())
        settings = dedicated_settings(create_settings(repository_root=Path.cwd(), environ={}))
        daemon = SimpleNamespace(ready=lambda: False)
        ResourceController(settings, daemon).bind(app)
        self.assertEqual(app.conversation.provider.base_url, "http://127.0.0.1:11435")
        self.assertEqual(app._dictation_corrector.base_url, "http://127.0.0.1:11435")
        for provider in app.dictation_assistant.providers.values():
            if getattr(provider, "name", "") == "ollama":
                self.assertEqual(provider.base_url, "http://127.0.0.1:11435")
                self.assertFalse(provider.availability_guard())

    def test_desktop_main_constructs_private_settings_and_closes_owned_daemon(self):
        from tests.helpers import test_app
        from fusion_reader_v2.config import create_settings
        from fusion_reader_v2.web import server as web_server
        from fusion_reader_v2.conversation import ConversationCore, OllamaChatProvider
        from unittest.mock import Mock

        root = Path(tempfile.mkdtemp())
        settings = create_settings(
            repository_root=Path.cwd(), environ={"HOME": str(root), "FUSION_READER_RUNTIME_ROOT": str(root / "runtime")}
        )
        app = test_app()
        app.conversation = ConversationCore(OllamaChatProvider())
        daemon = SimpleNamespace(
            start=Mock(return_value={"ok": False}),
            ready=lambda: False,
            stop=Mock(return_value={"ok": True, "released": True}),
            cancel=Mock(),
        )
        server = SimpleNamespace(serve_forever=Mock(), server_close=Mock(), shutdown=Mock())
        lifetime = SimpleNamespace(start=Mock(), stop=Mock())
        with (
            patch.object(web_server, "create_settings", return_value=settings),
            patch("fusion_reader_v2.composition.create_fusion_reader", return_value=app),
            patch("fusion_reader_v2.managed_ollama.ManagedOllama", return_value=daemon),
            patch.object(web_server, "create_http_server", return_value=server) as create_server,
            patch("fusion_reader_v2.desktop_lifetime.DesktopLifetime", return_value=lifetime),
            patch("fusion_reader_v2.desktop_lifetime.run_owned", return_value=SimpleNamespace(stdout="")),
            patch.object(web_server.signal, "signal"),
        ):
            web_server.main()
        self.assertEqual(create_server.call_args.args[1].providers.ollama_url, "http://127.0.0.1:11435")
        self.assertEqual(app.conversation.provider.base_url, "http://127.0.0.1:11435")
        self.assertEqual(app._dictation_corrector.base_url, "http://127.0.0.1:11435")
        daemon.start.assert_called_once()
        daemon.stop.assert_called_once()
        server.server_close.assert_called_once()

    def test_managed_cli_fallback_is_cpu_and_long_form_preserves_that_policy(self):
        from tests.helpers import test_app
        from fusion_reader_v2.config import create_settings
        from fusion_reader_v2.resources import ResourceController
        from fusion_reader_v2.dialogue import AutoSTTProvider, WhisperCliSTTProvider, NullSTTProvider
        from fusion_reader_v2.services.media import long_form_stt_provider

        app = test_app()
        app.stt = AutoSTTProvider(NullSTTProvider(), WhisperCliSTTProvider())
        controller = ResourceController(create_settings(repository_root=Path.cwd(), environ={}))
        controller.bind(app)
        self.assertEqual(app.stt.fallback.resource_device, "cpu")
        media_stt = long_form_stt_provider(app.stt, 60)
        controller.bind_stt(media_stt)
        self.assertEqual(media_stt.fallback.resource_device, "cpu")

    def test_private_child_shares_store_without_pruning_or_changing_global_environment(self):
        import os
        from fusion_reader_v2.managed_ollama import ManagedOllama
        from fusion_reader_v2.owned_subprocess import run_owned

        root = Path(tempfile.mkdtemp())
        sentinel = root / "existing-model"
        sentinel.write_bytes(b"keep-existing-weights")
        manager = ManagedOllama(
            models_dir=root,
            port=11435,
            reserve_mib=4096,
            port_available=lambda: True,
            listener_owned=lambda process, port: process.poll() is None,
            command=[sys.executable, "-c", "import time; time.sleep(1.2)"],
        )
        try:
            with patch.dict(os.environ, {"OLLAMA_HOST": "http://127.0.0.1:11434"}):
                with patch("fusion_reader_v2.managed_ollama.run_owned", wraps=run_owned) as launch:
                    self.assertTrue(manager.start()["ok"])
                    environment = launch.call_args.kwargs["env"]
                    self.assertEqual(environment["OLLAMA_HOST"], "http://127.0.0.1:11435")
                    self.assertEqual(environment["OLLAMA_MODELS"], str(root))
                    self.assertEqual(environment["OLLAMA_NOPRUNE"], "true")
                    self.assertEqual(environment["OLLAMA_NO_CLOUD"], "true")
                    self.assertEqual(environment["OLLAMA_GPU_OVERHEAD"], str(4096 * 1024 * 1024))
                    self.assertEqual(os.environ["OLLAMA_HOST"], "http://127.0.0.1:11434")
            self.assertEqual(sentinel.read_bytes(), b"keep-existing-weights")
            self.assertEqual(set(root.iterdir()), {sentinel})
        finally:
            manager.stop()

    def test_failed_api_start_does_not_claim_existing_voice_services(self):
        from tests.helpers import test_app
        from fusion_reader_v2.config import create_settings
        from fusion_reader_v2.web import server as web_server
        from unittest.mock import Mock

        app = test_app()
        root = Path(tempfile.mkdtemp())
        settings = create_settings(repository_root=Path.cwd(), environ={"HOME": str(root)})
        daemon = SimpleNamespace(start=Mock(return_value={"ok": False}), ready=lambda: False, cancel=Mock())
        with (
            patch.object(web_server, "create_settings", return_value=settings),
            patch("fusion_reader_v2.composition.create_fusion_reader", return_value=app),
            patch("fusion_reader_v2.managed_ollama.ManagedOllama", return_value=daemon),
            patch.object(web_server, "create_http_server", side_effect=OSError("API occupied")),
            patch("fusion_reader_v2.desktop_lifetime.release_gpu_resources", return_value={}) as cleanup,
            patch.object(web_server.signal, "signal"),
        ):
            with self.assertRaisesRegex(OSError, "API occupied"):
                web_server.main()
        self.assertFalse(cleanup.call_args.kwargs["release_services"])

    def test_owned_daemon_stop_is_requested_before_http_join(self):
        from fusion_reader_v2.web.server import FusionHTTPServer

        events = []
        server = object.__new__(FusionHTTPServer)
        app = SimpleNamespace(
            shutdown_background_work=lambda **kw: {"ok": True},
            resource_controller=SimpleNamespace(begin_shutdown=lambda: events.append("owned_stop")),
        )
        server.context = SimpleNamespace(app=app, request_shutdown_jobs=lambda: {}, shutdown_jobs=lambda **kw: {})
        with patch("http.server.HTTPServer.server_close", side_effect=lambda *a: events.append("socket_closed")):
            server.server_close()
        self.assertEqual(events, ["owned_stop", "socket_closed"])
