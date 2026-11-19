from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar


provider_for_turn: ContextVar[str | None] = ContextVar("provider_for_turn", default=None)


@contextmanager
def use_turn_provider(value: object, allowed: set[str], default: str):
    if not isinstance(value, str) or (value and value not in allowed):
        raise ValueError("invalid_turn_provider")
    token = provider_for_turn.set(value or default)
    try:
        yield
    finally:
        provider_for_turn.reset(token)
