#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# External boundary defaults stay explicit here and remain overrideable via env vars.
DOCTORA_ROOT="${DOCTORA_LUCY_ROOT:-/home/lucy-ubuntu/Escritorio/doctora-lucy}"
ALLTALK_DIR="${DIRECT_CHAT_ALLTALK_DIR:-/home/lucy-ubuntu/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts}"
FUSION_TTS_PORT="${FUSION_READER_GPU_TTS_PORT:-7853}"
LUCY_TTS_PORT="${LUCY_TTS_PORT:-7854}"
LEGACY_TTS_PORT="${FUSION_READER_CPU_TTS_PORT:-7851}"
HISTORIC_PORT=7852
OWNER_FILE="${FUSION_READER_TTS_OWNER_FILE:-$ROOT/runtime/fusion_reader_v2/tts_owner.json}"
VOICE_RELEVANT_PATTERN='7851|7852|7853|7854|[Tt][Tt][Ss]|[Aa]ll[Tt]alk|[Ff]usion|[Dd]octora|[Ll]ucy|puerto|[Vv]oice'

strict_failures=0
strict_warnings=0
external_warnings=0
external_infos=0

strict_fail() {
  echo "FAIL: $*" >&2
  strict_failures=$((strict_failures + 1))
}

strict_warn() {
  echo "WARN: $*" >&2
  strict_warnings=$((strict_warnings + 1))
}

strict_ok() {
  echo "OK: $*"
}

external_warn() {
  echo "WARN: $*" >&2
  external_warnings=$((external_warnings + 1))
}

external_info() {
  echo "INFO: $*"
  external_infos=$((external_infos + 1))
}

require_strict_file_contains() {
  local file="$1"
  local pattern="$2"
  [[ -f "$file" ]] || {
    strict_fail "missing file: $file"
    return
  }
  grep -Fq -- "$pattern" "$file" || strict_fail "$file does not contain required pattern: $pattern"
}

require_strict_file_not_contains() {
  local file="$1"
  local pattern="$2"
  [[ -f "$file" ]] || {
    strict_fail "missing file: $file"
    return
  }
  if grep -Fq -- "$pattern" "$file"; then
    strict_fail "$file contains forbidden pattern: $pattern"
  fi
}

require_external_file_contains() {
  local file="$1"
  local pattern="$2"
  [[ -f "$file" ]] || {
    external_warn "missing file: $file"
    return
  }
  grep -Fq -- "$pattern" "$file" || external_warn "$file does not contain expected pattern: $pattern"
}

require_external_file_not_contains() {
  local file="$1"
  local pattern="$2"
  [[ -f "$file" ]] || {
    external_warn "missing file: $file"
    return
  }
  if grep -Fq -- "$pattern" "$file"; then
    external_warn "$file contains boundary-risk pattern: $pattern"
  fi
}

port_is_listening() {
  local port="$1"
  ss -ltn 2>/dev/null | grep -q "[.:]$port[[:space:]]"
}

owner_file_matches_fusion() {
  [[ -f "$OWNER_FILE" ]] || return 1
  grep -q '"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"' "$OWNER_FILE" || return 1
  grep -q "\"port\"[[:space:]]*:[[:space:]]*$FUSION_TTS_PORT" "$OWNER_FILE" || return 1

  local owner_pid
  owner_pid="$(sed -n 's/.*"owner_pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$OWNER_FILE" | head -1)"
  if [[ -n "$owner_pid" ]] && [[ -r "/proc/$owner_pid/cmdline" ]]; then
    if tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "tts_server:app" \
      && tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "--port $FUSION_TTS_PORT"; then
      return 0
    fi
  fi

  local listener_pid=""
  if command -v lsof >/dev/null 2>&1; then
    listener_pid="$(lsof -tiTCP:"$FUSION_TTS_PORT" -sTCP:LISTEN 2>/dev/null | head -1)"
  fi
  if [[ -z "$listener_pid" ]] && command -v ss >/dev/null 2>&1; then
    listener_pid="$(ss -ltnp 2>/dev/null | grep -F ":$FUSION_TTS_PORT" | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -1)"
  fi
  [[ -n "$listener_pid" ]] || return 1
  [[ -r "/proc/$listener_pid/cmdline" ]] || return 1
  tr '\0' ' ' <"/proc/$listener_pid/cmdline" | grep -q -- "tts_server:app" || return 1
  tr '\0' ' ' <"/proc/$listener_pid/cmdline" | grep -q -- "--port $FUSION_TTS_PORT" || return 1

  python3 - "$OWNER_FILE" "$listener_pid" <<'PY' >/dev/null 2>&1 || true
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
pid = int(sys.argv[2])
data = json.loads(path.read_text(encoding="utf-8"))
data["owner_pid"] = pid
path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
PY
  return 0
}

