"""The Zenas room can apply the pool schema as agent_zenas.

agent-roles creates zenas_pool for agent_zenas; the store does not re-create
an existing schema (Postgres checks CREATE on the database even with IF NOT EXISTS).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "_chassis"))

from chassis.roles import statements_for  # noqa: E402

from agents.zenas.pool.store import PostgresStore, schema_statements  # noqa: E402


def test_agent_roles_give_zenas_its_pool_schema() -> None:
    sql = statements_for("zenas", "abc123")
    assert "CREATE SCHEMA IF NOT EXISTS zenas_pool AUTHORIZATION agent_zenas" in sql
    assert "GRANT USAGE, CREATE ON SCHEMA zenas_pool TO agent_zenas" in sql
    other = "\n".join(statements_for("template", "abc123"))
    assert "zenas_pool" not in other


class _Conn:
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.executed: list[str] = []

    async def fetch(self, query: str, name: str):
        return [1] if name in self.existing else []

    async def execute(self, statement: str):
        self.executed.append(statement)


def test_apply_schema_skips_an_existing_schema() -> None:
    conn = _Conn({"zenas_pool"})
    asyncio.run(PostgresStore(conn, home_region="za").apply_schema())
    assert not any(s.upper().startswith("CREATE SCHEMA") for s in conn.executed)
    assert len(conn.executed) == len(schema_statements()) - 1


def test_apply_schema_creates_a_missing_schema() -> None:
    conn = _Conn(set())
    asyncio.run(PostgresStore(conn, home_region="za").apply_schema())
    assert conn.executed[0].upper().startswith("CREATE SCHEMA IF NOT EXISTS ZENAS_POOL")
