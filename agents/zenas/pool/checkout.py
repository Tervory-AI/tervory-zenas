"""A connection for long room jobs: one pooled checkout per statement.

A case load or a case conversion runs for minutes to hours. If it held one
pooled checkout for the whole job, the room's small request pool (three
connections) ran out after three loads: /pool/status, the self-test and every
further Load waited for a connection that never came back, and Tank answered
"Zenas room is unreachable". Each checkout is also one transaction, so the whole
job's rows sat uncommitted until the end and a cut load lost all of them.

``PerStatement`` borrows a connection for each statement and gives it back at
once, so every statement commits on its own and a job holds no connection
between statements. ``transaction()`` keeps one checkout for its block, so the
statements inside share one transaction. ``session()`` keeps one checkout with
no transaction, for a session-level advisory lock.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any


class PerStatement:
    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._held: ContextVar[Any] = ContextVar(f"zenas_job_conn_{id(self)}", default=None)

    async def _run(self, method: str, *args: Any, **kwargs: Any) -> Any:
        held = self._held.get()
        if held is not None:
            return await getattr(held, method)(*args, **kwargs)
        async with self._pool.acquire() as conn:
            return await getattr(conn, method)(*args, **kwargs)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("execute", *args, **kwargs)

    async def executemany(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("executemany", *args, **kwargs)

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("fetch", *args, **kwargs)

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("fetchrow", *args, **kwargs)

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("fetchval", *args, **kwargs)

    @asynccontextmanager
    async def transaction(self, **options: Any):
        """One checkout for the block; a nested block is a savepoint on it."""
        held = self._held.get()
        if held is not None:
            async with held.transaction(**options):
                yield
            return
        async with self._pool.acquire() as conn:
            token = self._held.set(conn)
            try:
                # A chassis checkout is already a transaction; this is then a savepoint.
                async with conn.transaction(**options):
                    yield
            finally:
                self._held.reset(token)

    @asynccontextmanager
    async def session(self):
        """One checkout with no transaction around it, held for the block."""
        try:
            checkout = self._pool.acquire(autocommit=True)
        except TypeError:
            checkout = self._pool.acquire()
        async with checkout as conn:
            yield conn

