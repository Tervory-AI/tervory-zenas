"""Must-fix checks from the review of the US knowledge pool. Offline only."""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.chunk import chunk_document
from agents.zenas.pool.config import current_region
from agents.zenas.pool.model import Chunk, LegalDocument, Section
from agents.zenas.pool.nightly import packaged_snapshots, run_nightly
from agents.zenas.pool.store import MemoryStore, PostgresStore, TEXT_CHUNK_SQL, schema_sql
from agents.zenas.pool.textutil import plan_version, version_id_for, VersionHead

ON = date(2026, 10, 13)


def _usc():
    return next(item for item in sources_for_region("us-east") if item.source_id == "govinfo-uscode")


def _wording(text: str, start: date):
    source = _usc()
    doc = build_document(
        source,
        citation="Fixture § 2",
        title="Restored wording",
        level="federal",
        kind="statute",
        body="Congress",
        sections=[Section("(a)", "General", text, [])],
        url="https://www.govinfo.gov/",
        status="in_force",
        doc_date=start,
        effective_from=start,
    )
    doc.known_from = start
    doc.version_id = version_id_for(doc.id, doc.content_hash, doc.effective_from, doc.known_from)
    return doc


def test_restored_wording_becomes_current_again():
    asyncio.run(_restored())


async def _restored():
    store = MemoryStore()
    await store.record_source(_usc())
    first = _wording("alpha wording", date(2020, 1, 1))
    middle = _wording("beta wording", date(2022, 1, 1))
    restored = _wording("alpha wording", date(2024, 1, 1))
    assert first.content_hash == restored.content_hash
    assert first.version_id != restored.version_id
    assert await store.upsert_version(first, chunk_document(first), on=date(2020, 1, 1))
    assert await store.upsert_version(middle, chunk_document(middle), on=date(2022, 1, 1))
    assert await store.upsert_version(restored, chunk_document(restored), on=date(2024, 1, 1))
    same = [doc for doc in store.docs.values() if doc.content_hash == first.content_hash]
    assert len(same) == 2
    open_rows = [doc for doc in store.docs.values() if doc.effective_to is None]
    assert len(open_rows) == 1
    assert open_rows[0].content_hash == restored.content_hash
    current = await store.find_by_citation("Fixture § 2(a)", jurisdiction="us", as_at=None)
    assert current and "alpha wording" in current[0].text


def test_late_earlier_version_is_born_closed():
    asyncio.run(_late())


async def _late():
    store = MemoryStore()
    await store.record_source(_usc())
    later = _wording("later wording", date(2024, 1, 1))
    earlier = _wording("earlier wording", date(2020, 1, 1))
    assert await store.upsert_version(later, chunk_document(later), on=date(2024, 1, 1))
    assert await store.upsert_version(earlier, chunk_document(earlier), on=date(2020, 1, 1))
    assert earlier.effective_to == date(2024, 1, 1)
    assert later.effective_to is None
    assert sum(1 for doc in store.docs.values() if doc.effective_to is None) == 1
    seen = await store.find_by_citation("Fixture § 2(a)", jurisdiction="us", as_at=date(2022, 6, 1))
    assert seen and "earlier wording" in seen[0].text
    current = await store.find_by_citation("Fixture § 2(a)", jurisdiction="us", as_at=date(2024, 6, 1))
    assert current and "later wording" in current[0].text


def test_plan_ignores_a_closed_copy_of_the_same_text():
    closed = VersionHead("v1", "aaa", date(2020, 1, 1), date(2022, 1, 1))
    open_other = VersionHead("v2", "bbb", date(2022, 1, 1), None)
    plan = plan_version(
        [closed, open_other],
        content_hash="aaa",
        effective_from=date(2024, 1, 1),
        on=date(2024, 1, 1),
    )
    assert plan.action == "insert"
    assert plan.close_ids == ("v2",)
    assert plan.effective_to is None


def test_undated_sources_use_first_seen_as_floor():
    asyncio.run(_undated())


