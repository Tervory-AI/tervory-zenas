"""Cooperative deadlines scoped to a single nightly task; ordinary Loads are unchanged."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Callable


class DeadlineReached(asyncio.CancelledError):
    """Yield a nightly wave without being swallowed by an adapter's error handler."""


_active: ContextVar[tuple[float, Callable[[], float]] | None] = ContextVar("nightly_deadline", default=None)


def check_deadline() -> None:
    active = _active.get()
    if active is not None and active[1]() >= active[0]:
        raise DeadlineReached


@contextmanager
def deadline_scope(until: float, clock: Callable[[], float] = monotonic):
    parent = _active.get()
    if parent is not None:
        until = min(until, parent[0])
    token = _active.set((until, clock))
    try:
        check_deadline()
        yield
    finally:
        _active.reset(token)
