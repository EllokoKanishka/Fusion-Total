#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
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
from fusion_reader_v2.owned_subprocess import run_owned
from fusion_reader_v2.tts import AudioCache
from fusion_reader_v2.version import __version__


def _json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _request_json(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_status_payload")
    return payload


def _base_url(settings: Settings) -> str:
    host = (
        settings.security.bind_host if settings.security.bind_host in {"127.0.0.1", "localhost", "::1"} else "127.0.0.1"
    )
    return f"http://{host}:{settings.ports.api}"


def command_start(settings: Settings, _args: argparse.Namespace) -> int:
    script = settings.paths.repository / "scripts" / "start_fusion_reader_v2.sh"
    if not script.is_file():
        _json({"ok": False, "error": "start_script_missing", "path": str(script)})
        return 1
    environment = dict(os.environ)
    environment.setdefault("FUSION_READER_PYTHON", sys.executable)
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
    result = run_owned([str(script)], cwd=settings.paths.repository, env=environment, timeout=180.0, check=False)
    return int(result.returncode)


def _owned_server_pid(settings: Settings) -> tuple[int | None, str]:
    pid_path = settings.paths.runtime / "fusion_reader_v2.pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return None, "pid_file_missing"
    except (OSError, ValueError):
        return None, "pid_file_invalid"
    proc = Path("/proc") / str(pid)
    if not proc.exists():
        return None, "pid_stale"
    try:
        command = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except OSError:
        return None, "pid_unreadable"
    if "fusion_reader_v2_server" not in command:
        return None, "pid_owner_mismatch"
    return pid, "owned"


def command_stop(settings: Settings, _args: argparse.Namespace) -> int:
    pid_path = settings.paths.runtime / "fusion_reader_v2.pid"
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


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.25)
        return client.connect_ex(("127.0.0.1", int(port))) == 0


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
        "ollama": settings.ports.ollama,
        "searxng": settings.ports.searxng,
    }
    owner = {"state": "missing"}
    try:
        raw_owner = json.loads(settings.providers.tts_owner_file.read_text(encoding="utf-8"))
        owner = {
            "state": "valid"
            if raw_owner.get("owner") == "fusion_reader_v2" and int(raw_owner.get("port")) == 7853
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
    session = {"state": "missing"}
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
        "ports": {name: {"port": port, "listening": _port_open(port)} for name, port in ports.items()},
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
    return run_owned([str(script)], cwd=settings.paths.repository, timeout=timeout, check=False).returncode


def command_smoke(settings: Settings, _args: argparse.Namespace) -> int:
    return _run_script(settings, "scripts/smoke_fusion_reader_v2.sh", 120.0)


def command_test(settings: Settings, _args: argparse.Namespace) -> int:
    return run_owned(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        cwd=settings.paths.repository,
        timeout=600.0,
        check=False,
    ).returncode


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
        settings = create_settings()
        return int(args.handler(settings, args))
    except (OSError, RuntimeError, ValueError, TimeoutExpired) as exc:
        _json({"ok": False, "error": type(exc).__name__, "detail": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
