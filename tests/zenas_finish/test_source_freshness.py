"""Changed offline law must not leave reusable cards on superseded versions."""
from datetime import date
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from agents.zenas.core.engine import question_key
from agents.zenas.core.ladder_store import PgLadder
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.chunk import chunk_document
from agents.zenas.pool.checkout import PerStatement
from agents.zenas.pool.model import Section
from agents.zenas.pool.store import PostgresStore

ROOT = Path(__file__).resolve().parents[2]
TENANT = "11111111-1111-4111-8111-111111111111"
FIRST, NEXT = date(2020, 1, 1), date(2024, 1, 1)


@pytest_asyncio.fixture
async def database():
    parts = urlsplit(os.environ["POOL_SCHEMA_DATABASE_URL"])
    assert parts.hostname in {"localhost", "127.0.0.1", "::1"}
    name = "zenas_freshness_" + uuid4().hex + "_test"
    role = "placeholder_freshness_" + uuid4().hex
    admin = await asyncpg.connect(urlunsplit(parts))
    await admin.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'placeholder\' NOSUPERUSER NOBYPASSRLS')
    await admin.execute(f'CREATE DATABASE "{name}" OWNER "{role}"')
    pool = None
    try:
        pool = await asyncpg.create_pool(urlunsplit(parts._replace(path="/" + name)), user=role,
                                       password="placeholder", min_size=1, max_size=3)
        async with pool.acquire() as conn:
            assert not await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            await PostgresStore(conn, home_region="us-east").apply_schema()
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


async def room(pool):
    async with pool.acquire() as conn:
        await conn.execute((ROOT / "agents/zenas/schema.sql").read_text())
    ladder = PgLadder(pool)
    ladder.review_mode = "off"
    return ladder


def document(region="us-east", text="placeholder original civil rule", start=FIRST, *, citation="Fixture section 1",
             source_id=None, kind="statute", url=None):
    source_id = source_id or ("govinfo-uscode" if region == "us-east" else "govza-acts")
    source = next(s for s in sources_for_region(region) if s.source_id == source_id)
    doc = build_document(source, citation=citation, title="Offline law fixture", level="national",
        kind=kind, body="Official publisher", sections=[Section("", "", text)],
        url=url or source.canonical_url, status="in_force", doc_date=start, effective_from=start)
    return source, doc


async def save(pool, region="us-east", **kwargs):
    source, doc = document(region, **kwargs)
    async with pool.acquire() as conn:
        store = PostgresStore(conn, home_region=region)
        await store.record_source(source)
        inserted = await store.upsert_version(doc, chunk_document(doc), on=doc.date)
    return doc, inserted


async def card(ladder, doc, *, region="us-east", status="lawyer_approved", owner=None, versioned=True):
    ref = {"doc_id": doc.id}
    if versioned:
        ref["version_id"] = doc.version_id
    return await ladder.add_rule_card({"region": region, "tenant_id": owner, "jurisdiction": doc.jurisdiction,
        "question": "What is the civil rule?", "sources": [ref], "status": status, "active": True})


async def read(ladder, saved, region="us-east"):
    return await ladder.get_rule_card(saved["id"], region=region, tenant_id=TENANT)


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["us-east", "za"])
@pytest.mark.parametrize("status", ["draft", "lawyer_approved"])
@pytest.mark.parametrize("owner", [None, TENANT])
async def test_source_replacement_retires_only_dependent_local_cards(database, region, status, owner):
    ladder = await room(database)
    old, _ = await save(database, region)
    affected = await card(ladder, old, region=region, status=status, owner=owner)
    unrelated, _ = await save(database, region, citation="Fixture section 2")
    kept = await card(ladder, unrelated, region=region)
    other = "za" if region == "us-east" else "us-east"
    foreign = await card(ladder, old, region=other)

    current, inserted = await save(database, region, text="placeholder amended civil rule", start=NEXT)
    assert inserted and old.id == current.id and old.version_id != current.version_id
    retired = await read(ladder, affected, region)
    assert retired["status"] == "stale" and retired["active"] is False
    assert (await read(ladder, kept, region))["status"] == "lawyer_approved"
    assert (await read(ladder, foreign, other))["status"] == "lawyer_approved"
    async with database.acquire() as conn:
        store = PostgresStore(conn, home_region=region)
        historical = await store.find_by_citation(old.citation, jurisdiction=old.jurisdiction, as_at=date(2022, 1, 1))
        now = await store.find_by_citation(current.citation, jurisdiction=current.jurisdiction, as_at=None)
    assert historical and "original" in historical[0].text
    assert now and "amended" in now[0].text


