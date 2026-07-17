#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fusion_reader_v2.owned_subprocess import run_owned  # noqa: E402

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"gh[opurs]_[A-Za-z0-9]{30,}"),
    "OpenAI key": re.compile(r"sk-[A-Za-z0-9_-]{32,}"),
}


def main() -> None:
    tracked = run_owned(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        timeout=30.0,
    ).stdout.split(b"\0")
    findings: list[str] = []
    for raw_path in tracked:
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        path = ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{relative}: {label}")
    if findings:
        raise SystemExit("secret-pattern findings:\n" + "\n".join(findings))
    print("secret-pattern scan: clean")


if __name__ == "__main__":
    main()
