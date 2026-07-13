from __future__ import annotations

from collections.abc import Callable

from fusion_reader_v2.reader import ReaderSession


class ReaderService:
    """Owns reader cursor navigation and its persistence/prefetch effects."""

    def __init__(
        self,
        session: ReaderSession,
        *,
        persist: Callable[[], None],
        prefetch_current: Callable[[], None],
        status: Callable[[], dict],
    ) -> None:
        self.session = session
        self.persist = persist
        self.prefetch_current = prefetch_current
        self.status = status

    def next(self) -> dict:
        self.session.next_chunk()
        return self._after_navigation()

    def previous(self) -> dict:
        self.session.previous_chunk()
        return self._after_navigation()

    def jump(self, one_based_index: int) -> dict:
        self.session.jump(one_based_index)
        return self._after_navigation()

    def _after_navigation(self) -> dict:
        self.persist()
        self.prefetch_current()
        return self.status()


__all__ = ["ReaderService"]