latest_relevant_doctora_boveda() {
  local db="$DOCTORA_ROOT/n8n_data/boveda_lucy.sqlite"
  if [[ ! -f "$db" ]]; then
    external_warn "Doctora boveda not found: $db"
    return
  fi
  if ! command -v sqlite3 >/dev/null 2>&1; then
    external_warn "sqlite3 not available; skipping Doctora boveda check"
    return
  fi

  sqlite3 "$db" "SELECT replace(replace(coalesce(contenido_memoria,'') || ' ' || coalesce(metadatos,''), char(10), ' '), char(13), ' ') FROM memoria_core ORDER BY id DESC LIMIT 200;" 2>/dev/null \
    | grep -im1 -E "$VOICE_RELEVANT_PATTERN" || true
}

latest_relevant_doctora_bunker() {
  local log="$DOCTORA_ROOT/data/lucy_bunker_log.jsonl"
  if [[ ! -f "$log" ]]; then
    external_warn "Doctora bunker log not found: $log"
    return
  fi
  tac "$log" 2>/dev/null | grep -im1 -E "$VOICE_RELEVANT_PATTERN" || true
}

check_doctora_voice_entry() {
  local source_name="$1"
  local latest="$2"
  if [[ -z "$latest" ]]; then
    external_warn "no relevant Doctora voice/TTS entry found in $source_name"
    return
  fi
  [[ "$latest" != *'"puerto_fusion":7852'* ]] || external_warn "$source_name still assigns Fusion to 7852"
  [[ "$latest" != *'"puerto_fusion": 7852'* ]] || external_warn "$source_name still assigns Fusion to 7852"
  [[ "$latest" != *'puerto_fusion:7852'* ]] || external_warn "$source_name still assigns Fusion to 7852"
  [[ "$latest" != *'"alltalk_port":7851'* ]] || external_warn "$source_name still assigns Lucy AllTalk to 7851"
  [[ "$latest" != *'"alltalk_port": 7851'* ]] || external_warn "$source_name still assigns Lucy AllTalk to 7851"
  [[ "$latest" != *'Lucy=7851'* ]] || external_warn "$source_name still assigns Lucy to 7851"
  [[ "$latest" != *'Fusion=7852'* ]] || external_warn "$source_name still assigns Fusion to 7852"
  if [[ "$latest" == *"$FUSION_TTS_PORT"* && "$latest" == *"$LUCY_TTS_PORT"* ]]; then
    external_info "$source_name mentions Fusion=$FUSION_TTS_PORT and Lucy=$LUCY_TTS_PORT"
  else
    external_warn "$source_name has a relevant voice/TTS entry but does not mention both current ports"
  fi
}

strict_status() {
  if (( strict_failures > 0 )); then
    echo "FAIL"
  elif (( strict_warnings > 0 )); then
    echo "OK_WITH_WARNINGS"
  else
    echo "OK"
  fi
}

external_status() {
  if (( external_warnings > 0 )); then
    echo "WARN"
  elif (( external_infos > 0 )); then
    echo "INFO"
  else
    echo "OK"
  fi
}

