#!/usr/bin/env python3
from __future__ import annotations

import sys

_SAFE_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/._-")


def escape_systemd_path(path: str) -> str:
    """Encode an absolute filesystem path for systemd path-valued directives.

    Path-only directives such as EnvironmentFile= and WorkingDirectory= do not
    accept shell-style quoting around a path containing spaces. Encoding unsafe
    UTF-8 bytes as C-style hexadecimal escapes keeps the path absolute while
    preserving spaces and non-ASCII characters when systemd parses the unit.
    """
    if not path.startswith("/"):
        raise ValueError("systemd paths must be absolute")

    return "".join(
        chr(byte) if byte in _SAFE_BYTES else f"\\x{byte:02x}"
        for byte in path.encode("utf-8")
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: systemd_unit_path.py /absolute/path", file=sys.stderr)
        return 2

    try:
        print(escape_systemd_path(args[0]))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
