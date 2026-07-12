"""Application services with explicit state and resource ownership."""

from .persistence import AtomicJSONStore, PersistenceWarning

__all__ = ["AtomicJSONStore", "PersistenceWarning"]
