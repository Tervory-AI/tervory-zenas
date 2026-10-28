"""The Laws.Africa loader → local vector service → actual Ask retrieval path.

These are offline protocol tests, not a claim about the pinned model's ranking.
The only encoder is a stub; the law text is the saved Companies Act section.
"""
import json
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
import pytest

from agents.zenas.core import law_pool as bridge
from agents.zenas.core.service import _passages_from_pool
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.embed import HermesLawClient, LAW_MODEL, live_backends
from agents.zenas.pool.indexing import index_pending
from agents.zenas.pool.nightly import run_nightly
from agents.zenas.pool.search import search_hybrid, search_meaning
from agents.zenas.pool.store import MemoryStore, PostgresStore
from hermes.embeddings import MODEL_ID
from test_hermes_law import stack

RECORDINGS = Path(__file__).resolve().parents[1] / "zenas_za_laws_africa/recordings"
SOURCE = next(row for row in CATALOGUE if row.source_id == "companies-act")
ON = date(2026, 10, 28)
QUESTION = "Who calls the shots under the Companies Act?"


class Replay:
    def __init__(self):
        self.work = json.loads((RECORDINGS / "works-1.json").read_text())["results"][0]
        self.calls = []
        self.html = (RECORDINGS / "section.html").read_bytes()

    async def get(self, url, headers):
        self.calls.append(url)
        if ".html?" in url:
            return self.html
        if url.endswith("/toc.json"):
            return (RECORDINGS / "toc.json").read_bytes()
        return json.dumps(self.work).encode()


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setenv("TANK_KEY", "TANK_KEY_ZENAS")
    monkeypatch.setenv("TANK_KEY_ZENAS", "TANK_KEY_ZENAS")
    monkeypatch.setenv("HERMES_URL", "http://hermes:8000")
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", "LAWS_AFRICA_API_TOKEN")
    from agents.zenas.pool.adapters import laws_africa_api
    async def no_wait(_):
        pass
    monkeypatch.setattr(laws_africa_api.asyncio, "sleep", no_wait)
    qdrant, encoder, app = stack("za")
    client = HermesLawClient(region="za", transport=httpx.ASGITransport(app=app))
    yield qdrant, encoder, client
    qdrant.close()


async def load(store, replay, client):
    return await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get,
                           embedder=client, index=client, on=ON)


def law_pool(monkeypatch, store, backend):
    class Connections:
        @asynccontextmanager
        async def acquire(self):
            yield self
        async def fetch(self, query, jurisdiction, region, versions):
            assert "d.jurisdiction=$1 AND s.region=$2" in query
            assert (jurisdiction, region) == ("za", "za")
            return [{"document_id": doc.id, "version_id": doc.version_id, "url": doc.url,
                     "title": doc.title, "kind": doc.kind, "level": doc.level, "status": doc.status,
                     "version_date": str(doc.effective_from), "attribution": doc.body, "licence": doc.licence}
                    for doc in store.docs.values() if doc.version_id in versions]
    monkeypatch.setattr(bridge, "PostgresStore", lambda *args, **kwargs: store)
    return bridge.AsyncpgLawPool(Connections(), home_region="za", embedder=backend, index=backend)


@pytest.mark.asyncio
async def test_everyday_question_reaches_actual_ask_bridge_with_exact_citation_provenance(monkeypatch, services):
    qdrant, encoder, client = services
    store, replay = MemoryStore(), Replay()
    report = await load(store, replay, client)
    assert report["sources"][0]["last_status"] == "ok"
    assert len(store.embeddings) == len(store.chunks) == qdrant.count("law_za").count
    assert await store.search_text("calls shots companies act", jurisdiction="za", as_at=None) == []
    law = law_pool(monkeypatch, store, client)
    found = await _passages_from_pool(law, query=QUESTION, citation="", jurisdiction="za", as_at=None, level=None, kind=None)
    assert QUESTION in encoder.calls  # Meaning receives the full question, not keyword tokens.
    assert found and "direction of its board" in found[0]["text"]
    assert found[0]["citation"] == "Act 71 of 2008, s 66(1)"
    assert found[0]["url"].endswith("#sec_66")
    assert found[0]["version_date"] == "2024-01-01" and found[0]["attribution"] == "Laws.Africa"
    assert "Laws.Africa" in found[0]["licence"]


@pytest.mark.asyncio
async def test_unchanged_nightly_does_no_encoding_and_new_version_is_date_filtered(services):
    qdrant, encoder, client = services
    store, replay = MemoryStore(), Replay()
    await load(store, replay, client)
    encoder.calls.clear()
    replay.calls.clear()
    await run_nightly(store, region="za", only=[SOURCE.source_id], live=True, http_get=replay.get,
                      embedder=client, index=client, on=ON)
    assert encoder.calls == []
    assert not any(".html?" in url or url.endswith("/toc.json") for url in replay.calls)
    previous = replay.work["points_in_time"][0]
    expression = previous["expressions"][0]
    replay.work["points_in_time"].append({"date": "2026-10-29", "expressions": [{
        **expression, "url": expression["url"].replace(previous["date"], "2026-10-29")}]})
    await run_nightly(store, region="za", only=[SOURCE.source_id], live=True, http_get=replay.get,
                      embedder=client, index=client, on=date(2026, 10, 30))
    # A new consolidation with unchanged words needs new version metadata, but
    # its section vectors are reused without another model call.
    assert encoder.calls == []
    current = await search_meaning(store, client, client, QUESTION, jurisdiction="za")
    old = await search_meaning(store, client, client, QUESTION, jurisdiction="za", as_at=ON)
    assert {row.version_date for row in current["chunks"]} == {"2026-10-29"}
    assert {row.version_date for row in old["chunks"]} == {"2024-01-01"}