@pytest.mark.asyncio
async def test_replacement_leaves_no_reusable_card_for_the_question(database):
    ladder = await room(database)
    old, _ = await save(database)
    await card(ladder, old)
    assert await ladder.find_approved(region="us-east", jurisdiction="us",
        key=question_key("What is the civil rule?"), tenant_id=TENANT)
    await save(database, text="placeholder amended civil rule", start=NEXT)
    assert await ladder.find_approved(region="us-east", jurisdiction="us",
        key=question_key("What is the civil rule?"), tenant_id=TENANT) is None


@pytest.mark.asyncio
async def test_unchanged_load_and_historical_backfill_keep_current_card(database):
    ladder = await room(database)
    current, _ = await save(database, start=NEXT)
    saved = await card(ladder, current)
    _, inserted = await save(database, start=NEXT)
    assert not inserted
    await save(database, text="placeholder earlier civil rule", start=FIRST)
    assert (await read(ladder, saved))["status"] == "lawyer_approved"


@pytest.mark.asyncio
async def test_legacy_unversioned_card_is_conservatively_retired(database):
    ladder = await room(database)
    old, _ = await save(database)
    saved = await card(ladder, old, versioned=False)
    await save(database, text="placeholder amended civil rule", start=NEXT)
    assert (await read(ladder, saved))["status"] == "stale"


@pytest.mark.asyncio
async def test_corrected_case_identity_at_same_official_url_retires_old_card(database):
    ladder = await room(database)
    url = "https://www.cac.org.za/placeholder-judgment.pdf"
    kwargs = {"source_id": "cac", "kind": "case", "url": url}
    old, _ = await save(database, "za", citation="Fixture mistaken case", **kwargs)
    saved = await card(ladder, old, region="za")
    current, _ = await save(database, "za", citation="Fixture corrected case", start=NEXT, **kwargs)
    assert current.id != old.id
    assert (await read(ladder, saved, "za"))["status"] == "stale"


@pytest.mark.asyncio
async def test_collapsed_section_closure_retires_its_card(database):
    ladder = await room(database)
    old, _ = await save(database)
    saved = await card(ladder, old)
    async with database.acquire() as conn:
        store = PostgresStore(conn, home_region="us-east")
        assert await store.close_collapsed(old.source_id, old.jurisdiction, old.citation,
            {old.content_hash}, set(), on=NEXT) == 1
        assert await store.close_collapsed(old.source_id, old.jurisdiction, old.citation,
            {old.content_hash}, set(), on=NEXT) == 0
    assert (await read(ladder, saved))["status"] == "stale"


@pytest.mark.asyncio
async def test_pool_loader_can_replace_versions_before_room_schema_exists(database):
    old, _ = await save(database)
    current, inserted = await save(database, text="placeholder amended civil rule", start=NEXT)
    assert inserted and old.id == current.id


@pytest.mark.asyncio
@pytest.mark.parametrize("closure", ["version", "collapsed"])
@pytest.mark.parametrize("per_statement", [False, True])
async def test_failed_invalidation_rolls_back_source_closure_and_card_state(database, closure, per_statement):
    ladder = await room(database)
    old, _ = await save(database)
    saved = await card(ladder, old)

    class RejectInvalidation:
        def __init__(self, conn):
            self.conn = conn
        def __getattr__(self, name):
            return getattr(self.conn, name)
        async def execute(self, sql, *args):
            if "UPDATE agent_zenas.rule_cards" in sql:
                raise RuntimeError("placeholder invalidation failure")
            return await self.conn.execute(sql, *args)

    async with database.acquire() as conn:
        store = PostgresStore(RejectInvalidation(PerStatement(database) if per_statement else conn), home_region="us-east")
        with pytest.raises(RuntimeError, match="placeholder invalidation failure"):
            if closure == "version":
                _, changed = document(text="placeholder amended civil rule", start=NEXT)
                await store.upsert_version(changed, chunk_document(changed), on=NEXT)
            else:
                await store.close_collapsed(old.source_id, old.jurisdiction, old.citation,
                    {old.content_hash}, set(), on=NEXT)
        assert await store.version_count(old.source_id) == 1
        rows = await store.find_by_citation(old.citation, jurisdiction="us", as_at=None)
    assert rows and rows[0].version_id == old.version_id
    assert (await read(ladder, saved))["status"] == "lawyer_approved"
