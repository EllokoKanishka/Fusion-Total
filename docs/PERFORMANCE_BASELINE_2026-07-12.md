# Performance baseline: 2026-07-12

Synthetic blocking checks use `python -m scripts.benchmark_synthetic_reader
--check` with temporary storage and null providers. They measure status, text
load, navigation, cached reads and cache lookup, while asserting bounded cache
and job registries. CI blocks only pathological one-second in-process latency
or unbounded growth, not hardware-sensitive microbenchmarks.

Real measurements are informative and must record hardware/provider context:
startup, first uncached/cached TTS, first block, prepare, export, PDF import,
PDF-to-DOCX, STT and dialogue. GPU, model and microphone results are never
inferred from synthetic providers.

The final local synthetic values and command outcome are recorded in
`CONSOLIDATION_FINAL_2026-07-12.md` after final verification.

## Synthetic result

Environment: Python 3.12.3, temporary roots, null TTS/STT/chat providers, no
network, GPU, microphone or real document content.

| Operation | Median | p95 | Maximum |
|---|---:|---:|---:|
| status | 2.541 ms | 2.828 ms | 3.732 ms |
| load text | 11.095 ms | 12.739 ms | 24.787 ms |
| navigation | 12.451 ms | 15.349 ms | 16.960 ms |
| cached read | 5.554 ms | 5.750 ms | 7.084 ms |
| cache lookup | 0.009 ms | 0.010 ms | 0.042 ms |

The final state retained one 12-byte synthetic cache item inside a 32 MiB
bound and zero audio jobs inside a 256-item registry. The blocking `--check`
uses only a one-second pathological-latency ceiling and boundedness assertions.
Real-provider timings remain intentionally unclaimed because TTS/STT/GPU and
microphone services were not started for this benchmark.
