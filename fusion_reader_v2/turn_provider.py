from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar


provider_for_turn: ContextVar[str | None] = ContextVar("provider_for_turn", default=None)
external_research_permission: ContextVar[bool | None] = ContextVar("external_research_permission", default=None)


@contextmanager
def use_turn_provider(value: object, allowed: set[str], default: str, *, allow_external_research: bool = False):
    if not isinstance(value, str) or (value and value not in allowed):
        raise ValueError("invalid_turn_provider")
    token = provider_for_turn.set(value or default)
    permission = external_research_permission.set(allow_external_research)
    try:
        yield
    finally:
        external_research_permission.reset(permission)
        provider_for_turn.reset(token)
