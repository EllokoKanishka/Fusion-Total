"""Last-window shutdown and narrowly scoped GPU cleanup for Panda."""

from __future__ import annotations

import json
import ctypes
import errno
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .owned_subprocess import run_owned


class DesktopLifetime:
    def __init__(self, shutdown, *, grace=4.0, ttl=180.0, clock=time.monotonic):
        self.shutdown = shutdown
        self.grace, self.ttl, self.clock = grace, ttl, clock
        self.clients: dict[str, float] = {}
        self.lock = threading.Lock()
        self.empty_since: float | None = None
        self.armed = False
        self.closing = False
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._watch, name="panda-desktop-lifetime")

    def update(self, client: str, *, leaving: bool = False) -> dict:
        if not re.fullmatch(r"[a-f0-9-]{36}", client):
            raise ValueError("invalid_desktop_client")
        with self.lock:
            if self.closing:
                return {"ok": False, "state": "closing"}
            if leaving:
                self.clients.pop(client, None)
            else:
                if len(self.clients) >= 128 and client not in self.clients:
                    raise ValueError("too_many_desktop_clients")
                self.clients[client] = self.clock()
                self.armed = True
                self.empty_since = None
            return {"ok": True, "clients": len(self.clients)}

    def tick(self) -> bool:
        with self.lock:
            now = self.clock()
            self.clients = {key: seen for key, seen in self.clients.items() if now - seen < self.ttl}
            if self.clients or not self.armed or self.closing:
                return False
            if self.empty_since is None:
                self.empty_since = now
            if now - self.empty_since < self.grace:
                return False
            self.closing = True
        self.shutdown()
        return True

    def _watch(self):
        while not self.stop_event.wait(0.5):
            if self.tick():
                return

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread.ident is not None and self.thread is not threading.current_thread():
            self.thread.join(5)


def _process_matches(pid: int, *, marker: str, repository: Path | None = None, port: int | None = None) -> bool:
    try:
        proc = Path("/proc") / str(pid)
        args = proc.joinpath("cmdline").read_bytes().decode().split("\0")
        if not any(marker in arg for arg in args):
            return False
        if repository is not None and proc.joinpath("cwd").resolve() != repository.resolve():
            return False
        if port is not None and not any(args[i : i + 2] == ["--port", str(port)] for i in range(len(args) - 1)):
            return False
        return pid > 1 and pid != os.getpid()
    except (OSError, UnicodeError):
        return False


def _stop_process(pid: int, *, check=None) -> bool:
    # A pidfd cannot accidentally signal another process after PID reuse.
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.pidfd_open(pid, 0)
    if fd < 0:
        error = ctypes.get_errno()
        if error == errno.ESRCH:
            return True
        raise OSError(error, "pidfd_open failed")

    def send(sig):
        if libc.pidfd_send_signal(fd, int(sig), None, 0) < 0:
            error = ctypes.get_errno()
            raise OSError(error, "pidfd_send_signal failed")

    try:
        if check is not None and not check():
            return False
        send(signal.SIGTERM)
        import select

        ready, _, _ = select.select([fd], [], [], 8)
        if not ready:
            send(signal.SIGKILL)
            ready, _, _ = select.select([fd], [], [], 3)
        return bool(ready)
    except ProcessLookupError:
        return True
    finally:
        os.close(fd)


def release_gpu_resources(app, settings, *, release_services=True) -> dict:
    result: dict = {"ollama": [], "tts_stopped": False, "stt_stopped": False, "errors": []}
    providers: list[Any] = []
    chat = app.conversation.provider
    providers.extend(getattr(chat, "providers", {"local": chat}).values())
    providers.extend(getattr(app.dictation_assistant, "providers", {}).values())
    from .resources import consumer_inventory

    result["release_scope"] = "panda_owned_resources_only"
    result["pending"] = []
    result["consumers"] = consumer_inventory(app)
    controller = getattr(app, "resource_controller", None)
    daemon = getattr(controller, "daemon", None)
    if daemon is not None:
        owned = daemon.stop()
        result["owned_ollama"] = owned
        if not owned.get("ok"):
            result["pending"].append("owned_ollama_child")
    else:
        for provider in providers + [getattr(app, "_dictation_corrector", None)]:
            if provider is not None and (getattr(provider, "name", "") == "ollama" or hasattr(provider, "keep_alive")):
                result["pending"].append(
                    {
                        "resource": "shared_ollama_not_unloaded",
                        "model": str(getattr(provider, "default_model", "") or getattr(provider, "model", "")),
                    }
                )
    if not release_services:
        result["pending"].append("tts_stt_not_claimed_during_failed_startup")
        result["all_vram_released"] = False
        return result
    try:
        owner = json.loads(settings.providers.tts_owner_file.read_text())
        pid = int(owner.get("owner_pid", 0))
        if (
            owner.get("owner") == "fusion_reader_v2"
            and owner.get("port") == 7853
            and _process_matches(pid, marker="tts_server:app", port=7853)
        ):
            result["tts_stopped"] = _stop_process(
                pid, check=lambda: _process_matches(pid, marker="tts_server:app", port=7853)
            )
    except (OSError, ValueError, TypeError) as exc:
        result["errors"].append(f"tts:{type(exc).__name__}")
    try:
        # Only the dedicated Fusion STT script in this repository is eligible.
        proc = run_owned(
            ["ss", "-ltnp", "sport", "=", f":{settings.ports.stt}"], capture_output=True, text=True, timeout=3
        )
        for value in re.findall(r"pid=(\d+)", proc.stdout):
            pid = int(value)
            if _process_matches(pid, marker="fusion_reader_v2_stt_server.py", repository=settings.paths.repository):
                result["stt_stopped"] = _stop_process(
                    pid,
                    check=lambda: _process_matches(
                        pid, marker="fusion_reader_v2_stt_server.py", repository=settings.paths.repository
                    ),
                )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["errors"].append(f"stt:{type(exc).__name__}")
    if not result["tts_stopped"]:
        result["pending"].append("tts_not_verified_released")
    if not result["stt_stopped"]:
        result["pending"].append("stt_not_verified_released")
    result["all_vram_released"] = False
    return result
