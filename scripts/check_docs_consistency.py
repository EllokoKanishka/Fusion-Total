#!/usr/bin/env python3
from __future__ import annotations

import re
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
    "docs/LICENSING_DECISION_REQUIRED.md",
    "docs/HANDOFF_CODEX_2026-07-12.md",
    "docs/CONSOLIDATION_FINAL_2026-07-12.md",
    "docs/audits/FINAL_CHANGE_INVENTORY_2026-07-12.md",
    "docs/audits/ARCHITECTURE_BEFORE_AFTER_2026-07-12.md",
    "requirements/constraints-py311.txt",
    "requirements/constraints-py312.txt",
    "scripts/generate_handoff.py",
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
    configuration = (ROOT / "docs/CONFIGURATION.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    if "local-only" not in configuration.lower() or "Non-loopback binds are rejected" not in configuration:
        raise SystemExit("configuration must document loopback-only HTTP")
    for obsolete in ("FUSION_READER_ALLOW_REMOTE", "FUSION_READER_API_TOKEN"):
        if obsolete in env_example or obsolete in configuration:
            raise SystemExit(f"obsolete remote variable remains documented: {obsolete}")
    package = (ROOT / "package.json").read_text(encoding="utf-8")
    if '"private": true' not in package or '"license": "ISC"' in package:
        raise SystemExit("package metadata contradicts pending licensing decision")
    state = (ROOT / "FUSION_READER_V2_STATE.md").read_text(encoding="utf-8")
    if "2.0.0" not in state or 'version = "2.0.0"' not in (ROOT / "pyproject.toml").read_text(encoding="utf-8"):
        raise SystemExit("canonical version mismatch")
    missing_links: list[str] = []
    markdown_files = [ROOT / relative for relative in REQUIRED if relative.endswith(".md")]
    for source in markdown_files:
        text = source.read_text(encoding="utf-8")
        for raw_target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = raw_target.split("#", 1)[0].strip()
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (source.parent / target).resolve()
            if not resolved.exists():
                missing_links.append(f"{source.relative_to(ROOT)} -> {target}")
    if missing_links:
        raise SystemExit("missing internal links: " + ", ".join(missing_links))
    archive = ROOT / "docs/archive"
    if not archive.is_dir() or not any(archive.glob("*.md")):
        raise SystemExit("historical documentation must remain identified under docs/archive")
    print(f"documentation consistency: {len(REQUIRED)} canonical artifacts verified")


if __name__ == "__main__":
    main()
