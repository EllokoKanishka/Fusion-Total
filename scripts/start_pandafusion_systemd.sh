#!/usr/bin/env bash
# Systemd shares the same startup policy as fusionctl, in foreground mode.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export FUSION_READER_FOREGROUND=1
exec bash "$ROOT/scripts/start_fusion_reader_v2.sh" "$@"
