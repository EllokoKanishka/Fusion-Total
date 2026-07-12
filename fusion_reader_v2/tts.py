from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


def _truthy(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _configured_gpu_tts_port() -> int:
    try:
        return int(os.environ.get("FUSION_READER_GPU_TTS_PORT", "7853"))
    except ValueError:
        return 7853


def _configured_lucy_tts_port() -> int:
    try:
        return int(os.environ.get("LUCY_TTS_PORT", "7854"))
    except ValueError:
        return 7854


def _historic_unassigned_tts_port() -> int:
    return 7852


def _default_owner_file() -> Path:
    return Path(
        os.environ.get(
            "FUSION_READER_TTS_OWNER_FILE",
            str(Path(__file__).resolve().parents[1] / "runtime" / "fusion_reader_v2" / "tts_owner.json"),
        )
    )


def _configured_cpu_tts_port() -> int:
    try:
        return int(os.environ.get("FUSION_READER_CPU_TTS_PORT", os.environ.get("DIRECT_CHAT_ALLTALK_PORT", "7851")))
    except ValueError:
        return 7851


@dataclass(frozen=True)
class AudioArtifact:
    ok: bool
    path: Path | None = None
    provider: str = ""
    detail: str = ""
    cached: bool = False
    source_url: str = ""
    duration_ms: int = 0


class TTSProvider:
    name = "base"

    def health(self) -> dict:
        return {"ok": False, "provider": self.name, "detail": "not_implemented"}

    def voices(self) -> list[str]:
        return []

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        return AudioArtifact(False, provider=self.name, detail="not_implemented")


class NullTTSProvider(TTSProvider):
    name = "null"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def health(self) -> dict:
        return {"ok": True, "provider": self.name}

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        self.calls.append((text, voice, language))
        started = time.perf_counter()
        fd, name = tempfile.mkstemp(prefix="fusion_reader_v2_null_", suffix=".wav")
        os.close(fd)
        path = Path(name)
        path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
        return AudioArtifact(True, path=path, provider=self.name, duration_ms=int((time.perf_counter() - started) * 1000))


class AllTalkProvider(TTSProvider):
    name = "alltalk"

    def __init__(
        self,
        base_url: str = "",
        default_voice: str = "",
        timeout_seconds: float | None = None,
        owner_file: Path | str | None = None,
    ) -> None:
        default_url = f"http://127.0.0.1:{_configured_gpu_tts_port()}"
        configured_url = (base_url or os.environ.get("FUSION_READER_ALLTALK_URL") or default_url).rstrip("/")
        self.default_voice = default_voice or os.environ.get("FUSION_READER_VOICE", "female_03.wav")
        self.timeout_seconds = timeout_seconds or float(os.environ.get("FUSION_READER_TTS_TIMEOUT", "120"))
        self.max_input_chars = int(os.environ.get("FUSION_READER_TTS_MAX_INPUT_CHARS", "0"))
        self.require_owner = _truthy(os.environ.get("FUSION_READER_REQUIRE_TTS_OWNER"), default=True)
        self.owner_file = Path(owner_file) if owner_file is not None else _default_owner_file()
        self.base_url = self._preferred_base_url(configured_url)

    def _local_port(self, url: str | None = None) -> int | None:
        target = url or getattr(self, "base_url", "")
        parsed = urllib.parse.urlparse(target)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return None
        try:
            return int(parsed.port or (443 if parsed.scheme == "https" else 80))
        except ValueError:
            return None

    def _cmdline_for_pid(self, pid: int) -> str:
        cmdline_path = Path("/proc") / str(pid) / "cmdline"
        return cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")

    def _listening_pid(self, port: int) -> int | None:
        commands = (
            ["lsof", "-tiTCP:%s" % port, "-sTCP:LISTEN"],
            ["ss", "-ltnp"],
        )
        for command in commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=2.0, check=False)
            except Exception:
                continue
            output = (result.stdout or "").strip()
            if not output:
                continue
            if command[0] == "lsof":
                first = output.splitlines()[0].strip()
                if first.isdigit():
                    return int(first)
                continue
            match = re.search(rf":{port}\b.*pid=(\d+)", output)
            if match:
                return int(match.group(1))
        return None

    def _listener_matches_fusion_tts(self, pid: int, port: int) -> bool:
        try:
            cmdline = self._cmdline_for_pid(pid)
        except Exception:
            return False
        return "tts_server:app" in cmdline and f"--port {port}" in cmdline

    def _rewrite_owner_pid(self, data: dict, owner_pid: int) -> None:
        updated = dict(data)
        updated["owner_pid"] = int(owner_pid)
        temporary: Path | None = None
        try:
            self.owner_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.owner_file.parent,
                prefix=f".{self.owner_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(updated, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.owner_file)
            temporary = None
        except Exception:
            return
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except Exception:
                    pass

    def _reconcile_owner_pid(self, data: dict, port: int) -> int | None:
        listener_pid = self._listening_pid(port)
        if not listener_pid or not self._listener_matches_fusion_tts(listener_pid, port):
            return None
        self._rewrite_owner_pid(data, listener_pid)
        return listener_pid

    def _owner_guard(self, url: str | None = None) -> tuple[bool, str]:
        port = self._local_port(url)
        gpu_port = _configured_gpu_tts_port()
        if port == _configured_lucy_tts_port():
            return False, f"tts_foreign_doctora_lucy_port:{port}"
        if port == _historic_unassigned_tts_port():
            return False, f"tts_historic_unassigned_port:{port}"
        if not self.require_owner or port != gpu_port:
            return True, ""
        try:
            data = json.loads(self.owner_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False, f"tts_owner_missing:{self.owner_file}"
        except Exception as exc:
            return False, f"tts_owner_invalid:{exc}"
        if data.get("owner") != "fusion_reader_v2":
            return False, f"tts_owner_mismatch:{data.get('owner') or 'unknown'}"
        try:
            owner_port = int(data.get("port"))
        except (TypeError, ValueError):
            return False, "tts_owner_port_invalid"
        if owner_port != gpu_port:
            return False, f"tts_owner_port_mismatch:{owner_port}"
        try:
            owner_pid = int(data.get("owner_pid"))
        except (TypeError, ValueError):
            reconciled_pid = self._reconcile_owner_pid(data, gpu_port)
            if reconciled_pid:
                owner_pid = reconciled_pid
            else:
                return False, "tts_owner_pid_missing"
        try:
            cmdline = self._cmdline_for_pid(owner_pid)
        except FileNotFoundError:
            reconciled_pid = self._reconcile_owner_pid(data, gpu_port)
            if reconciled_pid:
                return True, ""
            return False, f"tts_owner_pid_stale:{owner_pid}"
        except Exception as exc:
            reconciled_pid = self._reconcile_owner_pid(data, gpu_port)
            if reconciled_pid:
                return True, ""
            return False, f"tts_owner_pid_unreadable:{exc}"
        if "tts_server:app" not in cmdline or f"--port {gpu_port}" not in cmdline:
            reconciled_pid = self._reconcile_owner_pid(data, gpu_port)
            if reconciled_pid:
                return True, ""
            return False, f"tts_owner_pid_mismatch:{owner_pid}"
        return True, ""

    def _gpu_service_ready(self) -> bool:
        gpu_url = f"http://127.0.0.1:{_configured_gpu_tts_port()}/api/ready"
        try:
            with urllib.request.urlopen(gpu_url, timeout=1.5) as resp:
                body = resp.read().decode("utf-8", errors="replace").strip().lower()
            return bool(body) and body in {"ready", "ok", "true"}
        except Exception:
            return False

    def _preferred_base_url(self, configured_url: str) -> str:
        configured = (configured_url or "").rstrip("/")
        gpu_url = f"http://127.0.0.1:{_configured_gpu_tts_port()}"
        cpu_url = f"http://127.0.0.1:{_configured_cpu_tts_port()}"
        if configured == gpu_url:
            return configured
        if configured and configured != cpu_url:
            return configured
        owner_ok, _ = self._owner_guard(gpu_url)
        if owner_ok and self._gpu_service_ready():
            return gpu_url
        return configured or gpu_url

    def _prepare_text(self, text: str) -> str:
        prepared = unicodedata.normalize("NFC", str(text or ""))
        prepared = prepared.replace("\ufeff", " ").replace("\u00ad", "")
        prepared = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", prepared)
        prepared = re.sub(r"\[Pagina\s+(\d+)\]", r"Página \1.", prepared, flags=re.IGNORECASE)
        prepared = re.sub(r"\s+", " ", prepared).strip()
        if self.max_input_chars <= 0 or len(prepared) <= self.max_input_chars:
            return prepared
        cut = prepared[: self.max_input_chars].rstrip()
        boundary = max(cut.rfind("."), cut.rfind(","), cut.rfind(";"), cut.rfind(":"))
        if boundary >= 80:
            return cut[: boundary + 1].strip()
        space = cut.rfind(" ")
        return cut[:space].strip() if space >= 80 else cut.strip()

    def _request_json(self, url: str, timeout: float = 5.0) -> dict:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"raw": raw.decode("utf-8", errors="replace")}

    def health(self) -> dict:
        url = f"{self.base_url}/api/ready"
        owner_ok, owner_detail = self._owner_guard()
        if not owner_ok:
            return {"ok": False, "provider": self.name, "url": url, "detail": owner_detail}
        try:
            with urllib.request.urlopen(url, timeout=3.0) as resp:
                body = resp.read().decode("utf-8", errors="replace").strip()
            return {"ok": True, "provider": self.name, "url": url, "detail": body}
        except (TimeoutError, socket.timeout):
            return {"ok": True, "provider": self.name, "url": url, "detail": "busy"}
        except Exception as e:
            return {"ok": False, "provider": self.name, "url": url, "detail": str(e)}

    def voices(self) -> list[str]:
        owner_ok, _owner_detail = self._owner_guard()
        if not owner_ok:
            return []
        try:
            data = self._request_json(f"{self.base_url}/api/voices", timeout=10.0)
        except Exception:
            return []
        voices = data.get("voices", []) if isinstance(data, dict) else data
        return [str(v) for v in voices if str(v).strip()] if isinstance(voices, list) else []

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        started = time.perf_counter()
        owner_ok, owner_detail = self._owner_guard()
        if not owner_ok:
            return AudioArtifact(False, provider=self.name, detail=owner_detail)
        voice = voice or self.default_voice
        prepared_text = self._prepare_text(text)
        if not prepared_text:
            return AudioArtifact(False, provider=self.name, detail="empty_tts_text")
        payload = {
            "text_input": prepared_text,
            "text_filtering": "standard",
            "character_voice_gen": voice,
            "narrator_enabled": "false",
            "narrator_voice_gen": voice,
            "text_not_inside": "character",
            "language": language or "es",
            "output_file_name": f"fusion_reader_v2_{int(time.time())}",
            "output_file_timestamp": "true",
            "autoplay": "false",
            "autoplay_volume": "0.8",
        }
        body = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/tts-generate",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            audio_ref = data.get("output_file_url") or data.get("audio_url") or data.get("file_url")
            if not audio_ref:
                return AudioArtifact(False, provider=self.name, detail="no_audio_url")
            audio_url = self._audio_url(str(audio_ref))
            with urllib.request.urlopen(audio_url, timeout=self.timeout_seconds) as audio_resp:
                audio = audio_resp.read()
            fd, name = tempfile.mkstemp(prefix="fusion_reader_v2_alltalk_", suffix=".wav")
            os.close(fd)
            path = Path(name)
            path.write_bytes(audio)
            return AudioArtifact(True, path=path, provider=self.name, source_url=audio_url, duration_ms=int((time.perf_counter() - started) * 1000))
        except urllib.error.HTTPError as e:
            return AudioArtifact(False, provider=self.name, detail=f"http_{e.code}", duration_ms=int((time.perf_counter() - started) * 1000))
        except Exception as e:
            return AudioArtifact(False, provider=self.name, detail=str(e), duration_ms=int((time.perf_counter() - started) * 1000))

    def _audio_url(self, audio_ref: str) -> str:
        if not audio_ref.startswith("http"):
            return f"{self.base_url}/{audio_ref.lstrip('/')}"
        parsed_ref = urllib.parse.urlparse(audio_ref)
        parsed_base = urllib.parse.urlparse(self.base_url)
        if parsed_ref.hostname in {"127.0.0.1", "localhost"} and parsed_base.hostname in {"127.0.0.1", "localhost"}:
            return urllib.parse.urlunparse(
                (
                    parsed_base.scheme,
                    parsed_base.netloc,
                    parsed_ref.path,
                    parsed_ref.params,
                    parsed_ref.query,
                    parsed_ref.fragment,
                )
            )
        return audio_ref


