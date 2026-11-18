"""Update only installer-owned settings, backing up existing bytes first."""

from pathlib import Path
import os
import sys
import tempfile
import uuid


def update(path, python, port):
    path = Path(path)
    original = path.read_bytes() if path.exists() else b""
    if path.exists():
        backup = path.with_name(path.name + ".backup." + uuid.uuid4().hex)
        with backup.open("xb") as handle:
            os.chmod(backup, 0o600)
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
    values = {"FUSION_READER_PYTHON": python, "FUSION_READER_V2_PORT": str(port)}
    lines = []
    for line in original.decode("utf-8").splitlines():
        key = line.split("=", 1)[0]
        if key not in values:
            lines.append(line)
    lines.extend(key + "=" + value for key, value in values.items())
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".launcher-env-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    update(*sys.argv[1:])
