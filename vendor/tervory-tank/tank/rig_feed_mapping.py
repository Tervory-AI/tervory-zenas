"""Server-recorded Rig previews and confirmations; never store feed values or keys."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool
    from access import FeedImport, FeedConnection
    from command import Actor

from fastapi import HTTPException

SETTING_FIELDS = {"format", "columns", "currency", "country", "price_basis", "records_path"}
OPTIONAL_COLUMNS = {"quantity", "active", "url", "gtin", "family", "fits_sku", "fits_family", "fit_evidence"}
CONFIRM_FIRST = "Review and confirm the field mapping before saving this feed."


def context(body: FeedImport | FeedConnection, provider: str, site: str) -> dict[str, Any]:
    settings = body.model_dump(include=SETTING_FIELDS - {"columns"})
    if hasattr(body, "contents"):
        source = {"contents": body.contents.get_secret_value(), "encoding": body.encoding}
    else:
        source = {"endpoint": body.endpoint, "auth_mode": body.auth_mode}
    return {"provider": provider, "site": site, "settings": settings,
            "source": hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()}


async def preview(pool: Pool, actor: Actor, body: FeedImport | FeedConnection, provider: str, site: str, result: dict[str, Any]) -> dict[str, Any]:
    preview_id = uuid.uuid4()
    value = {**context(body, provider, site), "fields": result["fields"], "required": result["required"]}
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM tank.rig_feed_previews WHERE tenant_id=$1 AND expires_at<=now()", actor.tenant_id)
            await conn.execute("""INSERT INTO tank.rig_feed_previews(tenant_id,id,user_id,preview)
                VALUES($1,$2,$3,$4::jsonb)""", actor.tenant_id, preview_id, actor.user_id, json.dumps(value))
    return {**result, "preview_id": str(preview_id)}


async def confirm(pool: Pool, actor: Actor, body: FeedImport | FeedConnection, provider: str, site: str, preview_id: str | None,
                  receipt: Callable[..., Awaitable[None]]) -> dict[str, bool]:
    try:
        preview_id = uuid.UUID(str(preview_id))
    except ValueError:
        raise HTTPException(422, CONFIRM_FIRST) from None
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchval("""SELECT preview FROM tank.rig_feed_previews
                WHERE tenant_id=$1 AND id=$2 AND user_id=$3 AND expires_at>now() FOR UPDATE""",
                actor.tenant_id, preview_id, actor.user_id)
            if not row:
                raise HTTPException(422, CONFIRM_FIRST)
            saved = json.loads(row) if isinstance(row, str) else row
            expected = context(body, provider, site)
            allowed = set(saved["required"]) | OPTIONAL_COLUMNS
            if (any(saved[key] != value for key, value in expected.items())
                    or not set(saved["required"]).issubset(body.columns)
                    or any(key not in allowed or value not in saved["fields"] for key, value in body.columns.items())):
                raise HTTPException(422, "The feed or fields changed. Preview and confirm the mapping again.")
            settings = {**body.model_dump(include=SETTING_FIELDS), "confirmation_version": 1}
            # Connections bind the saved mapping to the reviewed endpoint. Credentials stay in ACCESS's vault.
            if hasattr(body, "endpoint"):
                settings["connection_source"] = expected["source"]
            await conn.execute("""INSERT INTO tank.rig_feed_settings(tenant_id,distributor,site,settings)
                VALUES($1,$2,$3,$4::jsonb) ON CONFLICT(tenant_id,distributor,site)
                DO UPDATE SET settings=EXCLUDED.settings""", actor.tenant_id, provider, site, json.dumps(settings))
            await receipt(conn, actor.tenant_id, "rig", site, actor.user_id, "feed_mapping_confirmed")
            await conn.execute("DELETE FROM tank.rig_feed_previews WHERE tenant_id=$1 AND id=$2", actor.tenant_id, preview_id)
    return {"saved": True}


async def confirmed(pool: Pool, actor: Actor, body: FeedImport | FeedConnection, provider: str, site: str) -> FeedImport | FeedConnection:
    async with pool.acquire() as conn:
        row = await conn.fetchval("SELECT settings FROM tank.rig_feed_settings WHERE tenant_id=$1 AND distributor=$2 AND site=$3",
                                  actor.tenant_id, provider, site)
    settings = (json.loads(row) if isinstance(row, str) else row) or {}
    if settings.get("confirmation_version") != 1:
        raise HTTPException(422, CONFIRM_FIRST)
    supplied = body.model_dump(include=SETTING_FIELDS, exclude_unset=True)
    # Empty columns mean reuse the saved map; explicitly changed declarations require another preview.
    if (any(value != settings.get(key) for key, value in supplied.items() if key != "columns" or value)
            or (hasattr(body, "endpoint") and settings.get("connection_source") != context(body, provider, site)["source"])):
        raise HTTPException(422, "The feed settings changed. Preview and confirm the mapping again.")
    return type(body).model_validate({**body.model_dump(), **{key: settings[key] for key in SETTING_FIELDS}})
