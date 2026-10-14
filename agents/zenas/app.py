"""Zenas — drafts and research for a licensed lawyer to review. No criminal law."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

_HERE = Path(__file__).resolve().parent
if (_HERE / "agents" / "zenas" / "pool").is_dir():
    _ROOT = _HERE
elif (_HERE.parent.parent / "agents" / "zenas" / "pool").is_dir():
    _ROOT = _HERE.parent.parent
else:
    _ROOT = _HERE
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_CHASSIS = _ROOT / "agents" / "_chassis"
if _CHASSIS.is_dir() and str(_CHASSIS) not in sys.path:
    sys.path.insert(0, str(_CHASSIS))

from agents.zenas.pool.catalogue import CATALOGUE  # noqa: E402
from chassis.app import create_app  # noqa: E402
from chassis.jobs import JobContext  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agents.zenas.core.matters import (  # noqa: E402
    open_matter,
    record_consent,
    record_review,
    upload_document,
)
from agents.zenas.core.seal import key_from_env  # noqa: E402
from agents.zenas.core.store import PgStore  # noqa: E402

AGENT = os.environ.get("AGENT_NAME", "zenas")
HEARTBEAT_TENANT = "00000000-0000-0000-0000-000000000001"

log = logging.getLogger("agent")


async def ping(ctx: JobContext) -> dict:
    result: dict = {"pong": True, "agent": ctx.agent}
    if ctx.input.get("heartbeat"):
        try:
            saved = await ctx.memory.remember(
                ctx.tenant_id,
                f"{ctx.agent} heartbeat",
                "heartbeat",
                "schedule:*/5",
            )
            result["heartbeats_seen"] = saved.get("seen")
        except Exception as exc:  # noqa: BLE001 — class name only, never the message
            log.warning("heartbeat memory failed (%s)", type(exc).__name__)
            result["memory"] = "unreachable"
    return result


def _box_region() -> str:
    return os.environ.get("TANK_REGION", "us-east")


def _store() -> PgStore:
    return PgStore(app.state.pool)


def _fail(result: dict[str, Any]) -> None:
    if result.get("ok"):
        return
    reason = str(result.get("reason") or "refused")
    status = 403 if "region" in reason else 422
    raise HTTPException(status_code=status, detail=reason)


async def open_matter_job(ctx: JobContext) -> dict:
    store = _store()
    await store.apply_schema()
    result = await open_matter(
        store,
        ctx.tank,
        tenant_id=ctx.tenant_id,
        home_region=str(ctx.input.get("home_region") or ""),
        jurisdiction=str(ctx.input.get("jurisdiction") or ""),
        title=str(ctx.input.get("title") or ""),
        created_by=str(ctx.input.get("created_by") or ""),
        user_id=str(ctx.input.get("user_id") or ""),
        box_region=_box_region(),
        parties=ctx.input.get("parties") if isinstance(ctx.input.get("parties"), list) else [],
        key=key_from_env(),
    )
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason")}
    matter = result["matter"]
    return {"ok": True, "matter_id": matter["id"], "area": matter["area"]}


async def upload_job(ctx: JobContext) -> dict:
    store = _store()
    await store.apply_schema()
    result = await upload_document(
        store,
        ctx.tank,
        tenant_id=ctx.tenant_id,
        matter_id=str(ctx.input.get("matter_id") or ""),
        kind=str(ctx.input.get("kind") or ""),
        body=str(ctx.input.get("body") or ""),
        box_region=_box_region(),
        home_region=str(ctx.input.get("home_region") or ""),
        key=key_from_env(),
        sources=ctx.input.get("sources") if isinstance(ctx.input.get("sources"), list) else [],
    )
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason")}
    document = result["document"]
    return {"ok": True, "document_id": document["id"], "version": document["version"]}


app = create_app(
    agent=AGENT,
    handlers={"ping": ping, "open_matter": open_matter_job, "upload": upload_job},
    schedules=[
        {
            "tenant_id": HEARTBEAT_TENANT,
            "cron": "*/5 * * * *",
            "kind": "ping",
            "input": {"heartbeat": True},
        }
    ],
)


class ConsentIn(BaseModel):
    tenant_id: UUID
    home_region: str
    user_id: str
    jurisdiction: str
    wording: str
    ip: str


class MatterIn(BaseModel):
    tenant_id: UUID
    home_region: str
    jurisdiction: str
    title: str
    created_by: str
    user_id: str = ""
    parties: list[Any] = Field(default_factory=list)


class DocumentIn(BaseModel):
    tenant_id: UUID
    home_region: str
    kind: str
    body: str
    sources: list[Any] = Field(default_factory=list)


class ReviewIn(BaseModel):
    tenant_id: UUID
    reviewer: str
    jurisdiction: str
    admission_no: str
    decision: str
    changes: str = ""


@app.get("/pool")
async def pool_import() -> dict[str, Any]:
    return {"imported": True, "sources": len(CATALOGUE)}


@app.post("/consents", status_code=201)
async def post_consent(body: ConsentIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await record_consent(
        store,
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        user_id=body.user_id,
        jurisdiction=body.jurisdiction,
        wording=body.wording,
        ip=body.ip,
        box_region=_box_region(),
    )
    _fail(result)
    return result


@app.post("/matters", status_code=201)
async def post_matter(body: MatterIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await open_matter(
        store,
        app.state.tank,
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        jurisdiction=body.jurisdiction,
        title=body.title,
        created_by=body.created_by,
        user_id=body.user_id,
        box_region=_box_region(),
        parties=body.parties,
        key=key_from_env(),
    )
    _fail(result)
    return result


@app.post("/matters/{matter_id}/documents", status_code=201)
async def post_document(matter_id: UUID, body: DocumentIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await upload_document(
        store,
        app.state.tank,
        tenant_id=str(body.tenant_id),
        matter_id=str(matter_id),
        kind=body.kind,
        body=body.body,
        box_region=_box_region(),
        home_region=body.home_region,
        key=key_from_env(),
        sources=body.sources,
    )
    _fail(result)
    public = dict(result)
    public.pop("prompt", None)
    return public


@app.post("/documents/{document_id}/reviews", status_code=201)
async def post_review(document_id: UUID, body: ReviewIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await record_review(
        store,
        tenant_id=str(body.tenant_id),
        document_id=str(document_id),
        reviewer=body.reviewer,
        jurisdiction=body.jurisdiction,
        admission_no=body.admission_no,
        decision=body.decision,
        changes=body.changes,
    )
    _fail(result)
    return result
