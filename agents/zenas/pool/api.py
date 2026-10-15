"""Pool status for this box, and a load of one source.

The routes are scoped to TANK_REGION. A source from the other region is
refused. Key values are never written into a response.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE, sources_for_region
from agents.zenas.pool.model import SourceSpec
from agents.zenas.pool.region import (
    RegionError,
    assert_box_region,
    assert_source_region,
    bind_store,
)
from agents.zenas.pool.status import pool_status
from agents.zenas.pool.store import Store, key_is_set


def _blank_row(source: SourceSpec) -> dict:
    return {
        "source_id": source.source_id,
        "name": source.name,
        "region": source.region,
        "jurisdiction": source.jurisdiction,
        "licence": source.licence,
        "terms_url": source.terms_url,
        "bulk_use": source.bulk_use,
        "enabled": source.enabled,
        "key_name": source.key_name,
        "key_required": source.key_required,
        "key_set": key_is_set(source.key_name),
        "documents": 0,
        "versions": 0,
        "last_update": None,
        "last_load": None,
        "last_status": "never",
        "detail": "",
        "errors": [],
        "link_only": False,
    }


async def status_for_box(store: Store, region: str | None) -> dict:
    """Every catalogue source for this region, with counts from the store.

    A missing or unknown region is refused. It does not come back as an empty list.
    """
    box = assert_box_region(region)
    report = await pool_status(store, region=box)
    by_id = {row["source_id"]: row for row in report["sources"]}
    sources = []
    for source in sources_for_region(box):
        sources.append(by_id.get(source.source_id) or _blank_row(source))
    return {"region": box, "sources": sources}


def _find(source_id: str) -> SourceSpec | None:
    for source in CATALOGUE:
        if source.source_id == source_id:
            return source
    return None


def _http_region(region: str | None) -> str:
    """A route answers 409 when the box region is missing or unknown."""
    try:
        return assert_box_region(region)
    except RegionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def create_app(
    store: Store,
    *,
    region: str | None,
    http_get=None,
    snapshots: dict[str, list[bytes]] | None = None,
    live: bool | None = None,
) -> FastAPI:
    """HTTP surface for the feeds switchboard. `region` is this box.

    Only `za` and `us-east` are accepted. Anything else raises before a route
    can answer, and each route still refuses a bad region with 409.
    """
    box = assert_box_region(region)
    bind_store(store, box)
    app = FastAPI()
    use_live = live if live is not None else snapshots is None

    @app.get("/pool/status")
    async def get_status() -> dict:
        home = _http_region(box)
        return await status_for_box(store, home)

    @app.post("/pool/sources/{source_id}/load")
    async def load_one(source_id: str) -> dict:
        home = _http_region(box)
        source = _find(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="unknown source")
        try:
            assert_source_region(source, home)
            await load_wave(
                store,
                region=home,
                only=[source],
                live=use_live,
                http_get=http_get,
                snapshots=snapshots,
            )
        except RegionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        report = await status_for_box(store, home)
        row = next(item for item in report["sources"] if item["source_id"] == source_id)
        return {"ok": True, "region": home, "source": row}

    return app
