#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from fusion_reader_v2 import (
    AudioCache,
    ConversationCore,
    FusionReaderV2,
    NullChatProvider,
    NullSTTProvider,
    NullTTSProvider,
    ReaderNotesStore,
    VoiceMetricsStore,
)


def timed(samples: int, operation) -> list[float]:
    durations = []
    for _ in range(samples):
        started = time.perf_counter()
        operation()
        durations.append((time.perf_counter() - started) * 1000)
    return durations


def summarize(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(ordered), 3),
    }


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="fusion_benchmark_") as temp:
        root = Path(temp)
        app = FusionReaderV2(
            tts=NullTTSProvider(),
            stt=NullSTTProvider(),
            conversation=ConversationCore(NullChatProvider()),
            cache=AudioCache(root / "cache", max_bytes=32 * 1024 * 1024),
            metrics=VoiceMetricsStore(root / "metrics.jsonl"),
            notes=ReaderNotesStore(root / "notes"),
            session_state_path=root / "session.json",
            audio_export_root=root / "downloads",
        )
        text = "\n\n".join(f"Bloque {index}. Lectura sintética estable." for index in range(1, 40))
        try:
            load = timed(25, lambda: app.load_text("bench", "Benchmark", text, prefetch=False))
            status = timed(1000, app.status)
            navigation = timed(500, app.next)
            app.jump(1)
            app.read_current(play=False)
            cached_read = timed(100, lambda: app.read_current(play=False))
            cache_lookup = timed(1000, lambda: app.cache.get("Bloque 1", app.voice.voice, app.voice.language))
            return {
                "kind": "synthetic",
                "status": summarize(status),
                "load_text": summarize(load),
                "navigation": summarize(navigation),
                "cached_read": summarize(cached_read),
                "cache_lookup": summarize(cache_lookup),
                "bounded": {
                    "cache": app.cache.inspect(),
                    "audio_jobs": {
                        "total": len(app._audio_export_service.registry),
                        "max_items": app._audio_export_service.registry.max_items,
                    },
                },
            }
        finally:
            app.shutdown_background_work(timeout=10.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark sintético reproducible de Fusion Reader v2")
    parser.add_argument("--check", action="store_true", help="falla sólo ante crecimiento o latencia patológica")
    args = parser.parse_args()
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.check:
        if result["status"]["max_ms"] > 1000 or result["navigation"]["max_ms"] > 1000:
            raise SystemExit("synthetic benchmark detected pathological latency")
        if result["bounded"]["audio_jobs"]["total"] > result["bounded"]["audio_jobs"]["max_items"]:
            raise SystemExit("audio job registry exceeded its configured bound")


if __name__ == "__main__":
    main()
