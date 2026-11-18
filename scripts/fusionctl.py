#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from subprocess import TimeoutExpired

from fusion_reader_v2.config import Settings, create_settings
from fusion_reader_v2.network import http_base_url
from fusion_reader_v2.owned_subprocess import run_owned
from fusion_reader_v2.tts import AudioCache
from fusion_reader_v2.version import __version__


def _json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _request_json(url: str, timeout: float = 3.0) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_status_payload")
    return payload


def _base_url(settings: Settings) -> str:
    return http_base_url(settings.security.bind_host, settings.ports.api)


def _project_environment(repository: Path, inherited: dict[str, str]) -> dict[str, str]:
    """Match the shared Bash loader without evaluating shell expressions."""
    environment = inherited.copy()
    if environment.get("FUSION_READER_IGNORE_ENV_FILE") == "1":
        return environment
    try:
        lines = (repository / ".env").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return environment
    for line in lines:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if match:
            key, value = match.groups()
            if not environment.get(key):
                environment[key] = value.removeprefix('"').removesuffix('"').removeprefix("'").removesuffix("'")
    return environment


def _run_visible(command: list[str], *, cwd: Path, timeout: float, env: dict[str, str] | None = None) -> int:
    result = run_owned(command, cwd=cwd, env=env, timeout=timeout, text=True, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def command_start(settings: Settings, _args: argparse.Namespace) -> int:
    script = settings.paths.repository / "scripts" / "start_fusion_reader_v2.sh"
    if not script.is_file():
        _json({"ok": False, "error": "start_script_missing", "path": str(script)})
        return 1
    environment = _project_environment(settings.paths.repository, dict(os.environ))
    if getattr(_args, "no_engines", False):
        environment["FUSION_READER_START_ENGINES"] = "0"
    project_python = settings.paths.repository / ".venv/bin/python"
    environment.setdefault("FUSION_READER_PYTHON", str(project_python) if project_python.is_file() else sys.executable)
    environment.update(
        {
            "FUSION_READER_RUNTIME_ROOT": str(settings.paths.runtime),
            "FUSION_READER_RUNTIME_DIR": str(settings.paths.runtime),
            "FUSION_READER_LIBRARY_ROOT": str(settings.paths.library),
            "FUSION_READER_DOWNLOADS_ROOT": str(settings.paths.downloads),
            "FUSION_READER_CACHE_ROOT": str(settings.paths.cache),
            "FUSION_READER_LOG_ROOT": str(settings.paths.logs),
            "FUSION_READER_LOG_DIR": str(settings.paths.logs),
        }
    )
    environment.update(
        {
            "FUSION_READER_V2_PORT": str(settings.ports.api),
            "FUSION_READER_GPU_TTS_PORT": str(settings.ports.tts_gpu),
            "FUSION_READER_CPU_TTS_PORT": str(settings.ports.tts_cpu),
            "DIRECT_CHAT_ALLTALK_PORT": str(settings.ports.tts_cpu),
            "FUSION_READER_STT_PORT": str(settings.ports.stt),
            "FUSION_READER_STT_PROVIDER": settings.providers.stt_provider,
            "FUSION_READER_STT_URL": settings.providers.stt_url,
            "FUSION_READER_OLLAMA_URL": settings.providers.ollama_url,
            "FUSION_READER_TTS_OWNER_FILE": str(settings.providers.tts_owner_file),
            "FUSION_READER_OPENAI_EXECUTION_MODE": settings.providers.openai_chat_execution_mode,
            "FUSION_READER_OPENAI_CHAT_AGENT_DIR": str(settings.providers.openai_chat_agent_dir),
        }
    )
    aliases = {
        "tts_url": "ALLTALK_URL",
        "openclaw_command": "OPENCLAW_BIN",
        "research_provider": "EXTERNAL_RESEARCH_PROVIDER",
        "openai_chat_execution_mode": "OPENAI_EXECUTION_MODE",
    }
    for name, value in vars(settings.providers).items():
        if name == "stt_model":
            continue  # CLI fallback default must not override the GPU server default.
        key = aliases.get(
            name, name.removesuffix("_seconds").upper() if name.endswith("_timeout_seconds") else name.upper()
        )
        environment["FUSION_READER_" + key] = str(int(value)) if isinstance(value, bool) else str(value)
    for name, value in vars(settings.limits).items():
        environment["FUSION_READER_" + name.upper()] = str(value)
    resource_keys = {"models_dir": "OLLAMA_MODELS_DIR", "reserve_mib": "GPU_RESERVE_MIB"}
    for name, value in vars(settings.resources).items():
        environment["FUSION_READER_" + resource_keys.get(name, name.upper())] = str(value)
    environment["FUSION_READER_BIND_HOST"] = settings.security.bind_host
    defaults = {
        "TTS_GPU_START_WAIT_SECONDS": environment.get("FUSION_READER_GPU_TTS_WAIT_SECONDS", "90"),
        "TTS_CPU_START_WAIT_SECONDS": "60",
        "STT_GPU_START_WAIT_SECONDS": "120",
        "STARTUP_WAIT_SECONDS": "40",
    }
    waits = [float(environment.get("FUSION_READER_" + key, default)) for key, default in defaults.items()]
    if any(not math.isfinite(value) or value <= 0 for value in waits):
        raise ValueError("invalid_startup_wait")
    return _run_visible(["bash", str(script)], cwd=settings.paths.repository, env=environment, timeout=sum(waits) + 60)


def _pid_path(settings: Settings) -> Path:
    environment = _project_environment(settings.paths.repository, dict(os.environ))
    configured = environment.get("FUSION_READER_PID_FILE")
    return settings.paths.repository / configured if configured else settings.paths.runtime / "fusion_reader_v2.pid"


def _owned_server_pid(settings: Settings) -> tuple[int | None, str]:
    pid_path = _pid_path(settings)
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return None, "pid_file_missing"
    except (OSError, ValueError):
        return None, "pid_file_invalid"
    if pid <= 1:
        return None, "pid_file_invalid"
    proc = Path("/proc") / str(pid)
    try:
        if proc.stat().st_uid != os.getuid() or (proc / "cwd").resolve(strict=True) != settings.paths.repository:
            return None, "pid_owner_mismatch"
        command = (proc / "cmdline").read_bytes().split(b"\x00")
        module = b"scripts.fusion_reader_v2_server"
        if not any(arg == b"-m" and following == module for arg, following in zip(command, command[1:])):
            return None, "pid_owner_mismatch"
        env = (proc / "environ").read_bytes().split(b"\x00")
        runtime_key = b"FUSION_READER_RUNTIME_ROOT="
        runtime = next((item[len(runtime_key) :] for item in env if item.startswith(runtime_key)), None)
        if runtime is None or Path(os.fsdecode(runtime)).expanduser().resolve() != settings.paths.runtime:
            return None, "pid_owner_mismatch"
    except FileNotFoundError:
        return None, "pid_stale"
    except OSError:
        return None, "pid_unreadable"
    return pid, "owned"


def probe_startup(settings: Settings, _args: argparse.Namespace) -> int:
    """Return 3 for a free endpoint, 0 for our server and 2 for a conflict."""
    try:
        with socket.create_connection((settings.security.bind_host, settings.ports.api), timeout=0.5):
            pass
    except ConnectionRefusedError:
        return 3
    except OSError as exc:
        _json({"ok": False, "error": "listener_unknown", "detail": type(exc).__name__})
        return 2

    pid, _detail = _owned_server_pid(settings)
    if pid is not None:
        try:
            health = _request_json(f"{_base_url(settings)}/health/live", timeout=2)
        except (OSError, ValueError):
            health = {}
        if health == {"ok": True, "status": "live", "pid": pid}:
            _json({"ok": True, "state": "already_running", "pid": pid})
            return 0
    _json({"ok": False, "error": "port_occupied", "port": settings.ports.api})
    return 2


def command_stop(settings: Settings, _args: argparse.Namespace) -> int:
    pid_path = _pid_path(settings)
    pid, detail = _owned_server_pid(settings)
    if pid is None:
        if detail == "pid_stale":
            pid_path.unlink(missing_ok=True)
            _json({"ok": True, "state": "stopped", "detail": "stale_pid_removed"})
            return 0
        if detail == "pid_file_missing":
            _json({"ok": True, "state": "stopped", "detail": detail})
            return 0
        _json({"ok": False, "error": detail})
        return 1
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and (Path("/proc") / str(pid)).exists():
        time.sleep(0.1)
    if (Path("/proc") / str(pid)).exists():
        _json({"ok": False, "error": "stop_timeout", "pid": pid})
        return 1
    pid_path.unlink(missing_ok=True)
    _json({"ok": True, "state": "stopped", "pid": pid})
    return 0


def command_restart(settings: Settings, args: argparse.Namespace) -> int:
    stopped = command_stop(settings, args)
    return command_start(settings, args) if stopped == 0 else stopped


def command_status(settings: Settings, _args: argparse.Namespace) -> int:
    try:
        payload = _request_json(f"{_base_url(settings)}/api/status")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        _json({"ok": False, "error": "reader_unavailable", "detail": type(exc).__name__})
        return 1
    _json(payload)
    return 0 if payload.get("ok") else 1


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def _git_commit(repository: Path) -> str:
    try:
        result = run_owned(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def command_doctor(settings: Settings, _args: argparse.Namespace) -> int:
    ports = {
        "api": settings.ports.api,
        "tts_cpu": settings.ports.tts_cpu,
        "tts_forbidden": 7852,
        "tts_gpu": settings.ports.tts_gpu,
        "tts_external_doctora": 7854,
        "stt": settings.ports.stt,
        "ollama": settings.resources.private_ollama_port
        if settings.resources.ollama_mode == "dedicated"
        else settings.ports.ollama,
        "searxng": settings.ports.searxng,
    }
    owner: dict[str, object] = {"state": "missing"}
    try:
        raw_owner = json.loads(settings.providers.tts_owner_file.read_text(encoding="utf-8"))
        if not isinstance(raw_owner, dict):
            raise ValueError("invalid_tts_owner")
        owner = {
            "state": "valid"
            if raw_owner.get("owner") == "fusion_reader_v2" and int(raw_owner.get("port", 0)) == settings.ports.tts_gpu
            else "mismatch",
            "owner": str(raw_owner.get("owner") or ""),
            "port": raw_owner.get("port"),
        }
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    roots = {
        name: {
            "path": str(path),
            "exists": path.exists(),
            "readable": os.access(path, os.R_OK) if path.exists() else False,
        }
        for name, path in {
            "runtime": settings.paths.runtime,
            "library": settings.paths.library,
            "downloads": settings.paths.downloads,
            "cache": settings.paths.cache,
            "logs": settings.paths.logs,
        }.items()
    }
    session: dict[str, object] = {"state": "missing"}
    try:
        session_size = settings.paths.session.stat().st_size
        session = {
            "state": "present",
            "bytes": session_size,
            "valid_json": isinstance(json.loads(settings.paths.session.read_text(encoding="utf-8")), dict),
        }
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        pass
    disk = shutil.disk_usage(settings.paths.repository)
    payload = {
        "ok": not _port_open(7852),
        "version": __version__,
        "commit": _git_commit(settings.paths.repository),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "roots": roots,
        "ports": {
            name: {
                "port": port,
                "listening": _port_open(port, settings.security.bind_host if name == "api" else "127.0.0.1"),
            }
            for name, port in ports.items()
        },
        "tts_owner": owner,
        "binaries": {name: bool(shutil.which(name)) for name in ("ffmpeg", "pdftotext", "pdfinfo", "tesseract", "git")},
        "session": session,
        "disk_free_bytes": disk.free,
        "warnings": ["7854 belongs to an external system and is informational only"],
    }
    _json(payload)
    return 0 if payload["ok"] else 1


def _run_script(settings: Settings, relative: str, timeout: float) -> int:
    script = settings.paths.repository / relative
    if not script.is_file():
        _json({"ok": False, "error": "script_missing", "path": str(script)})
        return 1
    return _run_visible(
        ["bash", str(script)],
        cwd=settings.paths.repository,
        env=_project_environment(settings.paths.repository, dict(os.environ)),
        timeout=timeout,
    )


def command_smoke(settings: Settings, _args: argparse.Namespace) -> int:
    if (settings.paths.repository / "pyproject.toml").is_file():
        return _run_script(settings, "scripts/smoke_fusion_reader_v2.sh", 120.0)
    try:
        payload = _request_json(f"{_base_url(settings)}/health/ready")
    except (OSError, ValueError) as exc:
        _json({"ok": False, "error": "reader_unavailable", "detail": type(exc).__name__})
        return 1
    _json(payload)
    return 0 if payload.get("reader_ready") else 1


def command_test(settings: Settings, _args: argparse.Namespace) -> int:
    runner = settings.paths.repository / "scripts/run_isolated_tests.py"
    if not runner.is_file() or not (settings.paths.repository / "tests").is_dir():
        _json({"ok": False, "error": "tests_require_source_checkout"})
        return 1
    return _run_visible([sys.executable, str(runner)], cwd=settings.paths.repository, timeout=600.0)


def command_logs(settings: Settings, args: argparse.Namespace) -> int:
    path = settings.paths.logs / "fusion_reader_v2_server.log"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        _json({"ok": False, "error": "log_file_missing", "path": str(path)})
        return 1
    print("\n".join(lines[-max(1, int(args.lines)) :]))
    return 0


def _cache(settings: Settings, *, create_root: bool) -> AudioCache:
    return AudioCache(
        settings.paths.cache,
        max_bytes=settings.limits.cache_max_bytes,
        max_age_days=settings.limits.cache_max_age_days,
        create_root=create_root,
    )


def command_cache_inspect(settings: Settings, _args: argparse.Namespace) -> int:
    _json(_cache(settings, create_root=False).inspect())
    return 0


def command_cache_prune(settings: Settings, args: argparse.Namespace) -> int:
    apply = bool(args.apply)
    _json(_cache(settings, create_root=apply).prune(apply=apply))
    return 0


def command_version(_settings: Settings, _args: argparse.Namespace) -> int:
    _json({"ok": True, "version": __version__})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fusionctl", description="Fusion Reader v2 local operations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    commands = {
        "start": command_start,
        "stop": command_stop,
        "restart": command_restart,
        "status": command_status,
        "doctor": command_doctor,
        "smoke": command_smoke,
        "test": command_test,
        "version": command_version,
    }
    for name, handler in commands.items():
        subparser = subparsers.add_parser(name)
        subparser.set_defaults(handler=handler)
        if name == "start":
            subparser.add_argument(
                "--no-engines", action="store_true", help="start only API/UI; use existing or unavailable providers"
            )
    probe = subparsers.add_parser("_probe-startup")
    probe.set_defaults(handler=probe_startup)
    logs = subparsers.add_parser("logs")
    logs.add_argument("--lines", type=int, default=100)
    logs.set_defaults(handler=command_logs)
    cache = subparsers.add_parser("cache")
    cache_commands = cache.add_subparsers(dest="cache_command", required=True)
    inspect = cache_commands.add_parser("inspect")
    inspect.set_defaults(handler=command_cache_inspect)
    prune = cache_commands.add_parser("prune")
    mode = prune.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    prune.set_defaults(handler=command_cache_prune)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repository = Path(__file__).resolve().parents[1]
        settings = create_settings(environ=_project_environment(repository, dict(os.environ)))
        return int(args.handler(settings, args))
    except (OSError, RuntimeError, ValueError, TimeoutExpired) as exc:
        _json({"ok": False, "error": type(exc).__name__, "detail": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
