#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md",
    "AGENTS.md",
    "FUSION_READER_V2_STATE.md",
    "docs/ARCHITECTURE.md",
    "docs/OPERATIONS.md",
    "docs/CONFIGURATION.md",
    "docs/TESTING.md",
    "docs/SECURITY.md",
    "docs/TROUBLESHOOTING.md",
    "docs/PORTABILITY.md",
    "docs/CONTRACTS.md",
    "docs/QUALITY_GATES.md",
)


def main() -> None:
    missing = [relative for relative in REQUIRED if not (ROOT / relative).is_file()]
    if missing:
        raise SystemExit(f"missing canonical documentation: {', '.join(missing)}")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    ordered = [
        "1. `AGENTS.md`",
        "2. `FUSION_READER_V2_STATE.md`",
        "3. `docs/ARCHITECTURE.md`",
        "4. `docs/OPERATIONS.md`",
        "5. `docs/CONTRACTS.md`",
    ]
    positions = [agents.find(item) for item in ordered]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise SystemExit("AGENTS.md does not contain the canonical reading order")
    print(f"documentation consistency: {len(REQUIRED)} canonical files present")


if __name__ == "__main__":
    main()
