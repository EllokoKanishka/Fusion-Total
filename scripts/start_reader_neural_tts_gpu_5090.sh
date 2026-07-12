#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Machine-local external defaults remain overrideable via env vars.
ALLTALK_DIR="${DIRECT_CHAT_ALLTALK_DIR:-${HOME}/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts}"
GPU_ENV="${FUSION_READER_GPU_ENV:-${HOME}/fusion_reader_envs/alltalk_gpu_5090_py311}"
ALLTALK_HOST="${FUSION_READER_GPU_TTS_HOST:-127.0.0.1}"
ALLTALK_PORT="${FUSION_READER_GPU_TTS_PORT:-7853}"
OWNER_FILE="${FUSION_READER_TTS_OWNER_FILE:-$ROOT/runtime/fusion_reader_v2/tts_owner.json}"
OWNER_NAME="fusion_reader_v2"

source "$ROOT/scripts/fusion_reader_gpu_guard.sh"
fusion_reader_refuse_when_gpu_conflict "Fusion Reader v2 AllTalk GPU TTS"

owner_file_matches() {
  [[ -f "$OWNER_FILE" ]] || return 1
  grep -q '"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"' "$OWNER_FILE" || return 1
  grep -q "\"port\"[[:space:]]*:[[:space:]]*$ALLTALK_PORT" "$OWNER_FILE" || return 1

  local owner_pid
  owner_pid="$(sed -n 's/.*"owner_pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$OWNER_FILE" | head -1)"
  if [[ -z "$owner_pid" ]]; then
    return 1
  fi
  [[ -r "/proc/$owner_pid/cmdline" ]] || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "tts_server:app" || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "--port $ALLTALK_PORT" || return 1
}

port_is_listening() {
  ss -ltn 2>/dev/null | grep -q "[.:]${ALLTALK_PORT}[[:space:]]"
}

if curl -fsS --max-time 2 "http://$ALLTALK_HOST:$ALLTALK_PORT/api/ready" >/dev/null 2>&1; then
  if owner_file_matches; then
    echo "AllTalk GPU Fusion already running on http://$ALLTALK_HOST:$ALLTALK_PORT"
    exit 0
  fi
  echo "Refusing to claim http://$ALLTALK_HOST:$ALLTALK_PORT: service is alive but is not owned by $OWNER_NAME." >&2
  echo "Owner file expected: $OWNER_FILE" >&2
  exit 2
fi

if port_is_listening; then
  echo "Refusing to start AllTalk GPU Fusion: port $ALLTALK_PORT is already in use by another service." >&2
  exit 2
fi

if [[ ! -x "$GPU_ENV/bin/python" ]]; then
  echo "GPU env not found: $GPU_ENV" >&2
  echo "Run ./scripts/bootstrap_alltalk_gpu_5090.sh first." >&2
  exit 1
fi

if [[ ! -d "$ALLTALK_DIR" ]]; then
  echo "AllTalk directory not found: $ALLTALK_DIR" >&2
  exit 1
fi

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export PYTHONPATH="$ROOT/scripts/gpu_compat${PYTHONPATH:+:$PYTHONPATH}"
export FUSION_READER_ALLOW_TORCH_PICKLE_LOAD=1
SITE_PACKAGES="$GPU_ENV/lib/python3.11/site-packages"
GPU_LIB_PATH=""
for libdir in "$SITE_PACKAGES"/nvidia/*/lib; do
  if [[ -d "$libdir" ]]; then
    GPU_LIB_PATH="${GPU_LIB_PATH:+$GPU_LIB_PATH:}$libdir"
  fi
done
if [[ -n "$GPU_LIB_PATH" ]]; then
  export LD_LIBRARY_PATH="$GPU_LIB_PATH${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
unset DIRECT_CHAT_ALLTALK_FORCE_CPU
mkdir -p "$(dirname "$OWNER_FILE")"
cat >"$OWNER_FILE" <<EOF
{
  "owner": "$OWNER_NAME",
  "role": "gpu_tts",
  "host": "$ALLTALK_HOST",
  "port": $ALLTALK_PORT,
  "owner_pid": $$,
  "python": "$GPU_ENV/bin/python",
  "alltalk_dir": "$ALLTALK_DIR"
}
EOF

cd "$ALLTALK_DIR"
exec "$GPU_ENV/bin/python" -m uvicorn tts_server:app --host "$ALLTALK_HOST" --port "$ALLTALK_PORT" --workers 1 --proxy-headers
