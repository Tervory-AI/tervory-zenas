"""Per-agent schema. Created on start. Every statement is idempotent."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

_AGENT = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def schema_for(agent: str) -> str:
    if _AGENT.fullmatch(agent) is None:
        raise ValueError("agent name must be lowercase letters, digits, and underscores")
    return f"agent_{agent}"


async def ensure_schema(conn: Any, agent: str) -> str:
    schema = schema_for(agent)
    # Tank creates the schema. The agent's own role may not be allowed to, and
    # CREATE SCHEMA IF NOT EXISTS is a no-op when it is already there.
    try:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    except Exception:
        present = None
        try:
            present = await conn.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = $1", schema)
        except Exception:
            present = None
        if present != 1:
            raise
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.jobs (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL,
            kind            text NOT NULL,
            input           jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            run_at          timestamptz NOT NULL DEFAULT now(),
            status          text NOT NULL DEFAULT 'queued',
            attempts        integer NOT NULL DEFAULT 0,
            max_attempts    integer NOT NULL DEFAULT 3,
            timeout_s       integer NOT NULL DEFAULT 120,
            error           text,
            result          jsonb,
            created_at      timestamptz NOT NULL DEFAULT now(),
            started_at      timestamptz,
            finished_at     timestamptz
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS jobs_tenant_status ON {schema}.jobs (tenant_id, status, run_at)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.schedules (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL,
            cron            text NOT NULL,
            kind            text NOT NULL,
            input           jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            enabled         boolean NOT NULL DEFAULT true,
            last_run        timestamptz
        )
        """
    )
    await conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS schedules_tenant_kind_cron
        ON {schema}.schedules (tenant_id, kind, cron)
        """
    )
    return schema


async def ensure_schedules(conn: Any, schema: str, schedules: list[dict[str, Any]]) -> None:
    for item in schedules:
        await conn.execute(
            f"""
            INSERT INTO {schema}.schedules (tenant_id, cron, kind, input, enabled)
            VALUES ($1, $2, $3, $4::jsonb, true)
            ON CONFLICT (tenant_id, kind, cron) DO NOTHING
            """,
            uuid.UUID(str(item["tenant_id"])),
            item["cron"],
            item["kind"],
            json.dumps(item.get("input") or {}),
        )


def as_json(value: Any) -> Any:
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    if isinstance(value, dict):
        return value
    return {}
