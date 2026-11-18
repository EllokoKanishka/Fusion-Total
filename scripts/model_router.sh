#!/usr/bin/env bash
# Historical helper retired: it changed the global OpenClaw model.
set -euo pipefail
if [[ "${1:-}" == "check" ]]; then
  echo "MODEL_ROUTER_DISABLED_USE_FUSION_DIALOGUE"
  exit 0
fi
echo "Este helper histórico está deshabilitado. Usá los proveedores aislados de Panda (fusion-dialogue/fusion-research)." >&2
exit 2
