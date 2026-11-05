"""Private room API. Tank is the only peer; no outside lookup happens here."""
from __future__ import annotations

import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

SKILLS = frozenset({"verify_business", "entity_links", "account_links", "site_risk", "business_risk"})
MAX_BYTES = 2_000_000


def reject() -> HTTPException:
    # Do not reflect names, credentials, or rejected input in errors.
    return HTTPException(400, "Use tenant-owned business evidence and a business lookup key.")


async def require_tank(request: Request) -> None:
    expected = os.environ.get("TANK_KEY", "")
    supplied = request.headers.get("X-Tank-Key", "")
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Tank authentication required.")


def identity(body: Any) -> tuple[UUID, str]:
    if not isinstance(body, dict):
        raise reject()
    try:
        tenant = UUID(body["tenant_id"])
        region = body["region"]
        if region not in {"us-east", "za"} or region != os.environ.get("TANK_REGION", "us-east"):
            raise reject()
        lookup = body["lookup"]
        if set(lookup) != {"kind", "value"}:
            raise reject()
        kind, value = lookup["kind"], lookup["value"]
        if not isinstance(value, str) or not 1 <= len(value) <= 253 or "@" in value or any(ord(c) < 32 for c in value):
            raise reject()
        if kind == "business_id":
            UUID(value)
        elif kind == "company_name":
            if not re.search(r"\b(incorporated|inc|llc|ltd|limited|corporation|corp|company|llp|pllc|pty|gmbh)\.?$", value.strip(), re.I) or re.search(r"\d{7,}", value):
                raise reject()
        elif kind in {"registration_number", "licence_number"}:
            if not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,30}:[A-Z0-9][A-Z0-9/.-]{1,70}", value, re.I):
                raise reject()
        elif kind == "domain":
            if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?", value, re.I):
                raise reject()
        else:
            raise reject()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise reject() from None
    return tenant, region


async def read_body(request: Request) -> dict:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Business evidence exceeds the request limit.")
    try:
        return json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise reject() from None


def attach(app: FastAPI) -> None:
    router = APIRouter(prefix="/verify", dependencies=[Depends(require_tank)])

    @router.post("/{skill}", status_code=201)
    async def verify(skill: str, request: Request) -> dict:
        if skill not in SKILLS:
            raise HTTPException(404, "Business skill not found.")
        body = await read_body(request)
        tenant, region = identity(body)
        # Approved skills only. No preview flag, model route, Base-room access,
        # Workspace credential, or automatic skill approval.
        try:
            result = await app.state.tank.run_skill(str(tenant), f"radix.{skill}", body)
        except Exception:
            raise HTTPException(502, "Tank could not run the approved business skill.") from None
        if not result.get("ok"):
            raise reject()
        if result.get("tenant_id") != str(tenant) or result.get("region") != region:
            raise HTTPException(502, "Tank returned an inconsistent business result.")
        run_id = uuid4()
        # Persist the validated result and its catalogue atomically, never raw
        # rejected input, keys, or Base contact fields. Every row has a tenant.
        async with app.state.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO agent_radix.business_runs (tenant_id, id, region, skill, result) VALUES ($1,$2,$3,$4,$5::jsonb)",
                    tenant, run_id, region, skill, json.dumps(result),
                )
                for source in result.get("source_catalogue", []):
                    await conn.execute(
                        "INSERT INTO agent_radix.business_sources (tenant_id, run_id, source_id, catalogue) VALUES ($1,$2,$3,$4::jsonb)",
                        tenant, run_id, source["id"], json.dumps(source),
                    )
        return {"id": str(run_id), **result}

    @router.get("/runs/{run_id}")
    async def read_run(run_id: UUID, tenant_id: UUID, request: Request) -> dict:
        region = os.environ.get("TANK_REGION", "us-east")
        async with app.state.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT result FROM agent_radix.business_runs WHERE tenant_id=$1 AND id=$2 AND region=$3",
                tenant_id, run_id, region,
            )
        if row is None:
            raise HTTPException(404, "Business result not found.")
        value = row["result"]
        return {"id": str(run_id), **(json.loads(value) if isinstance(value, str) else value)}

    app.include_router(router)
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with inner(app):
            async with app.state.pool.acquire() as conn:
                await conn.execute((Path(__file__).resolve().parents[1] / "schema.sql").read_text())
            yield

    app.router.lifespan_context = lifespan