async def _undated():
    store = MemoryStore()
    await run_nightly(store, region="us-east", snapshots=packaged_snapshots("us-east"), on=ON)
    before = ON.replace(day=ON.day - 1)
    delaware = await store.find_by_citation("8 Del. C. § 101(a)", jurisdiction="us", as_at=ON)
    assert delaware and "certificate of incorporation" in delaware[0].text.lower()
    assert not await store.find_by_citation("8 Del. C. § 101(a)", jurisdiction="us", as_at=before)
    tariff = await store.find_by_citation("HTSUS 8901.10.00.00", jurisdiction="us", as_at=ON)
    assert tariff and "ferry boats" in tariff[0].text.lower()
    assert not await store.find_by_citation("HTSUS 8901.10.00.00", jurisdiction="us", as_at=before)
    stored = [
        doc
        for doc in store.docs.values()
        if doc.source_id in {"delaware-code", "usitc-htsus"}
    ]
    assert stored
    assert all(doc.effective_from is None and doc.known_from == ON for doc in stored)


def test_postgres_upsert_is_one_transaction():
    asyncio.run(_postgres_txn())


async def _postgres_txn():
    class _Txn:
        def __init__(self, log: list[str]) -> None:
            self.log = log

        async def __aenter__(self):
            self.log.append("begin")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.log.append("commit")
            return False

    class _Conn:
        def __init__(self) -> None:
            self.log: list[str] = []
            self.executes: list[str] = []
            self.batches: list[list] = []

        def transaction(self):
            return _Txn(self.log)

        async def fetchrow(self, sql, *args):
            return {
                "source_id": "govinfo-uscode",
                "name": "United States Code",
                "region": "us-east",
                "jurisdiction": "us",
                "licence": "public domain",
                "terms": "enacted text may be stored",
                "terms_url": "https://www.govinfo.gov/",
                "bulk_use": "allowed",
                "store_full_text": True,
                "canonical_url": "https://www.govinfo.gov/",
                "notes": "",
            }

        async def fetch(self, sql, *args):
            return []

        async def execute(self, sql, *args):
            self.log.append("execute")
            self.executes.append(sql)

        async def executemany(self, sql, rows):
            self.log.append("executemany")
            self.batches.append(list(rows))

    conn = _Conn()
    doc = LegalDocument(
        id="govinfo-uscode:fixture 9",
        jurisdiction="us",
        level="federal",
        body="Congress",
        kind="statute",
        citation="Fixture § 9",
        title="Transaction",
        date=date(2024, 1, 1),
        effective_from=date(2024, 1, 1),
        effective_to=None,
        status="in_force",
        url="https://www.govinfo.gov/",
        licence="public domain",
        text="one two",
        sections=[],
        source_id="govinfo-uscode",
        version_id="govinfo-uscode:fixture 9:abcd",
        content_hash="abcd",
    )
    chunks = [
        Chunk("c0", doc.version_id, doc.id, "us", "", "One", "one", doc.citation, "fixture 9", 0),
        Chunk("c1", doc.version_id, doc.id, "us", "", "Two", "two", doc.citation, "fixture 9", 1),
    ]
    assert await PostgresStore(conn).upsert_version(doc, chunks, on=date(2024, 1, 1))
    begin = conn.log.index("begin")
    commit = conn.log.index("commit")
    writes = [i for i, name in enumerate(conn.log) if name in {"execute", "executemany"}]
    assert begin < writes[0] and writes[-1] < commit
    assert len(conn.batches) == 1
    assert len(conn.batches[0]) == 2
    assert not any("zenas_pool.chunks" in sql for sql in conn.executes)


def test_nightly_records_an_error_and_continues():
    asyncio.run(_nightly_error())


async def _nightly_error():
    class Boom(MemoryStore):
        async def upsert_version(self, doc, chunks, *, on):
            if doc.source_id == "frcp":
                raise RuntimeError("disk full")
            return await super().upsert_version(doc, chunks, on=on)

    store = Boom()
    report = await run_nightly(
        store,
        region="us-east",
        snapshots=packaged_snapshots("us-east"),
        on=ON,
    )
    by_id = {row["source_id"]: row for row in report["sources"]}
    assert by_id["frcp"]["last_status"] == "error"
    assert "disk full" in by_id["frcp"]["detail"]
    assert by_id["fre"]["documents"] == 1
    assert by_id["fre"]["last_status"] == "ok"


