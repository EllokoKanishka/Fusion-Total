#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/env_helper.sh"
load_env_safe

export FUSION_READER_CHAT_MODEL="${FUSION_READER_CHAT_MODEL:-qwen3:14b-q8_0}"
export FUSION_READER_CHAT_THINK="${FUSION_READER_CHAT_THINK:-1}"
export FUSION_READER_CHAT_NUM_PREDICT="${FUSION_READER_CHAT_NUM_PREDICT:-1536}"
export FUSION_READER_CHAT_TEMPERATURE="${FUSION_READER_CHAT_TEMPERATURE:-0.35}"
export FUSION_READER_REASONING_MODE="${FUSION_READER_REASONING_MODE:-thinking}"

echo "Fusion Reader v2 modo academico"
echo "Modelo chat: ${FUSION_READER_CHAT_MODEL}"
echo "Thinking: ${FUSION_READER_CHAT_THINK}"
echo "Num predict: ${FUSION_READER_CHAT_NUM_PREDICT}"
echo "Modo razonamiento: ${FUSION_READER_REASONING_MODE}"

exec "$ROOT/scripts/start_fusion_reader_v2.sh" "$@"
