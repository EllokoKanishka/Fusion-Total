"""Cooperative priority at native-inference boundaries, without dropping work."""

from __future__ import annotations

import heapq
import threading
import time
from contextlib import contextmanager


class PriorityGate:
    def __init__(self):
        self.condition = threading.Condition()
        self.waiting: list[tuple[int, int]] = []
        self.active = False
        self.sequence = 0

    @staticmethod
    def check(cancel_event=None):
        if time.monotonic() >= getattr(cancel_event, "deadline", float("inf")):
            raise TimeoutError("deadline_exceeded")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("cancelled")

    @contextmanager
    def slot(self, priority=0, cancel_event=None):
        with self.condition:
            self.sequence += 1
            ticket = (int(priority), self.sequence)
            heapq.heappush(self.waiting, ticket)
            try:
                while True:
                    self.check(cancel_event)
                    if not self.active and self.waiting[0] == ticket:
                        heapq.heappop(self.waiting)
                        self.active = True
                        break
                    self.condition.wait(0.05)
            except BaseException:
                self.waiting.remove(ticket)
                heapq.heapify(self.waiting)
                self.condition.notify_all()
                raise
        try:
            yield
            self.check(cancel_event)
        finally:
            with self.condition:
                self.active = False
                self.condition.notify_all()

    def iterate(self, iterable, *, priority=0, cancel_event=None):
        iterator = iter(iterable)
        try:
            while True:
                with self.slot(priority, cancel_event):
                    try:
                        item = next(iterator)
                    except StopIteration:
                        return
                yield item
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