@pytest.mark.asyncio
async def test_failed_vector_write_is_repaired_from_stored_sections_before_feed_checkpoint(monkeypatch, services):
    qdrant, encoder, client = services
    store, replay = MemoryStore(), Replay()
    original = client.upsert
    async def unavailable(*args):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(client, "upsert", unavailable)
    report = await load(store, replay, client)
    assert report["sources"][0]["last_status"] == "error"
    assert store.chunks and not store.embeddings
    monkeypatch.setattr(client, "upsert", original)
    report = await load(store, replay, client)
    assert report["sources"][0]["last_status"] == "ok"
    assert len(store.embeddings) == len(store.chunks) == qdrant.count("law_za").count
    encoder.calls.clear()
    await load(store, replay, client)
    assert encoder.calls == []


@pytest.mark.asyncio
async def test_missing_source_never_indexes_or_appears_even_in_keyword_results(services):
    _, _, client = services
    store, replay = MemoryStore(), Replay()
    # Existing sections predating this deployment get indexed by the same load.
    await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get, on=ON)
    for doc in store.docs.values():
        doc.url = ""
    await index_pending(store, client, client, region="za")
    assert not store.embeddings
    assert await search_hybrid(store, client, client, QUESTION, keyword_query="board", jurisdiction="za") == []


@pytest.mark.asyncio
async def test_boundary_rejects_other_region_and_remote_host_before_sending_text(monkeypatch, services):
    _, encoder, client = services
    from agents.zenas.pool.region import RegionError
    with pytest.raises(RegionError):
        await client.query("us", [1, 0, 0, 0], limit=8)
    monkeypatch.setenv("HERMES_URL", "https://remote.tervory.com")
    with pytest.raises(RegionError):
        await client.embed([QUESTION])
    assert encoder.calls == []


def test_production_defaults_are_real_and_model_matches_the_pinned_encoder():
    backend, index = live_backends(PostgresStore(object(), home_region="za"), "za")
    assert isinstance(backend, HermesLawClient) and backend is index
    assert LAW_MODEL == MODEL_ID


@pytest.mark.asyncio
async def test_meaning_answer_and_saved_memo_keep_source_date_and_attribution(monkeypatch, services):
    from agents.zenas.core.ladder_store import LadderMemory
    from agents.zenas.core.service import answer_question
    from agents.zenas.core.store import MemoryStore as MatterStore
    _, _, client = services
    monkeypatch.setenv("ZENAS_REVIEW", "off")
    pool = MemoryStore()
    await load(pool, Replay(), client)
    law = law_pool(monkeypatch, pool, client)
    passages = await _passages_from_pool(law, query=QUESTION, citation="", jurisdiction="za", as_at=None, level=None, kind=None)
    passage = next(row for row in passages if "direction of its board" in row["text"])
    tenant = "00000000-0000-0000-0000-0000000000a1"
    ladder, matters = LadderMemory(), MatterStore()
    await ladder.register_tenant(tenant, "za")
    await matters.register_tenant(tenant, "za")
    matter = await matters.add_matter({"tenant_id": tenant, "jurisdiction": "za", "title": QUESTION})
    await ladder.add_rule_card({"region": "za", "tenant_id": tenant, "jurisdiction": "za", "question": QUESTION,
        "status": "lawyer_approved", "elements": [{"id": "board", "test": passage["text"],
            "citation": passage["citation"], "quote": passage["text"]}],
        "decision_table": [{"element_id": "board", "when": {"entity": "company"}, "finding": "met"}]})
    result = await answer_question(matters, ladder, None, tenant_id=tenant, home_region="za", box_region="za",
        jurisdiction="za", question=QUESTION, passages=[], features={"entity": "company"}, matter_id=matter["id"], pool=law)
    assert result["ok"] and result["delivered"] and result["cost_usd"] == 0
    source, = result["memo"]["sources"]
    assert source == {key: passage[key] for key in ("citation", "url", "version_date", "attribution", "licence")} | {"quote": passage["text"]}
    saved = await matters.get_document(tenant, result["document_id"])
    assert saved["sources"] == [source]


