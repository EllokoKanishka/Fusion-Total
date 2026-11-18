"""Observed GPU occupancy and explicit admission estimates, independent of games."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from .owned_subprocess import run_owned


@dataclass(frozen=True)
class GPUBudget:
    total_mib: int | None
    used_mib: int | None
    reserve_mib: int = 4096
    detail: str = ""

    @property
    def measured(self):
        return self.total_mib is not None

    @property
    def available_mib(self):
        if self.total_mib is None or self.used_mib is None:
            return None
        return max(0, self.total_mib - self.used_mib - self.reserve_mib)

    def permits(self, required_mib: int) -> bool:
        return (
            self.total_mib is not None
            and self.used_mib is not None
            and self.total_mib - self.used_mib >= self.reserve_mib + max(0, required_mib)
        )

    def to_dict(self):
        return {**asdict(self), "available_mib": self.available_mib, "measured": self.total_mib is not None}


def read_gpu_budget(reserve_mib=4096) -> GPUBudget:
    try:
        result = run_owned(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
            timeout=2,
            text=True,
            output_limit=4096,
        )
        # Panda's configured CUDA device 0; do not sum unrelated GPUs.
        total, used = map(int, result.stdout.strip().splitlines()[0].split(","))
        if result.returncode or not 0 <= used <= total or total <= 0:
            raise ValueError("invalid_gpu_sample")
        return GPUBudget(total, used, max(0, reserve_mib), "observed")
    except Exception as exc:
        return GPUBudget(None, None, max(0, reserve_mib), "gpu_sample_unavailable:" + type(exc).__name__)


def main():
    budget = read_gpu_budget(int(os.environ.get("FUSION_READER_GPU_RESERVE_MIB", "4096")))
    required = int(os.environ.get("FUSION_READER_GPU_START_REQUIRED_MIB", "4096"))
    print(json.dumps(budget.to_dict()))
    return 3 if budget.available_mib is None else (0 if budget.permits(required) else 2)


if __name__ == "__main__":
    raise SystemExit(main())
