"""Bridge the knowledge pool store into ladder retrieve / Ask answers.

`app.state.pool` is the asyncpg pool. Ladder retrieve expects an object with
`find_by_citation` and `search_text` (same shape as pool.store.MemoryStore /
PostgresStore). This wrapper acquires one connection per call.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from agents.zenas.pool.config import jurisdiction_for
from agents.zenas.pool.region import known_region
from agents.zenas.pool.store import PostgresStore


class AsyncpgLawPool:
    """Postgres-backed law pool used when Ask sends no passages."""

    requires_source_links = True

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

    async def source_metadata(self, chunks: list[Any], *, jurisdiction: str) -> dict[tuple[str, str], dict[str, str]]:
        """Resolve links from the exact stored document versions, never the request or model."""
        if not known_region(self.home_region) or jurisdiction != jurisdiction_for(self.home_region):
            return {}
        versions = list({chunk.version_id for chunk in chunks})
        if not versions:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""SELECT d.document_id,d.version_id,d.url,d.title,d.kind,d.level,d.status
                FROM zenas_pool.documents d JOIN zenas_pool.sources s ON s.source_id=d.source_id
                WHERE d.jurisdiction=$1 AND s.region=$2 AND d.version_id=ANY($3::text[])""",
                jurisdiction, self.home_region, versions)
        return {(row["document_id"], row["version_id"]): {key: row[key] for key in ("url", "title", "kind", "level", "status")}
                for row in rows}


def bind_law_pool(application: Any, *, home_region: str) -> AsyncpgLawPool:
    """Attach law_pool to app.state from the chassis asyncpg pool."""
    bridge = AsyncpgLawPool(application.state.pool, home_region=home_region)
    application.state.law_pool = bridge
    return bridge
