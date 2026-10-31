"""A long load borrows a connection per statement, so the room's request pool never runs dry."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.pool import laws_africa  # noqa: E402
from agents.zenas.pool.api import begin_load, finish_loads  # noqa: E402
from agents.zenas.pool.catalogue import sources_for_region  # noqa: E402
from agents.zenas.pool.checkout import PerStatement  # noqa: E402
from agents.zenas.pool.store import MemoryStore  # noqa: E402


class FakeConn:
    def __init__(self, pool: "FakePool", n: int) -> None:
        self.pool, self.n = pool, n

    async def execute(self, sql, *args):
        self.pool.log.append((self.n, sql))
        return "OK"

    fetch = fetchrow = fetchval = executemany = execute

    @asynccontextmanager
    async def transaction(self, **_):
        self.pool.log.append((self.n, "BEGIN"))
        yield
        self.pool.log.append((self.n, "COMMIT"))


class FakePool:
    """Three connections, like the chassis pool. acquire() waits when all are out."""

    def __init__(self, size: int = 3) -> None:
        self.free = asyncio.Semaphore(size)
        self.out = 0
        self.peak = 0
        self.count = 0
        self.log: list[tuple[int, str]] = []
        self.autocommit: list[bool] = []

    @asynccontextmanager
    async def acquire(self, **kwargs):
        await self.free.acquire()
        self.count += 1
        self.out += 1
        self.peak = max(self.peak, self.out)
        self.autocommit.append(bool(kwargs.get("autocommit")))
        try:
            yield FakeConn(self, self.count)
        finally:
            self.out -= 1
            self.free.release()


@pytest.mark.asyncio
async def test_each_statement_returns_its_connection() -> None:
    pool = FakePool()
    conn = PerStatement(pool)
    await conn.execute("A")
    await conn.fetch("B")
    assert pool.out == 0
    assert [n for n, _ in pool.log] == [1, 2]


@pytest.mark.asyncio
async def test_a_transaction_keeps_one_connection_for_its_block() -> None:
    pool = FakePool()
    conn = PerStatement(pool)
    async with conn.transaction():
        await conn.execute("A")
        await conn.fetch("B")
        assert pool.out == 1
        async with conn.transaction():
            await conn.execute("C")
    assert pool.out == 0
    used = {n for n, sql in pool.log if sql in {"A", "B", "C"}}
    assert used == {1}


@pytest.mark.asyncio
async def test_the_advisory_lock_is_taken_and_released_on_one_session() -> None:
    pool = FakePool()

    class Holder:
        _conn = PerStatement(pool)

    async with laws_africa._run_lock(Holder()):
        assert pool.out == 1
    locks = [(n, sql) for n, sql in pool.log if "advisory" in sql]
    assert len(locks) == 2 and locks[0][0] == locks[1][0]
    assert pool.autocommit == [True]


@pytest.mark.asyncio
async def test_more_loads_than_connections_leave_requests_answering(monkeypatch) -> None:
    """Four loads at once on a three-connection pool: a request still gets a connection."""
    pool = FakePool(size=3)
    release = asyncio.Event()
    started = 0

    async def slow_load(store, source, region, **_):
        nonlocal started
        started += 1
        await store._conn.execute("load start")
        await release.wait()
        await store._conn.execute("load end")

    from agents.zenas.pool import api

    monkeypatch.setattr(api, "_load_one", slow_load)

    @asynccontextmanager
    async def request_store():
        async with pool.acquire() as held:
            store = MemoryStore()
            store._conn = held
            yield store

    @asynccontextmanager
    async def job_store():
        store = MemoryStore()
        store._conn = PerStatement(pool)
        yield store

    sources = [s for s in sources_for_region("za") if s.enabled][:4]
    assert len(sources) == 4
    for source in sources:
        out = await asyncio.wait_for(
            begin_load(request_store, source, "za", live=False, http_get=None, snapshots={},
                       open_job_store=job_store),
            timeout=2,
        )
        assert out["status"] == "running"
    for _ in range(50):
        if started == 4:
            break
        await asyncio.sleep(0.01)
    assert started == 4
    async with asyncio.timeout(2):
        async with request_store() as store:
            await store._conn.fetch("status")
    assert pool.out == 0 or pool.out < 3
    release.set()
    await finish_loads()
    assert pool.out == 0
