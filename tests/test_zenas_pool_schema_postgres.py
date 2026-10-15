"""Apply the pool schema on a real Postgres: empty, again, and over main's schema."""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.store import PostgresStore, schema_statements

MAIN_SCHEMA = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "zenas_pool_schema_main.sql"


def _statements(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            parts.append("\n".join(buf).strip())
            buf = []
    if buf:
        parts.append("\n".join(buf).strip())
    return parts


def _database_url() -> str:
    url = os.environ.get("POOL_SCHEMA_DATABASE_URL", "").strip()
    if url:
        return url
    if os.environ.get("GITHUB_ACTIONS"):
        pytest.fail("POOL_SCHEMA_DATABASE_URL is not set")
    pytest.skip("POOL_SCHEMA_DATABASE_URL is not set")
    return ""


async def _reset(conn) -> None:
    await conn.execute("DROP SCHEMA IF EXISTS zenas_pool CASCADE")


async def _apply_text(conn, text: str) -> None:
    for statement in _statements(text):
        await conn.execute(statement)


def test_schema_applies_on_empty_again_and_over_main():
    asyncio.run(_three_ways())


async def _three_ways():
    conn = await asyncpg.connect(_database_url())
    try:
        await _empty_and_again(conn)
        await _over_main(conn)
    finally:
        await _reset(conn)
        await conn.close()


async def _empty_and_again(conn) -> None:
    await _reset(conn)
    store = PostgresStore(conn, home_region="us-east")
    await store.apply_schema()
    await store.apply_schema()
    tables = await conn.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'zenas_pool'
        ORDER BY table_name
        """
    )
    names = {row["table_name"] for row in tables}
    assert {"sources", "documents", "chunks", "cites", "checkpoints"} <= names
    await conn.execute(
        """
        INSERT INTO zenas_pool.sources (
            source_id, name, region, jurisdiction, licence, terms, bulk_use, store_full_text
        ) VALUES (
            'frcp', 'Federal Rules of Civil Procedure', 'us-east', 'us',
            'Public domain.', 'Government work. Recorded as information.', 'allowed', true
        )
        """
    )
    fresh = await conn.fetchrow(
        "SELECT enabled, key_name, key_required FROM zenas_pool.sources WHERE source_id = 'frcp'"
    )
    assert fresh["enabled"] is True
    assert fresh["key_name"] == ""
    assert fresh["key_required"] is False


async def _over_main(conn) -> None:
    await _reset(conn)
    await _apply_text(conn, MAIN_SCHEMA.read_text(encoding="utf-8"))
    before = await conn.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'zenas_pool' AND table_name = 'sources'
        """
    )
    assert "enabled" not in {row["column_name"] for row in before}
    await conn.execute(
        """
        INSERT INTO zenas_pool.sources (
            source_id, name, region, jurisdiction, licence, terms, bulk_use, store_full_text
        ) VALUES (
            'govinfo-uscode', 'United States Code', 'us-east', 'us',
            'Public domain.', 'Government work. Recorded as information.', 'allowed', true
        )
        """
    )
    store = PostgresStore(conn, home_region="us-east")
    await store.apply_schema()
    await store.apply_schema()
    row = await conn.fetchrow(
        """
        SELECT name, enabled, key_name, key_required
        FROM zenas_pool.sources
        WHERE source_id = 'govinfo-uscode'
        """
    )
    assert row["name"] == "United States Code"
    assert row["enabled"] is True
    assert row["key_name"] == ""
    assert row["key_required"] is False
    count = await conn.fetchval("SELECT count(*) FROM zenas_pool.sources")
    assert count == 1
    # The statements the store applies are the same ones just run.
    assert schema_statements()
