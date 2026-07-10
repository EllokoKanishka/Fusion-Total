#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


# Machine-local GPU env default remains overrideable via FUSION_READER_GPU_ENV.
DEFAULT_GPU_ENV = Path(os.environ.get("FUSION_READER_GPU_ENV", "/home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311"))

if os.environ.get("FUSION_READER_GPU_CHECK_NO_REEXEC") != "1":
    env_python = DEFAULT_GPU_ENV / "bin" / "python"
    if env_python.exists() and Path(sys.executable).resolve() != env_python.resolve():
        os.environ["FUSION_READER_GPU_CHECK_NO_REEXEC"] = "1"
        os.execv(str(env_python), [str(env_python), *sys.argv])


def main() -> int:
    try:
        import torch
    except Exception as exc:
        print(f"torch import failed: {exc}", file=sys.stderr)
        return 1

    print(f"python: {sys.version.split()[0]}")
    print(f"torch: {torch.__version__}")
    print(f"torch cuda runtime: {torch.version.cuda}")
    print(f"cuda available: {torch.cuda.is_available()}")
    if not torch.cuda.is_available():
        return 2

    name = torch.cuda.get_device_name(0)
    capability = torch.cuda.get_device_capability(0)
    print(f"gpu: {name}")
    print(f"capability: sm_{capability[0]}{capability[1]}")
    if capability < (12, 0):
        print("warning: expected RTX 50-series capability sm_120 or newer", file=sys.stderr)

    x = torch.randn(64, 64, device="cuda")
    y = torch.matmul(x, x)
    torch.cuda.synchronize()
    print(f"matmul ok: {float(y[0, 0]):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
