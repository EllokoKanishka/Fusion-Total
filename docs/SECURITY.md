# Security

## Default boundary

The HTTP server binds to loopback. Remote mode is postponed and every
non-loopback bind is rejected, including when legacy remote variables are set.
Do not expose the service directly to the Internet.

## Local data

Fusion may store session state, notes, voice metrics, logs and generated audio
under `FUSION_READER_RUNTIME_ROOT`; imports under the library root; and explicit
exports under the downloads root. It has no telemetry. Document content is not
logged by default and is not sent outside the machine except when the user
explicitly requests external research under the documented provider contract.

Cleanup is explicit:

```bash
fusionctl cache inspect
fusionctl cache prune --dry-run
fusionctl cache prune --apply
```

To delete session state or notes, stop Fusion and remove only the configured
temporary/runtime files after reviewing their paths. Never commit `runtime/`,
`.env`, tokens, documents, generated WAV/DOCX files or owner metadata.

## Defensive controls

- canonical path checks and symlink rejection for downloads/cache;
- streaming uploads to private temporary files with byte limits;
- ZIP/document expansion guards;
- bounded JSON/base64 bodies and bounded job registries;
- atomic state/cache writes and corruption quarantine;
- subprocess argument arrays and timeouts at external boundaries;
- stable error envelopes without secrets or document bodies;
- no mutable global application instance at import time;
- deterministic shutdown of owned threads and futures.

Security CI runs `pip-audit`, secret-pattern scanning, CodeQL and the path,
configuration, persistence, cache and job tests.
