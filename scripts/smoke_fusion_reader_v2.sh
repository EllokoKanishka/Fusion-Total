#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWNER_FILE="${FUSION_READER_TTS_OWNER_FILE:-$ROOT/runtime/fusion_reader_v2/tts_owner.json}"
UI_PORT="${FUSION_READER_V2_PORT:-8010}"
GPU_TTS_PORT="${FUSION_READER_GPU_TTS_PORT:-7853}"
CPU_TTS_PORT="${FUSION_READER_CPU_TTS_PORT:-${DIRECT_CHAT_ALLTALK_PORT:-7851}}"
STT_PORT="${FUSION_READER_STT_PORT:-8021}"
STT_PROVIDER_RAW="${FUSION_READER_STT_PROVIDER:-auto}"
case "${STT_PROVIDER_RAW,,}" in
  cli) STT_PROVIDER="cli" ;;
  server|faster_whisper|faster-whisper) STT_PROVIDER="server" ;;
  *) STT_PROVIDER="auto" ;;
esac
STT_COMMAND="${FUSION_READER_STT_COMMAND:-whisper}"
OLLAMA_PORT="${FUSION_READER_OLLAMA_PORT:-11434}"
SEARXNG_URL="${FUSION_READER_SEARXNG_URL:-http://127.0.0.1:8080}"
HISTORIC_PORT=7852
DOCTORA_PORT="${LUCY_TTS_PORT:-7854}"

strict_failures=0
warnings=0
infos=0

strict_fail() {
  echo "FAIL: $*" >&2
  strict_failures=$((strict_failures + 1))
}

warn() {
  echo "WARN: $*" >&2
  warnings=$((warnings + 1))
}

info() {
  echo "INFO: $*"
  infos=$((infos + 1))
}

ok() {
  echo "OK: $*"
}

port_is_listening() {
  local port="$1"
  ss -ltn 2>/dev/null | grep -q "[.:]$port[[:space:]]"
}

curl_json_ok() {
  local url="$1"
  curl -fsS --max-time 2 "$url" >/dev/null 2>&1
}

owner_file_matches_fusion() {
  [[ -f "$OWNER_FILE" ]] || return 1
  grep -q '"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"' "$OWNER_FILE" || return 1
  grep -q "\"port\"[[:space:]]*:[[:space:]]*$GPU_TTS_PORT" "$OWNER_FILE" || return 1
}

final_status() {
  if (( strict_failures > 0 )); then
    echo "FAIL"
  elif (( warnings > 0 )); then
    echo "OK_WITH_WARNINGS"
  else
    echo "OK"
  fi
}

echo "Fusion Reader v2 smoke check"
echo
echo "FUSION FILE CHECKS"

for path in \
  "$ROOT/fusion_reader_v2" \
  "$ROOT/scripts/start_fusion_reader_v2.sh" \
  "$ROOT/scripts/open_fusion_reader.sh" \
  "$ROOT/scripts/verify_voice_port_isolation.sh" \
  "$ROOT/docs/DEPENDENCIES_V2.md"
do
  if [[ -e "$path" ]]; then
    ok "found $(realpath --relative-to="$ROOT" "$path" 2>/dev/null || echo "$path")"
  else
    strict_fail "missing required path: $path"
  fi
done

echo
echo "FUSION PORT CHECKS"

if port_is_listening "$UI_PORT"; then
  if curl_json_ok "http://127.0.0.1:${UI_PORT}/api/status"; then
    ok "UI/API ${UI_PORT} is listening and /api/status responds"
  else
    warn "UI/API ${UI_PORT} is listening but /api/status did not respond cleanly"
  fi
else
  warn "UI/API ${UI_PORT} is not listening"
fi

if port_is_listening "$GPU_TTS_PORT"; then
  if owner_file_matches_fusion; then
    ok "Fusion GPU TTS ${GPU_TTS_PORT} is listening with owner file for fusion_reader_v2"
  else
    warn "Fusion GPU TTS ${GPU_TTS_PORT} is listening but owner validation is not confirmed"
  fi
else
  warn "Fusion GPU TTS ${GPU_TTS_PORT} is not listening"
fi

