"""Private Market Lab reads and alert delivery through the existing Tank corridor."""
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from core.evidence import overview
from core.intel import PLATFORM, latest
from core.warnings import listing
from core.warning_bridge import call
from feeds.catalog import sources
from feeds.keys import key_name_for


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    report_id: UUID | None = None


def build_router(*, key_ok, ledger_context, engine):
    router = APIRouter()

    @router.post("/market/ml3/{operation}")
    async def read(operation: str, body: Read, x_tank_key: str = Header(default="")):
        if not key_ok(x_tank_key):
            raise HTTPException(401, "Room key required")
        tenant = str(body.tenant_id)
        async with ledger_context() as ledger:
            if operation == "intel":
                if tenant != PLATFORM:
                    raise HTTPException(404, "Not found")
                result = await latest(ledger)
                if result is None:
                    raise HTTPException(503, "Market Lab is not connected")
                return result
            if operation == "warnings":
                result = await listing(ledger, tenant)
                dates = {str(r["id"]): r["created_at"] for r in await ledger.conn.fetch(
                    "SELECT id,created_at FROM agent_market_lab.warnings WHERE tenant_id=$1 ORDER BY created_at DESC,id LIMIT 50", body.tenant_id)}
                return {**result, "warnings": [{**w, "created_at": dates[w["id"]]} for w in result["warnings"]]}
            if operation == "briefs":
                if body.report_id is not None:
                    from core.brief_detail import detail
                    report = await detail(ledger, tenant, str(body.report_id), engine=engine)
                    if report is None:
                        raise HTTPException(404, "Not found")
                    return {"briefs": [report]}
                return {"briefs": await ledger.briefs_for(tenant)}
            if operation == "patterns":
                from core.jobs import load_engine
                from core.patterns import listing as patterns
                return await patterns(ledger.memory, tenant, load_engine(engine).warning_plan())
            if operation == "gate":
                from core.predictions import gate
                return gate(await ledger.predictions_for(tenant), tenant)
            if operation == "predictions":
                return {"predictions": await ledger.predictions_for(tenant)}
            if operation in {"calibration", "calibration.curve"}:
                rows = await ledger.predictions_for(tenant)
                return {**(await ledger.calibration_for(tenant)),
                        "hits": sum(r.get("outcome") == "hit" for r in rows),
                        "misses": sum(r.get("outcome") == "miss" for r in rows)}
            if operation == "feeds":
                result = await overview(ledger, tenant, engine)
                names = {s.id: key_name_for(s) for s in sources(engine)}
                # Bound-key URLs and configuration values never leave this read.
                feeds = [{**{k: f.get(k) for k in ("id", "name", "state", "rows", "checked_at", "document_id")},
                          "key_name": names.get(f["id"], "")} for f in result["feeds"]]
                from core.intel import SOURCES
                bundle = await latest(ledger)
                for source in SOURCES:
                    feeds.append({"id": source.id, "name": source.name, "state": "on" if bundle else "off",
                                  "rows": sum(source.id in i["source_ids"] for i in bundle["indicators"]) if bundle else 0,
                                  "checked_at": bundle["published_at"] if bundle else None, "key_name": ""})
                return {"feeds": feeds}
            if operation == "alerts":
                from chassis.db import ensure_schedules
                from core.jobs import load_engine
                # Confirm also enables the existing tenant refresh/resolution jobs;
                # an account using only chats must not depend on visiting a page.
                schedules = [{**schedule, "tenant_id": tenant} for schedule in load_engine(engine).schedules()]
                schedules.append({"tenant_id": tenant, "cron": "* * * * *", "kind": "alerts", "input": {}})
                await ensure_schedules(ledger.conn, "agent_market_lab", schedules)
                return {"status": "queued"}
        raise HTTPException(404, "Not found")
    return router


class MailStore:
    async def pending_mail(self, tenant, clock):
        return (await call("alerts/pending", {"tenant_id": tenant}))["pending"]

    async def claim_mail(self, tenant, ident, clock):
        return (await call("alerts/claim", {"tenant_id": tenant, "warning_id": ident})).get("record")

    async def finish_mail(self, tenant, ident, status, clock):
        return await call("alerts/finish", {"tenant_id": tenant, "warning_id": ident, "status": status})


async def deliver(tenant: str, *, ledger=None) -> dict:
    from agents.tiger.alert_email import deliver_pending
    mail = await deliver_pending(MailStore(), tenant, namespace="market_lab")
    hooks = await call("alerts/webhooks", {"tenant_id": tenant})
    result = {"mail": mail, "webhooks": hooks}
    if ledger is not None:
        from core.briefs import deliver as deliver_briefs
        result["briefs"] = await deliver_briefs(ledger, tenant)
    return result