def test_region_fails_closed_when_unset():
    saved = os.environ.pop("TANK_REGION", None)
    try:
        try:
            current_region()
        except ValueError as exc:
            assert "TANK_REGION" in str(exc)
        else:
            raise AssertionError("unset TANK_REGION must fail")
    finally:
        if saved is not None:
            os.environ["TANK_REGION"] = saved


def test_full_text_orders_by_rank_then_ordinal():
    folded = " ".join(TEXT_CHUNK_SQL.split())
    order = folded.upper().split("ORDER BY", 1)[1]
    assert "TS_RANK(" in order
    assert order.index("TS_RANK(") < order.index("ORDINAL")
    assert "DESC" in order
    asyncio.run(_memory_rank())


async def _memory_rank():
    source = next(item for item in sources_for_region("us-east") if item.source_id == "frcp")
    store = MemoryStore()
    await store.record_source(source)
    doc = build_document(
        source,
        citation="Fixture § 3",
        title="Rank",
        level="federal",
        kind="court_rule",
        body="Supreme Court of the United States",
        sections=[
            Section("(a)", "Once", "ferry boats once", []),
            Section("(b)", "Often", "ferry ferry ferry boats", []),
        ],
        url="https://www.govinfo.gov/",
        status="in_force",
        doc_date=date(2020, 1, 1),
        effective_from=date(2020, 1, 1),
    )
    assert await store.upsert_version(doc, chunk_document(doc), on=date(2020, 1, 1))
    found = await store.search_text("ferry", jurisdiction="us", as_at=None)
    assert [chunk.ordinal for chunk in found] == [1, 0]


def test_refeeding_a_closed_version_does_not_duplicate():
    asyncio.run(_refeed_closed())


async def _refeed_closed():
    store = MemoryStore()
    await store.record_source(_usc())
    later = _wording("later wording", date(2024, 1, 1))
    earlier = _wording("earlier wording", date(2020, 1, 1))
    assert await store.upsert_version(later, chunk_document(later), on=date(2024, 1, 1))
    assert await store.upsert_version(earlier, chunk_document(earlier), on=date(2020, 6, 1))
    for seen in (date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)):
        replay = _wording("earlier wording", date(2020, 1, 1))
        replay.known_from = seen
        replay.version_id = version_id_for(
            replay.id, replay.content_hash, replay.effective_from, replay.known_from
        )
        assert not await store.upsert_version(replay, chunk_document(replay), on=seen)
    early_rows = [doc for doc in store.docs.values() if doc.effective_from == date(2020, 1, 1)]
    assert len(early_rows) == 1
    assert await store.version_count() == 2

    cycle = MemoryStore()
    await cycle.record_source(_usc())
    for wording, start in (
        ("alpha wording", date(2020, 1, 1)),
        ("beta wording", date(2022, 1, 1)),
        ("alpha wording", date(2024, 1, 1)),
    ):
        doc = _wording(wording, start)
        assert await cycle.upsert_version(doc, chunk_document(doc), on=start)
    for wording, start, seen in (
        ("alpha wording", date(2020, 1, 1), date(2026, 4, 1)),
        ("beta wording", date(2022, 1, 1), date(2026, 4, 2)),
        ("alpha wording", date(2024, 1, 1), date(2026, 4, 3)),
    ):
        replay = _wording(wording, start)
        replay.known_from = seen
        replay.version_id = version_id_for(
            replay.id, replay.content_hash, replay.effective_from, replay.known_from
        )
        assert not await cycle.upsert_version(replay, chunk_document(replay), on=seen)
    assert await cycle.version_count() == 3
    assert sum(1 for doc in cycle.docs.values() if "beta wording" in doc.text) == 1


def test_hash_index_includes_the_version_span():
    text = schema_sql()
    assert "ALTER TABLE zenas_pool.documents ADD COLUMN IF NOT EXISTS known_from date;" in text
    start = text.index("CREATE UNIQUE INDEX IF NOT EXISTS documents_hash_span")
    statement = text[start : text.index(";", start)]
    assert "content_hash" in statement
    assert "effective_from" in statement
    assert "known_from" in statement
    assert "DROP" not in statement.upper()