@pytest.mark.asyncio
async def test_keyword_fallback_and_rank_fusion_never_duplicate_a_section(monkeypatch, services):
    _, _, client = services
    store = MemoryStore()
    await load(store, Replay(), client)
    mixed = await search_hybrid(store, client, client, QUESTION, keyword_query="board", jurisdiction="za")
    assert len(mixed) == len({row.chunk_id for row in mixed}) == len(store.chunks)
    async def unavailable(_):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(client, "embed", unavailable)
    keywords = await search_hybrid(store, client, client, QUESTION, keyword_query="board", jurisdiction="za")
    assert [row.chunk_id for row in keywords] == [row.chunk_id for row in mixed]
    assert keywords[0].url.endswith("#sec_66")


@pytest.mark.asyncio
async def test_paged_meaning_skips_stale_candidates_instead_of_hiding_current_law(services):
    from types import SimpleNamespace
    _, _, client = services
    store = MemoryStore()
    await load(store, Replay(), client)
    valid = next(iter(store.chunks))
    pages = []
    async def query_pages(jurisdiction, vector, *, limit):
        pages.append(1)
        yield ["a-stale-version-id"]
        pages.append(2)
        yield [valid]
    result = await search_meaning(store, client, SimpleNamespace(query_pages=query_pages), QUESTION, jurisdiction="za", limit=1)
    assert pages == [1, 2]
    assert [row.chunk_id for row in result["chunks"]] == [valid]


@pytest.mark.asyncio
async def test_source_less_citation_is_not_returned_by_retrieve(monkeypatch, services):
    _, _, client = services
    store = MemoryStore()
    await load(store, Replay(), client)
    for doc in store.docs.values():
        doc.url = ""
    law = law_pool(monkeypatch, store, client)
    assert await _passages_from_pool(law, query="", citation="Act 71 of 2008, s 66", jurisdiction="za", as_at=None, level=None, kind=None) == []


@pytest.mark.asyncio
async def test_changed_section_only_is_encoded_and_query_pages_run_through_real_routes(services):
    _, encoder, client = services
    store, replay = MemoryStore(), Replay()
    await load(store, replay, client)
    encoder.calls.clear()
    point = replay.work["points_in_time"][0]
    expression = point["expressions"][0]
    replay.work["points_in_time"].append({"date": "2026-10-29", "expressions": [{
        **expression, "url": expression["url"].replace(point["date"], "2026-10-29")}]})
    # Protocol-only byte change, not a claim about a real amendment: duplicate
    # the already-recorded sentence. The parent heading chunk is unchanged.
    sentence = b"The business and affairs of a company must be managed by or under the direction of its board."
    replay.html = replay.html.replace(sentence, sentence + b" " + sentence)
    await run_nightly(store, region="za", only=[SOURCE.source_id], live=True, http_get=replay.get,
                      embedder=client, index=client, on=date(2026, 10, 30))
    assert len(encoder.calls) == 1
    assert sentence.decode() + " " + sentence.decode() in encoder.calls[0]
    ids = []
    async for page in client.query_pages("za", [1, 0, 0, 0], limit=1):
        ids.extend(page)
    assert len(ids) == len(set(ids)) == len(store.chunks)


@pytest.mark.asyncio
async def test_production_store_receipts_and_hydration_are_region_scoped():
    from agents.zenas.pool.model import Chunk
    from agents.zenas.pool.region import RegionError
    class Connection:
        def __init__(self):
            self.calls = []
        async def fetch(self, sql, *args):
            self.calls.append((sql, args))
            return []
        async def executemany(self, sql, args):
            self.calls.append((sql, args))
    conn = Connection()
    store = PostgresStore(conn, home_region="za")
    assert await store.pending_embeddings(LAW_MODEL, jurisdiction="za", source_ids=["companies-act"]) == []
    sql, args = conn.calls[-1]
    assert "s.region=$3" in sql and "c.jurisdiction=$2 AND d.jurisdiction=$2" in sql
    assert args[:3] == (LAW_MODEL, "za", "za")
    assert await store.get_chunks(["chunk"], jurisdiction="za", as_at=ON) == []
    sql, args = conn.calls[-1]
    assert "s.region=$3" in sql and "d.effective_to>$4" in sql
    assert args[:4] == ("za", ["chunk"], "za", ON)
    chunk = Chunk("chunk", "version", "document", "us", "", "", "", "", "", 0)
    before = len(conn.calls)
    with pytest.raises(RegionError):
        await store.record_embeddings([chunk], LAW_MODEL, 384, "law_us")
    with pytest.raises(RegionError):
        await store.pending_embeddings(LAW_MODEL, jurisdiction="us")
    with pytest.raises(RegionError):
        await store.get_chunks(["chunk"], jurisdiction="us")
    assert len(conn.calls) == before


@pytest.mark.asyncio
async def test_first_seen_date_is_not_invented_as_an_official_version_date(services):
    _, _, client = services
    store, replay = MemoryStore(), Replay()
    await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get, on=ON)
    # The explicit unknown-date case uses stored law, not invented date data.
    for doc in store.docs.values():
        doc.effective_from = None
    await index_pending(store, client, client, region="za")
    chunks = await store.get_chunks(list(store.chunks), jurisdiction="za")
    assert chunks and len(store.embeddings) == len(chunks)
    assert all(chunk.version_date == "" and chunk.known_from == str(ON) for chunk in chunks)
