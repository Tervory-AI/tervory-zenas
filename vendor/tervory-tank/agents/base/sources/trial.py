"""Persist trial candidates without changing Base's serving profile."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sources.context_dev import BRAND_CREDITS, ContextDev
from sources.vendor_ledger import PLATFORM_TENANT, SCHEMA, TankVendorLedger, decoded


async def run_trial(pool: Any, http: Any, tenant_id: str, run_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    tenant, run = uuid.UUID(tenant_id), uuid.UUID(run_id)
    if tenant != uuid.UUID(PLATFORM_TENANT):
        raise ValueError("company trials require the platform tenant")
    lock = f"context-trial:{tenant}:{run}"
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
        if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1,0))", lock):
            raise ValueError("this trial is already running")
        try:
            task = await conn.fetchval(
                "SELECT 1 FROM agent_base.tasks WHERE tenant_id=$1 AND id=$2 AND status='done'", tenant, run,
            )
            if task != 1:
                raise ValueError("a completed batch in this tenant is required")
            for record in records:
                present = await conn.fetchval(
                    "SELECT 1 FROM agent_base.records WHERE tenant_id=$1 AND task_id=$2 AND id=$3 AND status='done'",
                    tenant, run, uuid.UUID(record["record_id"]),
                )
                if present != 1:
                    raise ValueError("record not found in this completed batch")
            ledger = TankVendorLedger(conn, http, tenant_id=tenant_id, run_id=run_id)
            await ledger.recover()
            adapter = ContextDev(http, ledger, tenant_id=tenant_id, run_id=run_id)
            results = []
            records_sent = 0
            uncertain = await conn.fetchval(
                "SELECT 1 FROM agent_base.context_dev_calls WHERE tenant_id=$1 AND run_id=$2 "
                "AND (result->'credits'='null'::jsonb OR (result->>'credits')::numeric <= 0 "
                "OR (result->>'credits')::numeric > reserved_credits) LIMIT 1",
                tenant, run,
            )
            adapter.stopped = uncertain == 1
            for record in records:
                previous = await conn.fetchval(
                    "SELECT result FROM agent_base.context_dev_calls WHERE tenant_id=$1 AND run_id=$2 AND record_id=$3",
                    tenant, run, uuid.UUID(record["record_id"]),
                )
                if previous is not None:
                    result = decoded(previous)
                else:
                    identity = {key: record[key] for key in ("name", "domain", "url") if record.get(key)}
                    result = await adapter.lookup(**identity, record_id=record["record_id"])
                    records_sent += result["status"] in ("candidate", "failed", "transport_error", "unknown")
                checked = record.get("checked_answers")
                if checked is not None:
                    await conn.execute(
                        "UPDATE agent_base.records SET passes=jsonb_set(passes,'{context_dev_checked}',$4::jsonb) "
                        "WHERE tenant_id=$1 AND task_id=$2 AND id=$3",
                        tenant, run, uuid.UUID(record["record_id"]),
                        json.dumps({"checked": bool(checked), "expected": checked, "label_source": record.get("label_source")}),
                    )
                results.append({"record_id": record["record_id"], **result})
                if result["status"] == "credit_limit":
                    break
            spent = await conn.fetchval(
                "SELECT coalesce(sum(reserved_credits),0) FROM agent_base.context_dev_calls "
                "WHERE tenant_id=$1 AND run_id=$2", tenant, run,
            )
            if results and (results[-1]["status"] == "credit_limit" or spent + BRAND_CREDITS > adapter.max_credits):
                results[-1]["credit_cap_reached"] = True
                await conn.execute(
                    "UPDATE agent_base.records SET passes=jsonb_set(passes,'{context_dev}',$4::jsonb) "
                    "WHERE tenant_id=$1 AND task_id=$2 AND id=$3",
                    tenant, run, uuid.UUID(results[-1]["record_id"]),
                    json.dumps({key: value for key, value in results[-1].items() if key != "record_id"}),
                )
                return {"run_id": run_id, "results": results, "records_sent": records_sent, "status": "credit_limit", "message": "credit cap reached"}
            return {
                "run_id": run_id, "results": results, "records_sent": records_sent,
                **({"message": "Context.dev trial key is not configured."}
                   if results and results[-1]["status"] == "not_configured" else {}),
            }
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", lock)
