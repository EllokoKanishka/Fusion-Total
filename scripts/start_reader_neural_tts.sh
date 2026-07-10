#!/usr/bin/env bash
set -euo pipefail

# Machine-local legacy defaults remain overrideable via env vars.
ALLTALK_DIR="${DIRECT_CHAT_ALLTALK_DIR:-/home/lucy-ubuntu/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts}"
ALLTALK_PYTHON="${DIRECT_CHAT_ALLTALK_PYTHON:-/home/lucy-ubuntu/ebook2audiobook/python_env/bin/python}"
ALLTALK_HOST="${DIRECT_CHAT_ALLTALK_HOST:-127.0.0.1}"
ALLTALK_PORT="${DIRECT_CHAT_ALLTALK_PORT:-7851}"
ALLTALK_FORCE_CPU="${DIRECT_CHAT_ALLTALK_FORCE_CPU:-1}"

if [[ ! -d "$ALLTALK_DIR" ]]; then
  echo "AllTalk directory not found: $ALLTALK_DIR" >&2
  exit 1
fi

if [[ ! -x "$ALLTALK_PYTHON" ]]; then
  echo "AllTalk Python not executable: $ALLTALK_PYTHON" >&2
  exit 1
fi

cd "$ALLTALK_DIR"

if [[ "$ALLTALK_FORCE_CPU" == "1" ]]; then
  export CUDA_VISIBLE_DEVICES=""
fi

exec "$ALLTALK_PYTHON" -m uvicorn tts_server:app --host "$ALLTALK_HOST" --port "$ALLTALK_PORT" --workers 1 --proxy-headers
