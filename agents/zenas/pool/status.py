"""pool_status is a report. The chassis (C6) can mount it as an HTTP route later."""

from __future__ import annotations

from agents.zenas.pool.store import Store


async def pool_status(store: Store, *, region: str | None = None) -> dict:
    rows = await store.status_rows()
    if region is not None:
        rows = [row for row in rows if row["region"] == region]
    return {"region": region, "sources": rows}
