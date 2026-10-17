"""Optional, thread-local progress and cancellation for graphical frontends."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class Cancelled(Exception):
    """The user requested cancellation at a safe checkpoint."""


@dataclass
class Observer:
    progress: Callable[[float, float | None, str], None]
    checkpoint: Callable[[], None]


current: ContextVar[Observer | None] = ContextVar("installer_observer", default=None)


@contextmanager
def observe(observer: Observer):
    token = current.set(observer)
    try:
        yield
    finally:
        current.reset(token)


class Progress:
    """The small tqdm interface used by the existing download functions."""

    def __init__(self, observer, total=None, desc="", **kwargs):
        self.observer = observer
        self.total = total
        self.desc = desc
        self.n = 0
        self.update(0)

    def update(self, amount):
        self.observer.checkpoint()
        self.n += amount
        self.observer.progress(self.n, self.total, self.desc.strip())

    def reset(self, total=None):
        self.total, self.n = total, 0
        self.update(0)

    def set_description_str(self, desc, refresh=True):
        self.desc = desc
        if refresh:
            self.update(0)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
