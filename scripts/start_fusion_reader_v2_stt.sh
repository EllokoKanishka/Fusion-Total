#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Machine-local GPU env default remains overrideable via FUSION_READER_GPU_ENV.
GPU_ENV="${FUSION_READER_GPU_ENV:-/home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311}"
STT_DEVICE_WAS_SET="${FUSION_READER_STT_DEVICE+x}"

source "$ROOT/scripts/fusion_reader_gpu_guard.sh"
if [[ "${FUSION_READER_STT_DEVICE:-cuda}" == "cuda" ]]; then
  fusion_reader_refuse_when_gpu_conflict "Fusion Reader v2 GPU STT"
fi

if [[ ! -x "$GPU_ENV/bin/python" ]]; then
  echo "GPU env not found: $GPU_ENV" >&2
  echo "Run ./scripts/bootstrap_alltalk_gpu_5090.sh first." >&2
  exit 1
fi

export FUSION_READER_STT_PORT="${FUSION_READER_STT_PORT:-8021}"
export FUSION_READER_STT_MODEL="${FUSION_READER_STT_MODEL:-small}"
export FUSION_READER_STT_DEVICE="${FUSION_READER_STT_DEVICE:-cuda}"
export FUSION_READER_STT_COMPUTE_TYPE="${FUSION_READER_STT_COMPUTE_TYPE:-float16}"
export FUSION_READER_STT_LANGUAGE="${FUSION_READER_STT_LANGUAGE:-es}"

if [[ -z "$STT_DEVICE_WAS_SET" ]] && fusion_reader_gpu_conflict_active \
  && [[ "${FUSION_READER_GAME_COEXISTENCE:-1}" != "0" ]]; then
  export FUSION_READER_STT_DEVICE=cpu
  export FUSION_READER_STT_COMPUTE_TYPE="${FUSION_READER_STT_COMPUTE_TYPE_WITH_GAME:-int8}"
fi

cd "$ROOT"

echo "Fusion Reader v2 STT: http://127.0.0.1:${FUSION_READER_STT_PORT}"
echo "Modelo STT: ${FUSION_READER_STT_MODEL} (${FUSION_READER_STT_DEVICE}/${FUSION_READER_STT_COMPUTE_TYPE})"

exec "$GPU_ENV/bin/python" scripts/fusion_reader_v2_stt_server.py
