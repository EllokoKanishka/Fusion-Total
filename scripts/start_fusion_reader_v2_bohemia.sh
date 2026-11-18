#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$ROOT/scripts/lib/env_helper.sh"
load_env_safe

export FUSION_READER_BOHEMIA_CHAT_MODEL="${FUSION_READER_BOHEMIA_CHAT_MODEL:-huihui_ai/qwen3-abliterated:14b-v2-q8_0}"
echo "Modelo de Bohemia para el próximo arranque: $FUSION_READER_BOHEMIA_CHAT_MODEL"

exec "$ROOT/scripts/start_fusion_reader_v2.sh" "$@"
