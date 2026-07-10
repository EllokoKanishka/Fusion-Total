#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Machine-local external defaults remain overrideable via env vars.
ALLTALK_DIR="${DIRECT_CHAT_ALLTALK_DIR:-/home/lucy-ubuntu/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts}"
PYTHON_BIN="${FUSION_READER_GPU_PYTHON:-python3.11}"
PYTHON_VERSION="${FUSION_READER_GPU_PYTHON_VERSION:-3.11}"
ENV_DIR="${FUSION_READER_GPU_ENV:-/home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311}"
TORCH_INDEX="${FUSION_READER_GPU_TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
INSTALL_ALLTALK_DEPS="${FUSION_READER_GPU_INSTALL_ALLTALK_DEPS:-1}"
USE_CONDA="${FUSION_READER_GPU_USE_CONDA:-1}"

if [[ ! -d "$ALLTALK_DIR" ]]; then
  echo "AllTalk directory not found: $ALLTALK_DIR" >&2
  exit 1
fi

if [[ "$USE_CONDA" != "1" ]] && ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python not found: $PYTHON_BIN" >&2
  exit 1
fi

mkdir -p "$(dirname "$ENV_DIR")"

if [[ ! -x "$ENV_DIR/bin/python" ]]; then
  if [[ "$USE_CONDA" == "1" ]] && command -v conda >/dev/null 2>&1; then
    conda create -y -p "$ENV_DIR" "python=$PYTHON_VERSION" pip
  else
    "$PYTHON_BIN" -m venv "$ENV_DIR"
  fi
fi

PY="$ENV_DIR/bin/python"
"$PY" -m pip install --upgrade pip "setuptools<82" wheel

"$PY" -m pip install --upgrade torch torchvision torchaudio torchcodec "setuptools<82" --index-url "$TORCH_INDEX"

if [[ "$INSTALL_ALLTALK_DEPS" == "1" ]]; then
  REQ="$ALLTALK_DIR/system/requirements/requirements_textgen.txt"
  CLEAN_REQ="$ENV_DIR/requirements_textgen_gpu_5090.txt"
  grep -vE '^(torch|torchaudio|torchvision|nvidia-.*-cu11)' "$REQ" > "$CLEAN_REQ"
  "$PY" -m pip install -r "$CLEAN_REQ"
  "$PY" -m pip install --upgrade "transformers==4.39.1" "tokenizers==0.15.2" "huggingface-hub==0.22.1" "fastapi==0.135.1"
  "$PY" -m pip install --upgrade aiofiles nvidia-npp-cu12
fi

"$PY" -m pip install --upgrade torch torchvision torchaudio torchcodec "setuptools<82" --index-url "$TORCH_INDEX"

"$PY" "$ROOT/scripts/check_gpu_5090_env.py"

echo
echo "GPU env ready: $ENV_DIR"
echo "Start experimental GPU AllTalk with:"
echo "  ./scripts/start_reader_neural_tts_gpu_5090.sh"
echo "  ./scripts/start_fusion_reader_v2.sh"
echo
echo "Fusion GPU TTS is reserved on http://127.0.0.1:${FUSION_READER_GPU_TTS_PORT:-7853}."
echo "Do not point Fusion at 7852; that port may belong to another local agent."
