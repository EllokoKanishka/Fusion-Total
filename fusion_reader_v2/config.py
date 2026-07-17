from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when local runtime configuration violates a safety boundary."""


def environment_value(name: str, default: str | None = None) -> str | None:
    """Read a compatibility environment value through the configuration boundary."""
    return os.environ.get(name, default)


def environment_has(name: str) -> bool:
    return name in os.environ


def environment_copy() -> dict[str, str]:
    return dict(os.environ)


def _truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _integer(env: Mapping[str, str], name: str, default: int, *, minimum: int = 0) -> int:
    raw = env.get(name)
    try:
        value = default if raw in {None, ""} else int(str(raw))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _floating(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = env.get(name)
    try:
        value = default if raw in {None, ""} else float(str(raw))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be numeric") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _default_downloads(home: Path) -> Path:
    for name in ("Descargas", "Downloads"):
        candidate = home / name
        if candidate.is_dir():
            return candidate.resolve(strict=False)
    return (home / "Descargas").resolve(strict=False)


def is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def ensure_within(path: Path | str, *roots: Path | str) -> Path:
    candidate = _path(path)
    allowed = tuple(_path(root) for root in roots)
    if not allowed or not any(candidate == root or root in candidate.parents for root in allowed):
        raise ConfigurationError(f"path is outside the allowed roots: {candidate}")
    return candidate


@dataclass(frozen=True)
class PathSettings:
    repository: Path
    runtime: Path
    library: Path
    downloads: Path
    cache: Path
    notes: Path
    metrics: Path
    logs: Path
    session: Path

    def validate(self) -> None:
        ensure_within(self.cache, self.runtime)
        ensure_within(self.notes, self.runtime)
        ensure_within(self.metrics, self.runtime)
        ensure_within(self.logs, self.runtime)
        ensure_within(self.session, self.runtime)


@dataclass(frozen=True)
class PortSettings:
    api: int = 8010
    tts_gpu: int = 7853
    tts_cpu: int = 7851
    stt: int = 8021
    ollama: int = 11434
    searxng: int = 8080

    def validate(self) -> None:
        if self.tts_gpu in {7852, 7854}:
            raise ConfigurationError(f"Fusion cannot use reserved TTS port {self.tts_gpu}")
        if self.tts_gpu != 7853:
            raise ConfigurationError("Fusion GPU TTS must use port 7853")
        if self.tts_cpu != 7851:
            raise ConfigurationError("Fusion CPU TTS fallback must use port 7851")


@dataclass(frozen=True)
class ProviderSettings:
    tts_url: str
    tts_owner_file: Path
    voice: str
    language: str
    tts_timeout_seconds: float
    stt_provider: str
    stt_url: str
    stt_timeout_seconds: float
    stt_command: str
    stt_model: str
    stt_threads: int
    ollama_url: str
    chat_model: str
    research_provider: str
    searxng_url: str
    searxng_timeout_seconds: float
    searxng_enabled: bool
    openclaw_command: str
    openclaw_agent: str
    openclaw_timeout_seconds: float
    openclaw_retries: int
    openclaw_enabled: bool

    def validate(self, ports: PortSettings) -> None:
        parsed = urlparse(self.tts_url)
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            port = parsed.port
            if port in {7852, 7854}:
                raise ConfigurationError(f"Fusion cannot use reserved TTS URL port {port}")
            if port not in {ports.tts_gpu, ports.tts_cpu}:
                raise ConfigurationError("Fusion TTS URL must target port 7853 or fallback 7851")
        if self.research_provider not in {"auto", "searxng", "openclaw", "none"}:
            raise ConfigurationError("invalid external research provider")
        if self.stt_provider not in {"auto", "server", "cli", "none"}:
            raise ConfigurationError("invalid STT provider")
        if self.openclaw_agent != "fusion-research":
            raise ConfigurationError("Fusion external research must use the fusion-research OpenClaw agent")


@dataclass(frozen=True)
class LimitSettings:
    upload_max_bytes: int = 128 * 1024 * 1024
    quick_text_max_chars: int = 2_000_000
    pdf_max_bytes: int = 500 * 1024 * 1024
    media_max_bytes: int = 2 * 1024 * 1024 * 1024
    media_timeout_seconds: float = 2 * 60 * 60
    job_ttl_seconds: int = 6 * 60 * 60
    job_max_items: int = 256
    cache_max_bytes: int = 8 * 1024 * 1024 * 1024
    cache_max_age_days: int = 30
    prefetch_ahead: int = 3
    prefetch_workers: int = 1
    prefetch_wait_seconds: float = 25.0


@dataclass(frozen=True)
class SecuritySettings:
    bind_host: str = "127.0.0.1"

    def validate(self) -> None:
        if is_loopback_host(self.bind_host):
            return
        raise ConfigurationError("remote mode is not supported; bind_host must be loopback")


@dataclass(frozen=True)
class Settings:
    paths: PathSettings
    ports: PortSettings
    providers: ProviderSettings
    limits: LimitSettings
    security: SecuritySettings

    def validate(self) -> "Settings":
        self.paths.validate()
        self.ports.validate()
        self.providers.validate(self.ports)
        self.security.validate()
        return self


def create_settings(
    *,
    environ: Mapping[str, str] | None = None,
    repository_root: Path | str | None = None,
) -> Settings:
    env = os.environ if environ is None else environ
    repository = _path(repository_root or Path(__file__).resolve().parents[1])
    home = _path(env.get("HOME") or Path.home())
    runtime = _path(
        env.get("FUSION_READER_RUNTIME_ROOT")
        or env.get("FUSION_READER_RUNTIME_DIR")
        or repository / "runtime" / "fusion_reader_v2"
    )
    library = _path(env.get("FUSION_READER_LIBRARY_ROOT") or repository / "library")
    downloads = _path(env.get("FUSION_READER_DOWNLOADS_ROOT") or _default_downloads(home))
    cache = _path(env.get("FUSION_READER_CACHE_ROOT") or runtime / "audio_cache")
    logs = _path(env.get("FUSION_READER_LOG_ROOT") or env.get("FUSION_READER_LOG_DIR") or runtime / "logs")
    ports = PortSettings(
        api=_integer(env, "FUSION_READER_V2_PORT", 8010, minimum=1),
        tts_gpu=_integer(env, "FUSION_READER_GPU_TTS_PORT", 7853, minimum=1),
        tts_cpu=_integer(env, "FUSION_READER_CPU_TTS_PORT", 7851, minimum=1),
        stt=_integer(env, "FUSION_READER_STT_PORT", 8021, minimum=1),
        ollama=_integer(env, "FUSION_READER_OLLAMA_PORT", 11434, minimum=1),
        searxng=8080,
    )
    settings = Settings(
        paths=PathSettings(
            repository=repository,
            runtime=runtime,
            library=library,
            downloads=downloads,
            cache=cache,
            notes=runtime / "notes",
            metrics=runtime / "voice_metrics.jsonl",
            logs=logs,
            session=runtime / "session_state.json",
        ),
        ports=ports,
        providers=ProviderSettings(
            tts_url=(env.get("FUSION_READER_ALLTALK_URL") or f"http://127.0.0.1:{ports.tts_gpu}").rstrip("/"),
            tts_owner_file=_path(env.get("FUSION_READER_TTS_OWNER_FILE") or runtime / "tts_owner.json"),
            voice=env.get("FUSION_READER_VOICE", "female_03.wav"),
            language=env.get("FUSION_READER_LANGUAGE", "es"),
            tts_timeout_seconds=_floating(env, "FUSION_READER_TTS_TIMEOUT", 120.0, minimum=0.1),
            stt_provider=env.get("FUSION_READER_STT_PROVIDER", "auto").strip().lower(),
            stt_url=(env.get("FUSION_READER_STT_URL") or f"http://127.0.0.1:{ports.stt}").rstrip("/"),
            stt_timeout_seconds=_floating(env, "FUSION_READER_STT_TIMEOUT", 120.0, minimum=0.1),
            stt_command=env.get("FUSION_READER_STT_COMMAND", "whisper"),
            stt_model=env.get("FUSION_READER_STT_MODEL", "small"),
            stt_threads=_integer(env, "FUSION_READER_STT_THREADS", 8, minimum=1),
            ollama_url=(env.get("FUSION_READER_OLLAMA_URL") or f"http://127.0.0.1:{ports.ollama}").rstrip("/"),
            chat_model=env.get("FUSION_READER_CHAT_MODEL", "qwen3:14b-q8_0"),
            research_provider=env.get("FUSION_READER_EXTERNAL_RESEARCH_PROVIDER", "auto").strip().lower(),
            searxng_url=(env.get("FUSION_READER_SEARXNG_URL") or f"http://127.0.0.1:{ports.searxng}").rstrip("/"),
            searxng_timeout_seconds=_floating(env, "FUSION_READER_SEARXNG_TIMEOUT", 12.0, minimum=0.1),
            searxng_enabled=_truthy(env.get("FUSION_READER_SEARXNG_ENABLED"), default=True),
            openclaw_command=env.get("FUSION_READER_OPENCLAW_BIN", str(home / ".openclaw" / "bin" / "openclaw")),
            openclaw_agent=env.get("FUSION_READER_OPENCLAW_AGENT", "fusion-research"),
            openclaw_timeout_seconds=_floating(env, "FUSION_READER_OPENCLAW_TIMEOUT", 90.0, minimum=0.1),
            openclaw_retries=_integer(env, "FUSION_READER_OPENCLAW_RETRIES", 2, minimum=1),
            openclaw_enabled=_truthy(env.get("FUSION_READER_OPENCLAW_ENABLED"), default=True),
        ),
        limits=LimitSettings(
            upload_max_bytes=_integer(env, "FUSION_READER_UPLOAD_MAX_BYTES", 128 * 1024 * 1024, minimum=1),
            quick_text_max_chars=_integer(env, "FUSION_READER_QUICK_TEXT_MAX_CHARS", 2_000_000, minimum=1),
            pdf_max_bytes=_integer(env, "FUSION_READER_PDF_MAX_BYTES", 500 * 1024 * 1024, minimum=1),
            media_max_bytes=_integer(
                env,
                "FUSION_READER_MEDIA_MAX_BYTES",
                2 * 1024 * 1024 * 1024,
                minimum=1,
            ),
            media_timeout_seconds=_floating(
                env,
                "FUSION_READER_MEDIA_TIMEOUT_SECONDS",
                2 * 60 * 60,
                minimum=1.0,
            ),
            job_ttl_seconds=_integer(env, "FUSION_READER_JOB_TTL_SECONDS", 6 * 60 * 60, minimum=1),
            job_max_items=_integer(env, "FUSION_READER_JOB_MAX_ITEMS", 256, minimum=1),
            cache_max_bytes=_integer(env, "FUSION_READER_CACHE_MAX_BYTES", 8 * 1024 * 1024 * 1024, minimum=1),
            cache_max_age_days=_integer(env, "FUSION_READER_CACHE_MAX_AGE_DAYS", 30, minimum=0),
            prefetch_ahead=_integer(env, "FUSION_READER_PREFETCH_AHEAD", 3),
            prefetch_workers=_integer(env, "FUSION_READER_PREFETCH_WORKERS", 1, minimum=1),
            prefetch_wait_seconds=_floating(env, "FUSION_READER_PREFETCH_WAIT_SECONDS", 25.0, minimum=0.1),
        ),
        security=SecuritySettings(
            bind_host=env.get("FUSION_READER_BIND_HOST", "127.0.0.1").strip(),
        ),
    )
    return settings.validate()
