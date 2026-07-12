from __future__ import annotations

import os

try:
    from fastapi import FastAPI
except Exception:
    FastAPI = None

if FastAPI is not None and not hasattr(FastAPI, "route"):

    def route(self, path, methods=None, **kwargs):
        return self.api_route(path, methods=methods, **kwargs)

    FastAPI.route = route

if os.environ.get("FUSION_READER_ALLOW_TORCH_PICKLE_LOAD") == "1":
    try:
        import torch
    except Exception:
        torch = None

    if torch is not None and not getattr(torch.load, "_fusion_reader_compat", False):
        _torch_load = torch.load

        def load_with_legacy_checkpoint_default(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return _torch_load(*args, **kwargs)

        load_with_legacy_checkpoint_default._fusion_reader_compat = True
        torch.load = load_with_legacy_checkpoint_default