if port_is_listening "$CPU_TTS_PORT"; then
  info "CPU fallback TTS ${CPU_TTS_PORT} is listening"
else
  info "CPU fallback TTS ${CPU_TTS_PORT} is not listening"
fi

info "requested STT provider: ${STT_PROVIDER} (value: ${STT_PROVIDER_RAW})"
if [[ "$STT_PROVIDER" == "cli" ]]; then
  if command -v "$STT_COMMAND" >/dev/null 2>&1 || [[ -x "$STT_COMMAND" ]]; then
    ok "Whisper CLI command is available: ${STT_COMMAND}"
  else
    warn "Whisper CLI command is unavailable: ${STT_COMMAND}"
  fi
  if port_is_listening "$STT_PORT"; then
    info "STT server ${STT_PORT} is listening but is not required in cli mode"
  else
    info "STT server ${STT_PORT} is not listening and is not required in cli mode"
  fi
elif port_is_listening "$STT_PORT"; then
  if curl_json_ok "http://127.0.0.1:${STT_PORT}/health"; then
    ok "STT ${STT_PORT} is listening and /health responds"
  else
    warn "STT ${STT_PORT} is listening but /health did not respond cleanly"
  fi
else
  warn "STT ${STT_PORT} is not listening for provider ${STT_PROVIDER}"
  if [[ "$STT_PROVIDER" == "auto" ]]; then
    if command -v "$STT_COMMAND" >/dev/null 2>&1 || [[ -x "$STT_COMMAND" ]]; then
      ok "Whisper CLI fallback is available: ${STT_COMMAND}"
    else
      warn "Whisper CLI fallback is unavailable: ${STT_COMMAND}"
    fi
  fi
fi

if port_is_listening "$OLLAMA_PORT"; then
  if curl_json_ok "http://127.0.0.1:${OLLAMA_PORT}/api/tags"; then
    ok "Ollama ${OLLAMA_PORT} is listening and /api/tags responds"
  else
    warn "Ollama ${OLLAMA_PORT} is listening but /api/tags did not respond cleanly"
  fi
else
  warn "Ollama ${OLLAMA_PORT} is not listening"
fi

if curl_json_ok "${SEARXNG_URL%/}/search?q=test&format=json"; then
  ok "SearXNG responds at ${SEARXNG_URL}"
else
  info "SearXNG does not respond at ${SEARXNG_URL}"
fi

echo
echo "BOUNDARY CHECKS"

if port_is_listening "$HISTORIC_PORT"; then
  strict_fail "historic/unassigned port ${HISTORIC_PORT} is listening"
else
  ok "historic port ${HISTORIC_PORT} is free"
fi

if port_is_listening "$DOCTORA_PORT"; then
  info "external Doctora/Lucy port ${DOCTORA_PORT} is listening"
else
  info "external Doctora/Lucy port ${DOCTORA_PORT} is not listening"
fi

echo
echo "VERIFY INTEGRATION"

verify_output="$("$ROOT/scripts/verify_voice_port_isolation.sh" 2>&1)"
verify_exit=$?
printf '%s\n' "$verify_output"
verify_result="$(sed -n 's/^FINAL RESULT: //p' <<<"$verify_output" | tail -n 1)"
if (( verify_exit != 0 )); then
  strict_fail "verify_voice_port_isolation.sh returned exit code ${verify_exit}"
else
  case "$verify_result" in
    FAIL)
      strict_fail "verify_voice_port_isolation.sh reported FINAL RESULT: FAIL"
      ;;
    OK_WITH_WARNINGS|OK_WITH_STRICT_WARNINGS|OK_WITH_EXTERNAL_WARNINGS)
      warn "verify_voice_port_isolation.sh reported FINAL RESULT: ${verify_result}"
      ;;
    OK)
      ok "verify_voice_port_isolation.sh reported FINAL RESULT: OK"
      ;;
    *)
      warn "verify_voice_port_isolation.sh reported unknown or missing FINAL RESULT: ${verify_result:-<missing>}"
      ;;
  esac
fi

echo
echo "FINAL RESULT"
echo "FINAL RESULT: $(final_status)"

if (( strict_failures > 0 )); then
  exit 1
fi
exit 0
