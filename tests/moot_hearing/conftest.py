"""Only uniquely named disposable Postgres databases; no production DSN accepted."""
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from agents.zenas.core.moot.hearing_store import HearingStore


@pytest_asyncio.fixture
async def pg_store():
    configured = os.environ.get("MOOT_TEST_DATABASE_URL")
    source = configured or (os.environ.get("POOL_SCHEMA_DATABASE_URL") if os.environ.get("GITHUB_ACTIONS") == "true" else None)
    if not source:
        pytest.skip("requires isolated MOOT_TEST_DATABASE_URL or hosted CI pooltest")
    parsed = urlsplit(source)
    assert parsed.hostname in ("127.0.0.1", "localhost", "::1"), "loopback disposable Postgres required"
    database = parsed.path.lstrip("/")
    assert database.endswith("_test") if configured else database.startswith("pooltest")
    name = "moot_" + uuid4().hex + "_test"
    admin = await asyncpg.connect(source)
    await admin.execute(f'CREATE DATABASE "{name}"')
    target = urlunsplit(parsed._replace(path="/" + name))
    pool = None
    try:
        pool = await asyncpg.create_pool(target, min_size=1, max_size=4)
        async with pool.acquire() as conn:
            await conn.execute("CREATE SCHEMA agent_zenas")
            await conn.execute(Path("agents/zenas/core/moot/hearing_schema.sql").read_text())
            await conn.execute("""CREATE TABLE agent_zenas.moot_problems(id text PRIMARY KEY, tenant_id uuid, region text, title text, body jsonb, locked boolean);
                CREATE TABLE agent_zenas.moot_matches(id uuid PRIMARY KEY,tenant_id uuid,problem_id text,region text,mode text,published boolean,fairness jsonb,runs jsonb,created_at timestamptz DEFAULT now());""")
        yield HearingStore(pool)
    finally:
        if pool:
            await pool.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()

