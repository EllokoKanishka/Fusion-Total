#!/usr/bin/env bash
# Helper script to load .env safely and resolve python
set -euo pipefail

if [[ -z "${ROOT:-}" ]]; then
  echo "ERROR: ROOT must be set before sourcing env_helper.sh" >&2
  exit 1
fi

load_env_safe() {
  if [[ -f "$ROOT/.env" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" =~ ^[[:space:]]*# ]] || [[ "$line" =~ ^[[:space:]]*$ ]]; then
        continue
      fi
      if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
        key="${BASH_REMATCH[1]}"
        val="${BASH_REMATCH[2]}"
        val="${val#\"}"
        val="${val%\"}"
        val="${val#\'}"
        val="${val%\'}"
        if [[ -z "${!key:-}" ]]; then
          export "$key"="$val"
        fi
      fi
    done < "$ROOT/.env"
  fi
}

verify_python() {
  local py="$1"
  if [[ -x "$py" ]]; then
    if "$py" -c "import reportlab, docx, PIL" >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

find_python() {
  if [[ -n "${FUSION_READER_PYTHON:-}" ]]; then
    if verify_python "$FUSION_READER_PYTHON"; then
      echo "$FUSION_READER_PYTHON"
      return 0
    fi
    echo "ERROR: FUSION_READER_PYTHON=$FUSION_READER_PYTHON no tiene las dependencias requeridas (reportlab, python-docx, Pillow)." >&2
    return 1
  fi

  if verify_python "$ROOT/.venv/bin/python3"; then
    echo "$ROOT/.venv/bin/python3"
    return 0
  fi
  if verify_python "$ROOT/.venv/bin/python"; then
    echo "$ROOT/.venv/bin/python"
    return 0
  fi
  if verify_python "$ROOT/venv/bin/python3"; then
    echo "$ROOT/venv/bin/python3"
    return 0
  fi
  if verify_python "$ROOT/venv/bin/python"; then
    echo "$ROOT/venv/bin/python"
    return 0
  fi

  local candidates=(
    "python3"
    "python"
    "${HOME}/Miniforge3/bin/python3"
    "${HOME}/Miniforge3/bin/python"
    "/usr/bin/python3"
    "/usr/local/bin/python3"
  )
  for candidate in "${candidates[@]}"; do
    local path=""
    if [[ "$candidate" == /* ]]; then
      path="$candidate"
    else
      path="$(command -v "$candidate" || true)"
    fi
    if [[ -n "$path" ]]; then
      if verify_python "$path"; then
        echo "$path"
        return 0
      fi
    fi
  done

  return 1
}
