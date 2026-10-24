"""Bridge the knowledge pool store into ladder retrieve / Ask answers.

`app.state.pool` is the asyncpg pool. Ladder retrieve expects an object with
`find_by_citation` and `search_text` (same shape as pool.store.MemoryStore /
PostgresStore). This wrapper acquires one connection per call.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from agents.zenas.pool.store import PostgresStore


class AsyncpgLawPool:
    """Postgres-backed law pool used when Ask sends no passages."""

    def __init__(self, pool: Any, *, home_region: str) -> None:
        self._pool = pool
        self.home_region = home_region

    async def find_by_citation(
        self,
        citation: str,
        *,
        jurisdiction: str,
        as_at: date | None = None,
    ) -> list[Any]:
        async with self._pool.acquire() as conn:
            store = PostgresStore(conn, home_region=self.home_region)
            return await store.find_by_citation(citation, jurisdiction=jurisdiction, as_at=as_at)

    async def search_text(
        self,
        query: str,
        *,
        jurisdiction: str,
        as_at: date | None = None,
        level: str | None = None,
        kind: str | None = None,
        limit: int = 8,
    ) -> list[Any]:
        async with self._pool.acquire() as conn:
            store = PostgresStore(conn, home_region=self.home_region)
            return await store.search_text(
                query,
                jurisdiction=jurisdiction,
                as_at=as_at,
                level=level,
                kind=kind,
                limit=limit,
            )


def bind_law_pool(application: Any, *, home_region: str) -> AsyncpgLawPool:
    """Attach law_pool to app.state from the chassis asyncpg pool."""
    bridge = AsyncpgLawPool(application.state.pool, home_region=home_region)
    application.state.law_pool = bridge
    return bridge
