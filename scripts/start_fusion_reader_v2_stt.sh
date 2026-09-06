#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Dedicated STT override wins; FUSION_READER_GPU_ENV remains a compatible fallback.
STT_ENV="${FUSION_READER_STT_ENV:-${FUSION_READER_GPU_ENV:-${HOME}/fusion_reader_envs/alltalk_gpu_5090_py311}}"
STT_DEVICE_WAS_SET="${FUSION_READER_STT_DEVICE+x}"
STT_MODEL_WAS_SET="${FUSION_READER_STT_MODEL+x}"
STT_BEAM_WAS_SET="${FUSION_READER_STT_BEAM_SIZE+x}"

source "$ROOT/scripts/fusion_reader_gpu_guard.sh"
if [[ "${FUSION_READER_STT_DEVICE:-cuda}" == "cuda" ]]; then
  fusion_reader_refuse_when_gpu_conflict "Fusion Reader v2 GPU STT"
fi

if [[ ! -x "$STT_ENV/bin/python" ]]; then
  echo "STT Python environment not found at resolved path: $STT_ENV" >&2
  echo "Set FUSION_READER_STT_ENV to a valid STT environment; FUSION_READER_GPU_ENV remains a compatible fallback." >&2
  echo "For the historical default only, ./scripts/bootstrap_alltalk_gpu_5090.sh is one setup option." >&2
  exit 1
fi

export FUSION_READER_STT_PORT="${FUSION_READER_STT_PORT:-8021}"
export FUSION_READER_STT_DEVICE="${FUSION_READER_STT_DEVICE:-cuda}"
export FUSION_READER_STT_COMPUTE_TYPE="${FUSION_READER_STT_COMPUTE_TYPE:-float16}"
export FUSION_READER_STT_LANGUAGE="${FUSION_READER_STT_LANGUAGE:-es}"

if [[ -z "$STT_DEVICE_WAS_SET" ]] && fusion_reader_gpu_conflict_active \
  && [[ "${FUSION_READER_GAME_COEXISTENCE:-1}" != "0" ]]; then
  export FUSION_READER_STT_DEVICE=cpu
  export FUSION_READER_STT_COMPUTE_TYPE="${FUSION_READER_STT_COMPUTE_TYPE_WITH_GAME:-int8}"
fi

# Quality-first defaults on a dedicated GPU. CPU/game coexistence keeps the
# lighter model unless the user explicitly selected a model.
if [[ -z "$STT_MODEL_WAS_SET" ]]; then
  if [[ "$FUSION_READER_STT_DEVICE" == "cuda" ]]; then
    export FUSION_READER_STT_MODEL=large-v3-turbo
  else
    export FUSION_READER_STT_MODEL=small
  fi
fi

if [[ -z "$STT_BEAM_WAS_SET" ]]; then
  if [[ "$FUSION_READER_STT_DEVICE" == "cuda" ]]; then
    export FUSION_READER_STT_BEAM_SIZE=5
  else
    export FUSION_READER_STT_BEAM_SIZE=2
  fi
fi

cd "$ROOT"

echo "Fusion Reader v2 STT: http://127.0.0.1:${FUSION_READER_STT_PORT}"
echo "Modelo STT: ${FUSION_READER_STT_MODEL} (${FUSION_READER_STT_DEVICE}/${FUSION_READER_STT_COMPUTE_TYPE}, beam=${FUSION_READER_STT_BEAM_SIZE})"
if [[ -n "${FUSION_READER_STT_HOTWORDS:-}" || -n "${FUSION_READER_STT_HOTWORDS_FILE:-}" || -n "${FUSION_READER_STT_INITIAL_PROMPT:-}" ]]; then
  echo "Context biasing STT: activo"
fi

exec "$STT_ENV/bin/python" scripts/fusion_reader_v2_stt_server.py