final_status() {
  if (( strict_failures > 0 )); then
    echo "FAIL"
  elif (( strict_warnings > 0 && external_warnings > 0 )); then
    echo "OK_WITH_WARNINGS"
  elif (( strict_warnings > 0 )); then
    echo "OK_WITH_STRICT_WARNINGS"
  elif (( external_warnings > 0 )); then
    echo "OK_WITH_EXTERNAL_WARNINGS"
  else
    echo "OK"
  fi
}

echo "Checking Fusion/Doctora voice port isolation..."
echo
echo "FUSION STRICT CHECKS"

require_strict_file_contains "$ROOT/AGENTS.md" "Fusion GPU URL: http://127.0.0.1:$FUSION_TTS_PORT"
require_strict_file_contains "$ROOT/AGENTS.md" "Doctora Lucy/Antigravity owns $LUCY_TTS_PORT"
require_strict_file_contains "$ROOT/FUSION_READER_V2_BLUEPRINT.md" "owner=fusion_reader_v2"
require_strict_file_contains "$ROOT/FUSION_READER_V2_BLUEPRINT.md" "puerto GPU estable y protegido es $FUSION_TTS_PORT"
require_strict_file_contains "$ROOT/agente/agent.yaml" "tts_url: http://127.0.0.1:$FUSION_TTS_PORT"
require_strict_file_not_contains "$ROOT/agente/agent.yaml" "tts_url: http://127.0.0.1:$LEGACY_TTS_PORT"
require_strict_file_contains "$ROOT/agente/system_prompt.md" "URL Fusion GPU: http://127.0.0.1:$FUSION_TTS_PORT"
require_strict_file_contains "$ROOT/fusion_reader_v2/tts.py" "tts_foreign_doctora_lucy_port"
require_strict_file_contains "$ROOT/fusion_reader_v2/tts.py" "tts_historic_unassigned_port"
require_strict_file_contains "$ROOT/scripts/start_reader_neural_tts_gpu_5090.sh" "FUSION_READER_TTS_OWNER_FILE"
require_strict_file_contains "$ROOT/scripts/start_reader_neural_tts_gpu_5090.sh" "owner_pid"
require_strict_file_contains "$ROOT/scripts/start_fusion_reader_v2.sh" "fusion_tts_owner_ok"
require_strict_file_contains "$ROOT/scripts/open_fusion_reader.sh" "fusion_gpu_ready"
require_strict_file_not_contains "$ROOT/scripts/start_fusion_reader_v2.sh" "127.0.0.1:$HISTORIC_PORT"
require_strict_file_not_contains "$ROOT/scripts/open_fusion_reader.sh" "127.0.0.1:$HISTORIC_PORT"

if port_is_listening "$HISTORIC_PORT"; then
  strict_fail "historic/unassigned port $HISTORIC_PORT is listening"
else
  strict_ok "historic port $HISTORIC_PORT is free"
fi

if port_is_listening "$FUSION_TTS_PORT"; then
  if owner_file_matches_fusion; then
    strict_ok "Fusion TTS port $FUSION_TTS_PORT has owner=fusion_reader_v2"
  else
    strict_fail "Fusion TTS port $FUSION_TTS_PORT is listening without a valid Fusion owner file"
  fi
else
  strict_warn "Fusion TTS port $FUSION_TTS_PORT is not listening"
fi

echo
echo "EXTERNAL BOUNDARY / DOCTORA INFO"

