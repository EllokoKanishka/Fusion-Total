#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import resource
import shutil
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any


def normalized_tokens(text: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", str(text or "")).casefold()
    asciiish = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.findall(r"[\w]+", asciiish, flags=re.UNICODE)


def load_entities(path: Path | None) -> list[str]:
    if path is None:
        return []
    entities: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if value and not value.startswith("#"):
            entities.append(value)
    return entities


def count_phrase(tokens: list[str], phrase: str) -> int:
    needle = normalized_tokens(phrase)
    if not needle or len(needle) > len(tokens):
        return 0
    width = len(needle)
    return sum(1 for index in range(len(tokens) - width + 1) if tokens[index : index + width] == needle)


def entity_counts(text: str, entities: list[str]) -> dict[str, int]:
    tokens = normalized_tokens(text)
    return {entity: count_phrase(tokens, entity) for entity in entities}


def ffprobe_duration(path: Path) -> float | None:
    try:
        output = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=30,
        ).strip()
        return float(output)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        return None


def normalize_qwen_audio(source: Path, target: Path, *, timeout_seconds: float = 3600.0) -> float:
    """Normalize arbitrary audio/video containers to the stable Qwen input profile."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg_not_available")
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "flac",
                str(target),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("qwen_audio_conversion_timeout") from exc
    if proc.returncode != 0 or not target.exists() or target.stat().st_size <= 0:
        detail = " ".join((proc.stderr or proc.stdout or "").split())[-1000:]
        suffix = f":{detail}" if detail else ""
        raise RuntimeError(f"qwen_audio_conversion_failed{suffix}")
    return time.perf_counter() - started


def gibibytes(value: int | float) -> float:
    return round(float(value) / (1024**3), 3)


def process_max_rss_gib() -> float:
    # Linux ru_maxrss is KiB. PandaFusion's supported desktop target is Linux.
    return round(float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / (1024**2), 3)


def serialize_timestamps(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("text", "")
            start = item.get("start_time", item.get("start"))
            end = item.get("end_time", item.get("end"))
        else:
            text = getattr(item, "text", "")
            start = getattr(item, "start_time", getattr(item, "start", None))
            end = getattr(item, "end_time", getattr(item, "end", None))
        rows.append({"text": str(text), "start": start, "end": end})
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Isolated Qwen3-ASR + ForcedAligner benchmark for PandaFusion.",
    )
    parser.add_argument("audio", type=Path, help="Local audio/video file to transcribe.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for benchmark artifacts.")
    parser.add_argument("--context", default="", help="Optional request-scoped ASR context.")
    parser.add_argument("--language", default="Spanish", help="Qwen canonical language name or empty for auto.")
    parser.add_argument("--entities-file", type=Path, help="One expected entity/name per line.")
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B")
    parser.add_argument("--aligner", default="Qwen/Qwen3-ForcedAligner-0.6B")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-inference-batch-size", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=2048)
    parser.add_argument(
        "--ffmpeg-timeout-seconds",
        type=float,
        default=3600.0,
        help="Maximum time allowed for local container-to-FLAC normalization.",
    )
    parser.add_argument(
        "--no-timestamps",
        action="store_true",
        help="Disable ForcedAligner timestamps. The PandaFusion migration gate should normally keep timestamps enabled.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    audio = args.audio.expanduser().resolve()
    if not audio.is_file():
        raise SystemExit(f"audio_not_found:{audio}")
    if args.entities_file is not None and not args.entities_file.is_file():
        raise SystemExit(f"entities_file_not_found:{args.entities_file}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    entities = load_entities(args.entities_file)
    duration_seconds = ffprobe_duration(audio)

    try:
        import torch
        from qwen_asr import Qwen3ASRModel
    except ImportError as exc:
        raise SystemExit(
            "qwen_asr_not_installed: use an isolated venv and install requirements/qwen3-asr-benchmark.txt"
        ) from exc

    if not torch.cuda.is_available() and str(args.device).startswith("cuda"):
        raise SystemExit("cuda_not_available")

    dtype = torch.bfloat16
    cuda_index = 0
    if str(args.device).startswith("cuda") and ":" in str(args.device):
        cuda_index = int(str(args.device).split(":", 1)[1])
    if torch.cuda.is_available():
        torch.cuda.set_device(cuda_index)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(cuda_index)

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pandafusion-qwen3-asr-") as temp_dir:
        normalized_audio = Path(temp_dir) / "input.flac"
        preprocess_seconds = normalize_qwen_audio(
            audio,
            normalized_audio,
            timeout_seconds=max(1.0, float(args.ffmpeg_timeout_seconds)),
        )

        load_started = time.perf_counter()
        aligner_kwargs = {"dtype": dtype, "device_map": args.device}
        model = Qwen3ASRModel.from_pretrained(
            args.model,
            dtype=dtype,
            device_map=args.device,
            max_inference_batch_size=args.max_inference_batch_size,
            max_new_tokens=args.max_new_tokens,
            forced_aligner=None if args.no_timestamps else args.aligner,
            forced_aligner_kwargs=None if args.no_timestamps else aligner_kwargs,
        )
        load_seconds = time.perf_counter() - load_started

        load_peak_allocated = None
        load_peak_reserved = None
        if torch.cuda.is_available():
            torch.cuda.synchronize(cuda_index)
            load_peak_allocated = torch.cuda.max_memory_allocated(cuda_index)
            load_peak_reserved = torch.cuda.max_memory_reserved(cuda_index)
            torch.cuda.reset_peak_memory_stats(cuda_index)

        inference_started = time.perf_counter()
        results = model.transcribe(
            audio=str(normalized_audio),
            context=args.context,
            language=args.language.strip() or None,
            return_time_stamps=not args.no_timestamps,
        )
        if torch.cuda.is_available():
            torch.cuda.synchronize(cuda_index)
        inference_seconds = time.perf_counter() - inference_started
        total_seconds = time.perf_counter() - started

    if not results:
        raise SystemExit("qwen_asr_empty_result")
    result = results[0]
    text = str(result.text or "").strip()
    timestamps = serialize_timestamps(result.time_stamps)
    counts = entity_counts(text, entities)

    inference_peak_allocated = None
    inference_peak_reserved = None
    gpu_name = None
    torch_cuda = getattr(torch.version, "cuda", None)
    if torch.cuda.is_available():
        inference_peak_allocated = torch.cuda.max_memory_allocated(cuda_index)
        inference_peak_reserved = torch.cuda.max_memory_reserved(cuda_index)
        gpu_name = torch.cuda.get_device_name(cuda_index)

    transcript_path = args.output_dir / "qwen3_asr_transcript.txt"
    timestamps_path = args.output_dir / "qwen3_asr_timestamps.json"
    report_path = args.output_dir / "qwen3_asr_report.json"
    transcript_path.write_text(text + "\n", encoding="utf-8")
    timestamps_path.write_text(json.dumps(timestamps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "engine": "qwen3_asr",
        "model": args.model,
        "aligner": None if args.no_timestamps else args.aligner,
        "qwen_asr_version": importlib.metadata.version("qwen-asr"),
        "torch_version": torch.__version__,
        "torch_cuda": torch_cuda,
        "device": args.device,
        "gpu_name": gpu_name,
        "dtype": "bfloat16",
        "audio": str(audio),
        "audio_duration_seconds": duration_seconds,
        "normalized_input": {
            "container": "flac",
            "sample_rate_hz": 16000,
            "channels": 1,
        },
        "language_requested": args.language.strip() or None,
        "language_detected": str(result.language or ""),
        "context": args.context,
        "timestamps_enabled": not args.no_timestamps,
        "timestamp_items": len(timestamps),
        "preprocess_seconds": round(preprocess_seconds, 3),
        "load_seconds": round(load_seconds, 3),
        "inference_seconds": round(inference_seconds, 3),
        "total_seconds": round(total_seconds, 3),
        "real_time_factor": (
            round(inference_seconds / duration_seconds, 5) if duration_seconds and duration_seconds > 0 else None
        ),
        "speed_x_realtime": (
            round(duration_seconds / inference_seconds, 3) if duration_seconds and inference_seconds > 0 else None
        ),
        "load_peak_gpu_allocated_gib": gibibytes(load_peak_allocated) if load_peak_allocated is not None else None,
        "load_peak_gpu_reserved_gib": gibibytes(load_peak_reserved) if load_peak_reserved is not None else None,
        "inference_peak_gpu_allocated_gib": (
            gibibytes(inference_peak_allocated) if inference_peak_allocated is not None else None
        ),
        "inference_peak_gpu_reserved_gib": (
            gibibytes(inference_peak_reserved) if inference_peak_reserved is not None else None
        ),
        "process_max_rss_gib": process_max_rss_gib(),
        "character_count": len(text),
        "word_count": len(normalized_tokens(text)),
        "entity_counts": counts,
        "entity_coverage": (
            round(sum(1 for count in counts.values() if count > 0) / len(counts), 4) if counts else None
        ),
        "artifacts": {
            "transcript": str(transcript_path),
            "timestamps": str(timestamps_path),
            "report": str(report_path),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
