"""pool_status is a report. The chassis (C6) can mount it as an HTTP route later."""

from __future__ import annotations

from agents.zenas.pool.store import Store


async def pool_status(store: Store, *, region: str | None = None) -> dict:
    rows = await store.status_rows()
    if region is not None:
        rows = [row for row in rows if row["region"] == region]
    # The national feed includes the existing minimum instruments. Their rows
    # retain their individual counts; the publisher row reports the full feed.
    national = next((row for row in rows if row["source_id"] == "laws-africa"), None)
    if national is not None:
        owned = [row for row in rows if row["region"] == "za" and row.get("key_name") == "LAWS_AFRICA_API_TOKEN"]
        national["documents"] = sum(row["documents"] for row in owned)
        national["versions"] = sum(row["versions"] for row in owned)
    return {"region": region, "sources": rows}
