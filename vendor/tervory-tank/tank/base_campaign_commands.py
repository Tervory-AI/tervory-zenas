"""Existing campaign execution and evidence reports through Command cards."""
import csv
import io
import json
from collections import defaultdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from base_audit import unpack
from base_evidence import public_url
from base_lists import receipt
from base_metrics import csv_text, export_records, obj, selected
from base_product import dimensions
from base_repository import BaseStore


class StartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=50)
    states: list[str] = Field(min_length=1, max_length=60)
    types: list[str] = Field(default_factory=list, max_length=15)
    include_collectors: bool = False


class CampaignArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign_id: UUID


def report_csv(records, audit, scores):
    reader = csv.DictReader(io.StringIO(export_records(records).lstrip("\ufeff")))
    measurement = audit["measurement"] if audit else {}
    interval = measurement.get("confidence_interval") or {}
    metadata = {"audit_id": str(audit["id"]) if audit else None,
                "audit_accuracy": measurement.get("accuracy"), "audit_interval_lower": interval.get("lower"),
                "audit_interval_upper": interval.get("upper"), "audit_confidence_level": measurement.get("confidence_level"),
                "audit_sample_size": measurement.get("sample_size"), "audit_reviewed_at": audit.get("reviewed_at") if audit else None,
                "source_scores": json.dumps(scores, default=str, sort_keys=True)}
    identities = ("licence_key", "state", "licence_type", "name")
    columns = [f"{key}_{part}" for key in identities for part in ("source_url", "checked_at")]
    output = []
    for record, cells in zip(records, reader):
        reference = obj(record.get("provenance_raw") or {}).get("_base_reference") or {}
        source, checked = public_url(reference.get("source_url")), reference.get("checked_at")
        evidence = {}
        for key in identities:
            if source and checked and reference.get(key) == record.get(key):
                evidence.update({key + "_source_url": source, key + "_checked_at": checked})
        output.append({**cells, **evidence, **metadata})
    return csv_text(output, [*reader.fieldnames, *columns, *metadata])


async def snapshot(pool, tenant, campaign):
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            owner = await conn.fetchrow("SELECT id,name FROM tank.base_campaigns WHERE tenant_id=$1 AND id=$2", tenant, campaign)
            if owner is None:
                raise HTTPException(404, "Not found")
            states = await conn.fetch("SELECT state,status,records_count FROM tank.base_campaign_states WHERE tenant_id=$1 AND campaign_id=$2 ORDER BY ordinal", tenant, campaign)
            rows = await conn.fetch("""SELECT r.*,CASE WHEN a.updated_at<=r.checked_at THEN a.raw END AS provenance_raw FROM tank.base_record_results r
                JOIN tank.base_campaign_states s ON s.tenant_id=$1 AND s.campaign_id=$2
                    AND s.task_id=r.task_id AND s.batch=r.batch
                LEFT JOIN agent_base.records a ON a.tenant_id=$1 AND a.id=r.record_id AND a.task_id=r.task_id AND a.batch=r.batch
                WHERE r.tenant_id=$1 ORDER BY r.checked_at,r.record_id""", tenant, campaign)
            audit = await conn.fetchrow("SELECT * FROM tank.base_audits WHERE tenant_id=$1 AND campaign_id=$2 AND reviewed_at IS NOT NULL ORDER BY reviewed_at DESC,id DESC LIMIT 1", tenant, campaign)
            scores = await __import__("base_reliability").read(conn)
    return dict(owner), [dict(row) for row in states], [{**dict(row), "fields": obj(row["fields"])} for row in rows], unpack(audit) if audit else None, scores


def build_router(rt):
    from command import Actor, identity, require_tool
    from trigger_commands import confirmation
    router = APIRouter(prefix="/v1/command/base/campaigns", tags=["base"])

    async def result(actor, tool, data):
        return {**data, "cost_usd": 0, "ledger_id": await receipt(actor.tenant_id, tool)}

    @router.post("/start")
    async def start(args: StartArgs, actor: Annotated[Actor, Depends(identity)]):
        require_tool(actor, "base.campaign.start")
        request_id = confirmation(actor, "base.campaign.start", args.model_dump(mode="json", exclude_none=True))
        if rt.box_region() != "us-east":
            raise HTTPException(403, "This public reference belongs to the US workspace.")
        states, kinds = dimensions(args.states, args.types)
        store = BaseStore(rt)
        file = await store.latest_file()
        if not file:
            raise HTTPException(503, "The platform reference is not available yet.")
        rows = [row for row in await store.reference(file["id"], states=states, types=kinds) if selected(row, states, kinds, args.include_collectors)]
        if not rows:
            raise HTTPException(422, "No reference licences match this selection.")
        groups = defaultdict(list)
        for row in rows:
            groups[row["state"]].append(row)
        campaign = await store.create_campaign(actor.tenant_id, request_id, args.name, actor.user_id, file["id"],
            {"states": states, "types": kinds, "include_collectors": args.include_collectors}, groups)
        await store.dispatch(actor.tenant_id)
        return await result(actor, "base.campaign.start", {"campaign_id": str(campaign), "total": len(rows),
            "summary": json.dumps({"campaign_id": str(campaign), "states": sorted(groups)})})

    @router.get("/show")
    async def show(args: Annotated[CampaignArgs, Depends()], actor: Annotated[Actor, Depends(identity)]):
        require_tool(actor, "base.campaign.show")
        owner, states, records, audit, scores = await snapshot(rt.pool(), actor.tenant_id, args.campaign_id)
        return await result(actor, "base.campaign.show", {"name": owner["name"], "total": len(records),
            "summary": json.dumps({"campaign_id": str(args.campaign_id), "states": states,
                "verified_records": sum(any((row["fields"].get(key) or {}).get("verified") for key in ("website", "phone", "email")) for row in records),
                "measurement": audit["measurement"] if audit else None, "source_scores": scores}, default=str)})

    @router.get("/export")
    async def export(args: Annotated[CampaignArgs, Depends()], actor: Annotated[Actor, Depends(identity)]):
        require_tool(actor, "base.campaign.csv")
        _, _, records, _, _ = await snapshot(rt.pool(), actor.tenant_id, args.campaign_id)
        return await result(actor, "base.campaign.csv", {"total": len(records),
            "url": "https://app.tervory.com/base/download?campaign_id=" + str(args.campaign_id)})

    @router.get("/csv")
    async def download(args: Annotated[CampaignArgs, Depends()], actor: Annotated[Actor, Depends(identity)]):
        require_tool(actor, "base.campaign.csv")
        _, _, records, audit, scores = await snapshot(rt.pool(), actor.tenant_id, args.campaign_id)
        return Response(report_csv(records, audit, scores), media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": 'attachment; filename="base-campaign.csv"', "Cache-Control": "private, no-store"})

    return router
