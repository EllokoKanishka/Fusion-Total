"""Deduplicate provider observations within one status response, without cross-request staleness."""

from contextvars import ContextVar
from functools import wraps

_OBSERVATIONS: ContextVar[dict[int, dict] | None] = ContextVar("reader_health_observations", default=None)


def observe_health(provider):
    observations = _OBSERVATIONS.get()
    if observations is None:
        return provider.health()
    key = id(provider)
    if key not in observations:
        observations[key] = provider.health()
    return dict(observations[key] or {})


def health_scope(function):
    @wraps(function)
    def scoped(*args, **kwargs):
        if _OBSERVATIONS.get() is not None:
            return function(*args, **kwargs)
        token = _OBSERVATIONS.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _OBSERVATIONS.reset(token)

    return scoped
