"""Durable Base credit reservations and an idempotent Tank delivery outbox."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from sources.context_dev import CreditLimit

PLATFORM_TENANT = "00000000-0000-0000-0000-000000000001"
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_base.context_dev_calls (
    call_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    run_id uuid NOT NULL,
    record_id uuid NOT NULL,
    region text NOT NULL,
    reserved_credits integer NOT NULL,
    result jsonb,
    posted boolean NOT NULL DEFAULT false,
    UNIQUE (tenant_id, run_id, record_id)
);
ALTER TABLE agent_base.context_dev_calls ADD COLUMN IF NOT EXISTS paid_step_approval_id uuid;
"""


def decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class TankVendorLedger:
    def __init__(self, conn: Any, http: Any, *, tenant_id: str, run_id: str) -> None:
        self.conn, self.http = conn, http
        self.tenant_id, self.run_id = uuid.UUID(tenant_id), uuid.UUID(run_id)
        if self.tenant_id != uuid.UUID(PLATFORM_TENANT):
            raise ValueError("company trials require the platform tenant")

    async def reserve(self, event: dict[str, Any], max_credits: int) -> str:
        async with self.conn.transaction():
            await self.conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"context-budget:{self.tenant_id}:{self.run_id}",
            )
            spent = await self.conn.fetchval(
                "SELECT coalesce(sum(reserved_credits),0) FROM agent_base.context_dev_calls "
                "WHERE tenant_id=$1 AND run_id=$2",
                self.tenant_id, self.run_id,
            )
            if spent + event["reserved_credits"] > max_credits:
                raise CreditLimit
            inserted = await self.conn.fetchval(
                """
                INSERT INTO agent_base.context_dev_calls
                    (call_id,tenant_id,run_id,record_id,region,reserved_credits,paid_step_approval_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (tenant_id,run_id,record_id) DO NOTHING RETURNING call_id
                """,
                uuid.UUID(event["call_id"]), self.tenant_id, self.run_id,
                uuid.UUID(event["record_id"]), os.environ.get("TANK_REGION", "us-east"), event["reserved_credits"],
                uuid.UUID(event["paid_step_approval_id"]) if event.get("paid_step_approval_id") else None,
            )
            if inserted is None:
                raise CreditLimit
        return str(inserted)

    async def settle(self, reservation: str, event: dict[str, Any]) -> None:
        result = {key: event[key] for key in ("status", "fields", "credits", "partial") if key in event}
        await self.conn.execute(
            "UPDATE agent_base.context_dev_calls SET result=$4::jsonb "
            "WHERE tenant_id=$1 AND run_id=$2 AND call_id=$3 AND result IS NULL",
            self.tenant_id, self.run_id, uuid.UUID(reservation), json.dumps(result),
        )
        await self.flush()

    async def flush(self) -> None:
        rows = await self.conn.fetch(
            "SELECT call_id,record_id,region,result,reserved_credits,paid_step_approval_id FROM agent_base.context_dev_calls "
            "WHERE tenant_id=$1 AND run_id=$2 AND posted=false AND result IS NOT NULL",
            self.tenant_id, self.run_id,
        )
        for row in rows:
            result = decoded(row["result"])
            credits = result.get("credits")
            verified_units = isinstance(credits, (int, float)) and 0 < credits <= 1000
            outcome = "unverified" if not verified_units else "delivered" if result["status"] == "candidate" else "failed"
            response = await self.http.post(
                os.environ.get("TANK_URL", "http://tank:8000").rstrip("/") + "/v1/base-product/context-dev",
                headers={"X-Tank-Key": os.environ.get("TANK_KEY", "")},
                json={
                    "id": str(row["call_id"]), "tenant_id": PLATFORM_TENANT,
                    "record_id": str(row["record_id"]), "run_id": str(self.run_id),
                    "units": credits if verified_units else row["reserved_credits"],
                    "outcome": outcome, "region": row["region"],
                    "paid_step_approval_id": str(row["paid_step_approval_id"]) if row["paid_step_approval_id"] else None,
                },
                timeout=30, follow_redirects=False,
            )
            if response.status_code != 200:
                raise RuntimeError("Tank vendor ledger unavailable")
            answer = response.json()
            if answer.get("ledger_id") != str(row["call_id"]) or answer.get("recorded") is not True:
                raise RuntimeError("Tank vendor ledger did not confirm this call")
            async with self.conn.transaction():
                await self.conn.execute(
                    "UPDATE agent_base.records SET passes=jsonb_set(passes,'{context_dev}',$4::jsonb), "
                    "updated_at=now() WHERE tenant_id=$1 AND task_id=$2 AND id=$3",
                    self.tenant_id, self.run_id, row["record_id"], json.dumps(result),
                )
                await self.conn.execute(
                    "UPDATE agent_base.context_dev_calls SET posted=true "
                    "WHERE tenant_id=$1 AND run_id=$2 AND call_id=$3",
                    self.tenant_id, self.run_id, row["call_id"],
                )

    async def recover(self) -> None:
        await self.conn.execute(
            "UPDATE agent_base.context_dev_calls SET result=$3::jsonb "
            "WHERE tenant_id=$1 AND run_id=$2 AND result IS NULL",
            self.tenant_id, self.run_id,
            json.dumps({"status": "unknown", "fields": {}, "credits": None}),
        )
        await self.flush()


async def flush_pending(pool: Any, http: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
        runs = await conn.fetch(
            "SELECT DISTINCT tenant_id,run_id FROM agent_base.context_dev_calls "
            "WHERE tenant_id=$1 AND posted=false LIMIT 200", uuid.UUID(PLATFORM_TENANT),
        )
        for row in runs:
            lock = f"context-trial:{row['tenant_id']}:{row['run_id']}"
            if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1,0))", lock):
                continue
            try:
                await TankVendorLedger(
                    conn, http, tenant_id=str(row["tenant_id"]), run_id=str(row["run_id"]),
                ).recover()
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", lock)