class AudioCache:
    def __init__(
        self,
        root: Path | str = "runtime/fusion_reader_v2/audio_cache",
        *,
        max_bytes: int | None = None,
        max_age_days: int | None = None,
        version: str | None = None,
        create_root: bool = True,
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.version = version or os.environ.get("FUSION_READER_AUDIO_CACHE_VERSION", "natural-v2")
        self.max_bytes = max(1, int(max_bytes if max_bytes is not None else 8 * 1024 * 1024 * 1024))
        self.max_age_days = max(0, int(max_age_days if max_age_days is not None else 30))
        self._lock = threading.RLock()
        if create_root:
            self.root.mkdir(parents=True, exist_ok=True)

    def key(self, text: str, voice: str, language: str) -> str:
        digest = hashlib.sha256(f"{self.version}\0{language}\0{voice}\0{text}".encode("utf-8")).hexdigest()
        return digest[:32]

    def path_for(self, text: str, voice: str, language: str) -> Path:
        return self.root / f"{self.key(text, voice, language)}.wav"

    def get(self, text: str, voice: str, language: str) -> AudioArtifact | None:
        path = self.path_for(text, voice, language)
        with self._lock:
            if self._valid_wav(path):
                try:
                    os.utime(path, None)
                except OSError:
                    pass
                return AudioArtifact(True, path=path, provider="cache", cached=True)
        return None

    def put(self, text: str, voice: str, language: str, artifact: AudioArtifact) -> AudioArtifact:
        if not artifact.ok or not artifact.path or not self._valid_wav(artifact.path):
            return artifact
        target = self.path_for(text, voice, language)
        temporary: Path | None = None
        with self._lock:
            try:
                with artifact.path.open("rb") as source, tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=self.root,
                    prefix=f".{target.stem}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    shutil.copyfileobj(source, handle, length=1024 * 1024)
                    handle.flush()
                    os.fsync(handle.fileno())
                if not self._valid_wav(temporary):
                    return artifact
                os.replace(temporary, target)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return AudioArtifact(True, path=target, provider=artifact.provider, cached=False, source_url=artifact.source_url, duration_ms=artifact.duration_ms)

    def inspect(self, *, now: float | None = None) -> dict:
        if not self.root.exists():
            return {
                "ok": True,
                "root": str(self.root),
                "items": 0,
                "bytes": 0,
                "max_bytes": self.max_bytes,
                "max_age_days": self.max_age_days,
                "prunable_items": 0,
                "prunable_bytes": 0,
            }
        with self._lock:
            entries, selected = self._cache_plan(time.time() if now is None else float(now))
        return {
            "ok": True,
            "root": str(self.root),
            "items": len(entries),
            "bytes": sum(size for _path, size, _mtime in entries),
            "max_bytes": self.max_bytes,
            "max_age_days": self.max_age_days,
            "prunable_items": len(selected),
            "prunable_bytes": sum(size for _path, size, _mtime in selected),
        }

    def prune(self, *, apply: bool = False, now: float | None = None) -> dict:
        if not self.root.exists():
            return {
                "ok": True,
                "applied": bool(apply),
                "items": 0,
                "bytes": 0,
                "selected_items": 0,
                "selected_bytes": 0,
                "removed_items": 0,
                "removed_bytes": 0,
            }
        with self._lock:
            entries, selected = self._cache_plan(time.time() if now is None else float(now))
            removed_items = 0
            removed_bytes = 0
            if apply:
                for path, size, _mtime in selected:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        continue
                    removed_items += 1
                    removed_bytes += size
        return {
            "ok": True,
            "applied": bool(apply),
            "items": len(entries),
            "bytes": sum(size for _path, size, _mtime in entries),
            "selected_items": len(selected),
            "selected_bytes": sum(size for _path, size, _mtime in selected),
            "removed_items": removed_items,
            "removed_bytes": removed_bytes,
        }

    def _cache_plan(self, now: float) -> tuple[list[tuple[Path, int, float]], list[tuple[Path, int, float]]]:
        entries: list[tuple[Path, int, float]] = []
        for path in self.root.glob("*.wav"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((path, stat.st_size, stat.st_mtime))
        entries.sort(key=lambda item: (item[2], item[0].name))
        selected: list[tuple[Path, int, float]] = []
        selected_paths: set[Path] = set()
        if self.max_age_days > 0:
            oldest_allowed = now - self.max_age_days * 24 * 60 * 60
            for item in entries:
                if item[2] < oldest_allowed:
                    selected.append(item)
                    selected_paths.add(item[0])
        remaining_bytes = sum(size for path, size, _mtime in entries if path not in selected_paths)
        for item in entries:
            if remaining_bytes <= self.max_bytes:
                break
            if item[0] in selected_paths:
                continue
            selected.append(item)
            selected_paths.add(item[0])
            remaining_bytes -= item[1]
        return entries, selected

    @staticmethod
    def _valid_wav(path: Path) -> bool:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size < 12:
                return False
            with path.open("rb") as handle:
                header = handle.read(12)
        except OSError:
            return False
        return header[:4] == b"RIFF" and header[8:12] == b"WAVE"
