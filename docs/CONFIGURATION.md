# Configuration

`fusion_reader_v2.config.create_settings()` is the only composition-time source
of paths, ports, limits, provider endpoints and HTTP security policy. Tests pass
an explicit environment mapping; runtime uses `os.environ`.

## Paths

| Variable | Default |
|---|---|
| `FUSION_READER_RUNTIME_ROOT` | `runtime/fusion_reader_v2` |
| `FUSION_READER_LIBRARY_ROOT` | `library` |
| `FUSION_READER_DOWNLOADS_ROOT` | `~/Descargas`, then `~/Downloads` |
| `FUSION_READER_CACHE_ROOT` | `<runtime>/audio_cache` |
| `FUSION_READER_LOG_ROOT` | `<runtime>/logs` |

`FUSION_READER_RUNTIME_DIR` and `FUSION_READER_LOG_DIR` remain compatibility
aliases. Cache, notes, metrics, logs and session state must remain inside the
configured runtime root.

## HTTP and providers

| Variable | Default |
|---|---|
| `FUSION_READER_BIND_HOST` | `127.0.0.1` |
| `FUSION_READER_V2_PORT` | `8010` |
| `FUSION_READER_ALLOW_REMOTE` | false |
| `FUSION_READER_API_TOKEN` | empty |
| `FUSION_READER_ALLTALK_URL` | `http://127.0.0.1:7853` |
| `FUSION_READER_STT_PROVIDER` | `auto` |
| `FUSION_READER_STT_URL` | `http://127.0.0.1:8021` |
| `FUSION_READER_OLLAMA_URL` | `http://127.0.0.1:11434` |
| `FUSION_READER_SEARXNG_URL` | `http://127.0.0.1:8080` |
| `FUSION_READER_EXTERNAL_RESEARCH_PROVIDER` | `auto` |
| `FUSION_READER_VOICE` | `female_03.wav` |
| `FUSION_READER_LANGUAGE` | `es` |

A non-loopback bind is rejected unless remote access is explicitly enabled and
a non-empty token is supplied. TTS URLs on `7852` or `7854` are rejected.

## Limits

Uploads, PDF uploads, cache bytes/age, job count/TTL and prefetch workers/ahead
are controlled by the `FUSION_READER_*_MAX_*`, `*_TTL_SECONDS` and
`FUSION_READER_PREFETCH_*` variables listed in `config.py`. Invalid integers,
negative values and unsafe ports fail fast with `ConfigurationError`.

Do not put tokens in tracked files. Use a local ignored `.env` or process
environment.
