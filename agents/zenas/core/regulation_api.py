"""Internal watch routes through Tank. No page or new client wording."""

from __future__ import annotations

import hmac
import os
from uuid import UUID

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from agents.zenas.core.regulation_store import PgWatches
from agents.zenas.core.regulations import add_watch
from agents.zenas.core.service import _use_home


class WatchIn(BaseModel):
    tenant_id: UUID
    home_region: str
    source: str = Field(max_length=40)
    topic: str = Field(min_length=1, max_length=200)
    options: dict[str, str] = Field(default_factory=dict)


class EnabledIn(BaseModel):
    tenant_id: UUID
    home_region: str
    enabled: bool


def public_watch(row):
    return {k: v for k, v in row.items() if k not in {"seen", "tenant_id"}}


def register_routes(app, pool_store, tenant_store, box_region):
    async def check(tenant_id, home_region, key):
        expected = os.environ.get("TANK_KEY", "")
        if not expected or not hmac.compare_digest(key.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="room key required")
        region = box_region()
        store = tenant_store()
        await store.apply_schema()
        _, refusal = await _use_home(
            store, tenant_id=str(tenant_id), home_region=home_region, box_region=region
        )
        if refusal:
            raise HTTPException(status_code=403, detail=refusal["reason"])
        return region

    @app.post("/regulations/watches", status_code=201)
    async def create(body: WatchIn, x_tank_key: str = Header(default="")):
        region = await check(body.tenant_id, body.home_region, x_tank_key)
        async with pool_store() as law:
            store = PgWatches(law._conn)
            await store.apply_schema()
            try:
                row = await add_watch(
                    store,
                    tenant_id=str(body.tenant_id),
                    region=region,
                    source=body.source,
                    topic=body.topic,
                    options=body.options,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            return {"ok": True, "watch": public_watch(row)}

    @app.get("/regulations/watches")
    async def listing(
        tenant_id: UUID, home_region: str, x_tank_key: str = Header(default="")
    ):
        region = await check(tenant_id, home_region, x_tank_key)
        async with pool_store() as law:
            store = PgWatches(law._conn)
            await store.apply_schema()
            return {
                "ok": True,
                "watches": [
                    public_watch(w) for w in await store.watches(str(tenant_id), region)
                ],
            }

    @app.post("/regulations/watches/{watch_id}")
    async def enabled(
        watch_id: UUID, body: EnabledIn, x_tank_key: str = Header(default="")
    ):
        await check(body.tenant_id, body.home_region, x_tank_key)
        async with pool_store() as law:
            store = PgWatches(law._conn)
            await store.apply_schema()
            row = await store.set_enabled(
                str(body.tenant_id), str(watch_id), body.enabled
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Not in scope.")
            return {"ok": True, "watch": public_watch(row)}

    @app.get("/regulations/changes")
    async def changes(
        tenant_id: UUID, home_region: str, x_tank_key: str = Header(default="")
    ):
        await check(tenant_id, home_region, x_tank_key)
        async with pool_store() as law:
            store = PgWatches(law._conn)
            await store.apply_schema()
            rows = await store.changes(str(tenant_id))
            return {
                "ok": True,
                "changes": [
                    {
                        "id": r["id"],
                        "watch_id": r["watch_id"],
                        "title": r["document"]["title"],
                        "link": r["document"]["link"],
                        "date": r["document"]["date"],
                        "summary": r["summary"],
                        "mail_state": r["mail_state"],
                        "attribution": r["document"]["attribution"],
                    }
                    for r in rows
                ],
            }