if [[ -d "$DOCTORA_ROOT" ]]; then
  require_external_file_contains "$DOCTORA_ROOT/VOICE_PORTS.md" "**$LUCY_TTS_PORT** | **Doctora Lucy TTS**"
  require_external_file_contains "$DOCTORA_ROOT/VOICE_PORTS.md" "**$FUSION_TTS_PORT** | **Fusion Reader TTS GPU**"
  require_external_file_contains "$DOCTORA_ROOT/AGENTS.md" "127.0.0.1:$LUCY_TTS_PORT"
  require_external_file_contains "$DOCTORA_ROOT/AGENTS.md" "127.0.0.1:$FUSION_TTS_PORT"
  require_external_file_contains "$DOCTORA_ROOT/GEMINI.md" "http://127.0.0.1:$LUCY_TTS_PORT"
  require_external_file_contains "$DOCTORA_ROOT/GEMINI.md" "http://127.0.0.1:$FUSION_TTS_PORT"
  require_external_file_contains "$DOCTORA_ROOT/bitacora_mantenimiento.md" "Doctora Lucy usa \`$LUCY_TTS_PORT\`; Fusion Reader v2 usa \`$FUSION_TTS_PORT\`"
  require_external_file_contains "$DOCTORA_ROOT/memoria/bitacora_mantenimiento.md" "Doctora Lucy usa AllTalk/TTS exclusivamente en \`127.0.0.1:$LUCY_TTS_PORT\`"
  require_external_file_contains "$DOCTORA_ROOT/memoria/demo.py" "Fusion Reader v2 usa $FUSION_TTS_PORT"
  require_external_file_contains "$DOCTORA_ROOT/scripts/lucy_alltalk.py" "127.0.0.1:$LUCY_TTS_PORT/api/generate"
  require_external_file_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" 'PORT="${LUCY_TTS_PORT:-7854}"'
  require_external_file_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" "--host 127.0.0.1"
  require_external_file_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" "setsid"
  require_external_file_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" ".gemini/antigravity/voice_env"
  require_external_file_contains "$DOCTORA_ROOT/.agents/workflows/boot.md" "127.0.0.1:$LUCY_TTS_PORT/api/ready"
  require_external_file_not_contains "$DOCTORA_ROOT/.agents/workflows/boot.md" "grep $LEGACY_TTS_PORT"
  require_external_file_not_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" "fuser -k $FUSION_TTS_PORT"
  require_external_file_not_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" "fuser -k $HISTORIC_PORT"
  check_doctora_voice_entry "latest relevant Doctora boveda entry" "$(latest_relevant_doctora_boveda)"
  check_doctora_voice_entry "latest relevant Doctora bunker entry" "$(latest_relevant_doctora_bunker)"
else
  external_warn "Doctora root not found: $DOCTORA_ROOT"
fi

if [[ -d "$ALLTALK_DIR" ]]; then
  require_external_file_contains "$ALLTALK_DIR/launch.sh" 'PORT="${LUCY_TTS_PORT:-7854}"'
  require_external_file_contains "$ALLTALK_DIR/launch.sh" "Refusing to start on reserved/historical port"
  require_external_file_contains "$ALLTALK_DIR/launch.sh" "--host 127.0.0.1"
  require_external_file_not_contains "$ALLTALK_DIR/launch.sh" "--port $HISTORIC_PORT"
  require_external_file_not_contains "$ALLTALK_DIR/launch.sh" "fuser -k $HISTORIC_PORT"
  if [[ -f "$ALLTALK_DIR/launch.sh.bak" ]]; then
    require_external_file_contains "$ALLTALK_DIR/launch.sh.bak" 'PORT="${LUCY_TTS_PORT:-7854}"'
    require_external_file_contains "$ALLTALK_DIR/launch.sh.bak" "--host 127.0.0.1"
    require_external_file_not_contains "$ALLTALK_DIR/launch.sh.bak" "--port $HISTORIC_PORT"
    require_external_file_not_contains "$ALLTALK_DIR/launch.sh.bak" "fuser -k $HISTORIC_PORT"
  fi
else
  external_warn "AllTalk directory not found: $ALLTALK_DIR"
fi

if port_is_listening "$LUCY_TTS_PORT"; then
  external_info "Doctora Lucy TTS port $LUCY_TTS_PORT is listening"
else
  external_info "Doctora Lucy TTS port $LUCY_TTS_PORT is not listening"
fi

if port_is_listening "$LEGACY_TTS_PORT"; then
  external_info "legacy/shared TTS port $LEGACY_TTS_PORT is listening; verify this is intentional"
fi

echo
echo "FINAL RESULT"
echo "FUSION STRICT CHECKS: $(strict_status)"
echo "EXTERNAL BOUNDARY / DOCTORA INFO: $(external_status)"
echo "FINAL RESULT: $(final_status)"

if (( strict_failures > 0 )); then
  exit 1
fi
