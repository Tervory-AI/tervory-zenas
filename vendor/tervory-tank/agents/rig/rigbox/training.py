"""Durable room events; only Tank writes training pairs and calls models."""

import json
import os
from uuid import UUID


def region() -> str:
    value = os.environ.get("TANK_REGION", "us-east")
    if value not in {"us-east", "za"}:
        raise ValueError("Invalid Rig region")
    return value


async def enqueue(conn, tenant: str, source: str, ref: str, value: dict, output: dict) -> None:
    await conn.execute("""INSERT INTO agent_rig.training_events
        (tenant_id, region, source, source_ref, input, output)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb) ON CONFLICT DO NOTHING""",
        UUID(tenant), region(), source, ref, json.dumps(value), json.dumps(output))
