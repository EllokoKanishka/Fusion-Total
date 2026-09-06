#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${QWEN3_ASR_BENCH_PYTHON:-python3}"
VENV_DIR="${QWEN3_ASR_BENCH_VENV:-$ROOT/runtime/qwen3_asr_benchmark/venv}"

mkdir -p "$(dirname "$VENV_DIR")"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$ROOT/requirements/qwen3-asr-benchmark.txt"

cat <<EOF
Qwen3-ASR benchmark environment ready:
  $VENV_DIR

This environment is isolated from PandaFusion's production venv.
Run the benchmark with:
  $VENV_DIR/bin/python $ROOT/scripts/benchmark_qwen3_asr.py --help
EOF
