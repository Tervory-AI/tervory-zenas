"""Findings table in the agent's own schema. Every statement is idempotent.

Rows are always written and read with tenant_id. The secret value has no column.
"""

from __future__ import annotations

import re
import uuid

_SCHEMA = re.compile(r"^agent_[a-z][a-z0-9_]{0,40}$")


def validate_schema(schema: str) -> str:
    if _SCHEMA.fullmatch(schema) is None:
        raise ValueError("schema name is not safe")
    return schema


def ensure_sql(schema: str) -> str:
    name = validate_schema(schema)
    return f"""
        CREATE TABLE IF NOT EXISTS {name}.findings (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL,
            scan_id         uuid NOT NULL,
            tool            text NOT NULL,
            severity        text NOT NULL,
            file            text,
            line            integer,
            rule            text,
            fix             text,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """


def index_sql(schema: str) -> str:
    name = validate_schema(schema)
    return (
        f"CREATE INDEX IF NOT EXISTS findings_tenant_scan "
        f"ON {name}.findings (tenant_id, scan_id)"
    )


def insert_sql(schema: str) -> str:
    name = validate_schema(schema)
    return f"""
        INSERT INTO {name}.findings (tenant_id, scan_id, tool, severity, file, line, rule, fix)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """


def list_sql(schema: str) -> str:
    name = validate_schema(schema)
    return f"""
        SELECT id, tenant_id, scan_id, tool, severity, file, line, rule, fix
        FROM {name}.findings
        WHERE tenant_id = $1 AND scan_id = $2
        ORDER BY file, line, rule
        """


async def ensure_findings(conn, schema: str) -> None:
    await conn.execute(ensure_sql(schema))
    await conn.execute(index_sql(schema))


async def insert_findings(conn, schema: str, tenant_id: str, scan_id: str, findings: list[dict]) -> None:
    sql = insert_sql(schema)
    tenant = uuid.UUID(str(tenant_id))
    scan = uuid.UUID(str(scan_id))
    for item in findings:
        await conn.execute(
            sql,
            tenant,
            scan,
            str(item.get("tool") or ""),
            str(item.get("severity") or ""),
            item.get("file"),
            int(item.get("line") or 0),
            item.get("rule"),
            item.get("fix"),
        )


async def list_findings(conn, schema: str, tenant_id: str, scan_id: str):
    return await conn.fetch(list_sql(schema), uuid.UUID(str(tenant_id)), uuid.UUID(str(scan_id)))
