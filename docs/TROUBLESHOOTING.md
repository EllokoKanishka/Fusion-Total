# Troubleshooting

Start with read-only diagnostics:

```bash
fusionctl status
fusionctl doctor
fusionctl logs --lines 100
./scripts/verify_voice_port_isolation.sh
./scripts/smoke_fusion_reader_v2.sh
```

## Reader does not start

- check whether `8010` belongs to an existing Fusion process;
- inspect the PID metadata before stopping anything;
- run `bash -n scripts/start_fusion_reader_v2.sh`;
- inspect the configured runtime/log roots.

## Voice unavailable

- `7853` must answer and have valid owner metadata;
- `7851` is the only CPU fallback;
- never redirect Fusion to `7852` or `7854`;
- cached reads may still work while the provider is down.

## Dialogue unavailable

Check `/health/ready` and `/api/status`. STT, Ollama and research can degrade
without making basic reading dead. In `auto`, STT may fall back to CLI and
research may fall back from SearXNG to OpenClaw `fusion-research`.

## State recovery warning

Corrupt JSON is preserved as `.corrupt.<timestamp>` and defaults are loaded.
Legacy unversioned state is backed up before migration. Do not delete those
artifacts until their contents are reviewed.

## Jobs stuck during shutdown

Retry `fusionctl stop`; shutdown is idempotent and validates PID ownership. If
it still times out, inspect status/logs rather than killing unrelated processes.
