from __future__ import annotations

from collections.abc import Set
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID


DEEPSEEK_MODEL = "deepseek-r1:32b"
GEMMA_MODEL = "gemma4:26b-a4b-it-q4_K_M"
LFM_MODEL = "lfm2.5:24b-a2b-q4_K_M"
LABORATORY_LOCAL_PROVIDERS = frozenset({"local", "deepseek", "gemma", "lfm"})
LABORATORY_PROVIDERS = LABORATORY_LOCAL_PROVIDERS | {"openai"}


provider_for_turn: ContextVar[str | None] = ContextVar("provider_for_turn", default=None)
external_research_permission: ContextVar[bool | None] = ContextVar("external_research_permission", default=None)
laboratory_session_for_turn: ContextVar[str] = ContextVar("laboratory_session_for_turn", default="")
laboratory_generation_for_turn: ContextVar[int | None] = ContextVar("laboratory_generation_for_turn", default=None)


def laboratory_session_id(value: object) -> str:
    if value == "":
        return ""
    if isinstance(value, str) and len(value) == 36:
        session = UUID(value)
        if session.version == 4 and value == str(session):
            return value
    raise ValueError("invalid_laboratory_session")


@contextmanager
def use_turn_provider(
    value: object,
    allowed: Set[str],
    default: str,
    *,
    allow_external_research: bool = False,
    laboratory_session: object = "",
    laboratory_generation: int | None = None,
):
    if not isinstance(value, str) or (value and value not in allowed):
        raise ValueError("invalid_turn_provider")
    session = laboratory_session_id(laboratory_session)
    token = provider_for_turn.set(value or default)
    permission = external_research_permission.set(allow_external_research)
    session_token = laboratory_session_for_turn.set(session)
    generation_token = laboratory_generation_for_turn.set(laboratory_generation)
    try:
        yield
    finally:
        laboratory_generation_for_turn.reset(generation_token)
        laboratory_session_for_turn.reset(session_token)
        external_research_permission.reset(permission)
        provider_for_turn.reset(token)
