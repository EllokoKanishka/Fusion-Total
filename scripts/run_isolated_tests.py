"""Run the test suite with a private home, roots and owned loopback services."""

from pathlib import Path
import argparse
import io
import json
import os
import socket
import sys
import tempfile
import unittest
import ast
from unittest.mock import patch

P = Path(__file__).resolve().parents[1]
CODE = P
_temporary = tempfile.TemporaryDirectory(prefix="panda-tests-")
TMP = Path(_temporary.name)
OUTPUT = Path(os.environ.get("FUSION_TEST_EVIDENCE_ROOT", str(TMP / "evidence")))
OUTPUT.mkdir(parents=True, exist_ok=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(CODE))
os.chdir(CODE)
tempfile.tempdir = str(TMP)
os.environ["TMPDIR"] = str(TMP)
endpoints = set()
loopback = ("127.0.0.1", "::1")
blocked = []
real_bind = socket.socket.bind


def bind(sock, address):
    if isinstance(address, tuple) and address[0] in loopback and (address[1] == 0 or address[:2] in endpoints):
        r = real_bind(sock, address)
        endpoints.add(sock.getsockname()[:2])
        return r
    raise RuntimeError("Test isolation: only owned ephemeral loopback binds allowed")


socket.socket.bind = bind


def guard(event, args):
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or address[:2] not in endpoints:
            blocked.append(
                "network:"
                + str(
                    [
                        (f.name, Path(f.filename).name)
                        for f in __import__("traceback").extract_stack()
                        if "/tests/" in f.filename
                    ][-3:]
                )
            )
            raise RuntimeError("Test isolation: real services blocked")
    if event == "subprocess.Popen":
        argv = args[1]
        name = Path(argv[0]).name
        allow = (
            (
                argv[0] == "/bin/bash"
                and len(argv) == 2
                and Path(argv[1]).name == "gpu-policy-fixture.sh"
                and (P in Path(argv[1]).resolve().parents or TMP in Path(argv[1]).resolve().parents)
            )
            or (argv[0] == sys.executable and len(argv) >= 3 and argv[1] == "-c" and argv[2] in CHILD_CODES)
            or name in {"git", "ffmpeg", "ffprobe", "bash", "node", "pdfinfo", "systemd-analyze"}
            or (
                name in {"pdftotext", "pdfimages", "pdftoppm", "tesseract"}
                and all(
                    not Path(arg).is_absolute() or Path(arg).resolve().is_relative_to(TMP)
                    for arg in argv[1:]
                    if isinstance(arg, str)
                )
            )
            or (
                str(CODE / "scripts/open_fusion_reader.sh") == argv[0]
                or str(CODE / "scripts/start_pandafusion_systemd.sh") == argv[0]
            )
            or (
                argv[0] == sys.executable
                and len(argv) == 3
                and argv[1] == "-c"
                and argv[2].startswith("import scripts.fusion_reader_v2_server;")
            )
        )
        if not allow:
            blocked.append("subprocess:" + name)
            raise RuntimeError("Test isolation: non-test child blocked")
    if event == "open":
        target, mode, flags = args
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        )
        if writing and isinstance(target, (str, bytes, os.PathLike)):
            q = Path(os.fsdecode(target)).resolve()
            if (
                P not in q.parents
                and TMP not in q.parents
                and q != TMP
                and OUTPUT.resolve() not in q.parents
                and q != Path("/dev/null")
            ):
                blocked.append("write")
                raise RuntimeError("Test isolation: writes outside implementation/audit roots blocked")


CHILD_CODES = set()
for filename in (
    "tests/integration/test_import_lifecycle.py",
    "tests/unit/test_shutdown_safety.py",
    "tests/unit/test_owned_subprocess.py",
    "tests/unit/test_desktop_lifetime.py",
    "tests/unit/test_managed_ollama.py",
):
    tree = ast.parse((CODE / filename).read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (
                node.value.startswith(
                    (
                        "import time",
                        "import os,time",
                        "import signal,time",
                        "import socket,time",
                        "import sys;",
                        "print(",
                        "raise SystemExit(",
                    )
                )
            )
        ):
            CHILD_CODES.add(node.value)
sys.addaudithook(guard)
# A private HOME is set before test imports, including historical compatibility modules.
private_home = TMP / "home"
private_home.mkdir(exist_ok=True)
for key in list(os.environ):
    if key.startswith(("FUSION_READER_", "DIRECT_CHAT_", "OPENCLAW_", "PANDA_")):
        os.environ.pop(key)
os.environ.update(
    {
        "HOME": str(private_home),
        "OPENCLAW_RUNTIME_DIR": str(TMP / "legacy-runtime"),
        "OPENCLAW_STATE_DIR": str(TMP / "legacy-state"),
        "DIRECT_CHAT_TTS_DRY_RUN": "1",
        "DIRECT_CHAT_VOICE_STATE_PATH": str(TMP / "legacy-voice.json"),
        "FUSION_READER_RUNTIME_ROOT": str(TMP / "runtime"),
        "FUSION_READER_LIBRARY_ROOT": str(TMP / "library"),
        "FUSION_READER_DOWNLOADS_ROOT": str(TMP / "downloads"),
        "FUSION_READER_IGNORE_ENV_FILE": "1",
    }
)
parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=["antes", "despues", "ampliadas", "stress"], nargs="?", default="ampliadas")
args = parser.parse_args()
names = [
    "unittest discovery: tests/stress/stress_*.py"
    if args.phase == "stress"
    else "unittest discovery: tests/**/test_*.py"
]
suite = unittest.defaultTestLoader.discover(
    str(CODE / "tests/stress") if args.phase == "stress" else str(CODE / "tests"),
    pattern="stress_*.py" if args.phase == "stress" else "test_*.py",
    top_level_dir=str(CODE),
)
stream = io.StringIO()
with patch("fusion_reader_v2.facade.FusionReaderV2._play"):
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
report = {
    "phase": args.phase,
    "python": sys.version,
    "selected": names,
    "testsRun": result.testsRun,
    "failures": len(result.failures),
    "errors": len(result.errors),
    "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
    "blocked": blocked,
    "log": stream.getvalue(),
}
(OUTPUT / f"pruebas-{args.phase}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
(OUTPUT / f"pruebas-{args.phase}.txt").write_text(stream.getvalue())
print(json.dumps({k: v for k, v in report.items() if k != "log"}, ensure_ascii=False))
print(stream.getvalue() if not result.wasSuccessful() else stream.getvalue()[-1500:])
raise SystemExit(not result.wasSuccessful() or bool(blocked))
