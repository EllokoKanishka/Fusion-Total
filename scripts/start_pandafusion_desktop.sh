#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
desktop_root="$project_root/desktop"

if ! curl --fail --silent --max-time 2 http://127.0.0.1:8010/api/status >/dev/null; then
  fusionctl start
fi

cd "$desktop_root"
exec npm run dev
