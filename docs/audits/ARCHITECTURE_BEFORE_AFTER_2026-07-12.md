# Architecture before and after: 2026-07-12

Baseline: `19ccaa9bc36b09c008af8245586c82e262ee081a`.

## Responsibility map

| Concern | Before | After |
|---|---|---|
| Composition | constructors spread across scripts and module globals | `config.py` plus `composition.py` create settings, providers, app and HTTP server |
| Public API | package and `service.py` implementation coupled | stable package/service facades delegate to owned services |
| HTTP | 4,108-line Python server with embedded UI and global app | thin compatibility wrapper, instance `WebContext`, router/errors, static HTML/CSS/JS |
| Lifecycle | locks, futures, threads and shutdown mixed into facade | `BackgroundLifecycleService` owns close state, leases, queues and idempotent shutdown |
| Persistence | direct JSON reads/writes with inconsistent recovery | versioned `AtomicJSONStore`, migrations, backup, fsync, replace and corruption recovery |
| Jobs | independent unbounded dictionaries | typed `JobRegistry` with states, TTL, maximum size and pruning |
| Audio export | facade-owned worker details | `AudioExportService` owns snapshots, jobs, cancellation, cleanup and downloads |
| Notes | facade directly coordinates store calls | `NotesService` owns note use cases while preserving the old methods |
| Configuration | environment reads and local paths distributed | typed `Settings`; all active environment access passes through `config.py` |
| Logging | ad hoc output | rotating structured Python logging with request/job/provider/thread context |
| Legacy | active, lab, desktop and temporary tools mixed in `scripts/` | explicit `legacy/` and `tests/manual/`; active package cannot import legacy |

`FusionReaderV2` remains a substantial compatibility coordinator because its
public surface is broad, but it no longer owns every responsibility: lifecycle,
persistence, notes, audio export, job retention and composition are separate,
independently tested objects. Further extraction should be contract-driven, not
line-count driven.

## Dependency graph

```text
entrypoints / fusionctl
        |
        v
config.Settings ---> composition factories ---> providers
                            |
                            v
                    FusionReaderV2 facade
                       |    |    |
            lifecycle -+    |    +- notes service
            persistence ----+    +- audio export service
                            |
                            v
                 reader / conversation / tools
                            |
                            v
              WebContext -> router -> JSON/static responses
```

Dependencies point inward through constructors. Importing the compatibility
server no longer creates an app, reads the real session, starts threads, probes
ports or creates roots.

## Lifecycle and jobs

The lifecycle has `open`, `closing` and `closed` ownership states. Interactive
TTS leases, prepare/export workers, prefetch futures and executors are captured
before shutdown. Shutdown is idempotent, bounded by timeout and retryable after
a surfaced error. Two app instances have independent lifecycle state.

Import, PDF and export work expose the common `queued`, `running`, `canceling`,
`done`, `cancelled` and `error` states. Registries reject unsafe growth, prune
expired terminal entries and retain referenced threads until cleanup.

## Persistence and cache

Owned JSON state has schema versions, explicit legacy transforms, size limits,
per-instance locks, atomic temporary writes, flush/fsync, `os.replace`, migration
backup and preserved corrupt inputs. Recovery degrades to a clean state with a
warning instead of preventing startup.

Audio cache keys include text, voice, language and cache/provider version. WAV
publication is atomic and validated; prune is constrained to the injected cache
root and supports inspect, dry-run and apply. Exports and user Downloads are not
cache and cannot be pruned by this path.

## Web and security

The stdlib server remains intentionally lightweight. `WebContext` owns the app,
jobs and worker threads. The router separates matching from handling; errors use
stable codes and request IDs. Uploads are limited and spooled, filenames and
download paths are bounded, security headers are emitted, CORS is absent by
default, and remote mutation requires explicit opt-in plus token.

`/health/live` reports process liveness. `/health/ready` reports basic reader
readiness and provider degradations without treating optional TTS/STT/Ollama or
SearXNG absence as process death.

## Compatibility and rollback

Package imports, `fusion_reader_v2.service.FusionReaderV2`, public signatures,
HTTP routes and JSON keys are frozen by contract tests. The old server path is a
thin wrapper. Persisted legacy state is migrated with backup.

Rollback is commit-local: revert the affected atomic commit. Persistent schema
version remains `1`, so no destructive data downgrade is required. Never roll
back by deleting runtime, Downloads or migration backups.
