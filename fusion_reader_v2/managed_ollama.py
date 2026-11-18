"""An Ollama child owned by this Panda process; never adopts a running daemon."""

from __future__ import annotations

import os
import shutil
import socket
import threading
import time
from pathlib import Path

from .owned_subprocess import OwnedProcessError, run_owned


def process_owns_listener(process, port):
    if process is None or process.poll() is not None:
        return False
    try:
        sockets = set()
        for path in Path(f"/proc/{process.pid}/fd").iterdir():
            try:
                sockets.add(path.readlink().as_posix())
            except OSError:
                continue
        for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
            fields = line.split()
            if fields[1] == f"0100007F:{port:04X}" and fields[3] == "0A":
                if f"socket:[{fields[9]}]" in sockets:
                    return True
    except OSError:
        pass
    return False


class ManagedOllama:
    def __init__(
        self,
        *,
        models_dir: Path,
        port=11435,
        reserve_mib=4096,
        command=None,
        port_available=None,
        listener_owned=process_owns_listener,
    ):
        self.models_dir = Path(models_dir)
        self.port = int(port)
        self.reserve_mib = max(0, int(reserve_mib))
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.command = command
        self.port_available = port_available or self._port_available
        self.listener_owned = listener_owned
        self.cancel = threading.Event()
        self.spawned = threading.Event()
        self.process = None
        self.thread: threading.Thread | None = None
        self.detail = "not_started"
        self.stopping = False

    def _port_available(self):
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", self.port))
            return True
        except OSError:
            return False

    def ready(self):
        return not self.stopping and self.process is not None and self.listener_owned(self.process, self.port)

    def status(self):
        ready = self.ready()
        return {
            "ok": ready,
            "ownership": "dedicated_child",
            "endpoint": self.base_url,
            "pid": self.process.pid if self.process is not None else None,
            "detail": "owned_listener_verified" if ready else self.detail,
        }

    def start(self, timeout=20):
        if self.thread is not None:
            return self.status()
        if not self.port_available():
            self.detail = "dedicated_endpoint_occupied_not_adopted"
            return self.status()
        if not self.models_dir.is_dir() or not os.access(self.models_dir, os.R_OK | os.X_OK):
            self.detail = "model_store_not_readable"
            return self.status()
        executable = shutil.which("ollama") if self.command is None else None
        if self.command is None and not executable:
            self.detail = "ollama_executable_missing"
            return self.status()
        command = self.command or [executable, "serve"]
        environment = dict(os.environ)
        environment.update(
            {
                "OLLAMA_HOST": self.base_url,
                "OLLAMA_MODELS": str(self.models_dir),
                "OLLAMA_MAX_LOADED_MODELS": "1",
                "OLLAMA_NUM_PARALLEL": "1",
                "OLLAMA_NOPRUNE": "true",
                "OLLAMA_NO_CLOUD": "true",
                "OLLAMA_GPU_OVERHEAD": str(self.reserve_mib * 1024 * 1024),
            }
        )

        def started(process):
            self.process = process
            self.spawned.set()

        def worker():
            try:
                result = run_owned(
                    command,
                    env=environment,
                    timeout=float("inf"),
                    cancel_event=self.cancel,
                    on_start=started,
                    text=True,
                    output_limit=65536,
                )
                self.detail = f"owned_child_exited:{result.returncode}"
            except OwnedProcessError:
                self.detail = "owned_child_stopped" if self.cancel.is_set() else "owned_child_failed"
            except Exception as exc:
                self.detail = "owned_child_failed:" + type(exc).__name__
            finally:
                self.spawned.set()

        self.thread = threading.Thread(target=worker, name="panda-owned-ollama")
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.thread.is_alive() and not self.cancel.is_set():
            if self.ready():
                return self.status()
            self.spawned.wait(0.05)
            time.sleep(0.02)
        failure = "dedicated_start_timeout" if self.thread.is_alive() else self.detail
        self.stop()
        self.detail = failure
        return self.status()

    def request_stop(self):
        self.stopping = True
        self.cancel.set()

    def stop(self):
        self.request_stop()
        if self.thread is not None:
            self.thread.join(5)
        alive = self.thread is not None and self.thread.is_alive()
        return {
            "ok": not alive,
            "ownership": "dedicated_child",
            "released": self.process is not None and self.process.poll() is not None,
            "detail": "owned_child_pending" if alive else self.detail,
        }
