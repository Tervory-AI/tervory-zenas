"""Source metadata checks using the repository's saved official Delaware statute.

This statute is not presented as an answer to the founder's forum-selection
question. Live answer/link verification remains a separate acceptance check.
"""

from datetime import date
import base64
from io import BytesIO
from pathlib import Path
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
from xml.etree import ElementTree

import asyncpg
import pytest

from agents.zenas.core.citation_sources import cited_sources, public_source_url
from agents.zenas.core.engine import cite_check
from agents.zenas.core.law_pool import AsyncpgLawPool
from agents.zenas.core.ladder_store import LadderMemory
from agents.zenas.core.service import _passages_from_pool, answer_question, draft_contract, write_memo
from agents.zenas.core.store import MemoryStore
from agents.zenas.pool.adapters.states import DelawareAdapter
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.chunk import chunk_document
from agents.zenas.pool.store import PostgresStore

ROOT = Path(__file__).resolve().parents[1]
NOTICE = "Zenas provides legal information, not legal advice."
TENANT = "00000000-0000-0000-0000-0000000000b1"
QUESTION = "May a corporation be incorporated under Delaware law?"


def official_document():
    source = next(row for row in sources_for_region("us-east") if row.source_id == "delaware-code")
    raw = (ROOT / "agents/zenas/pool/fixtures/saved/delaware-code/title8-c001-sc01.html").read_bytes()
    document = next(row for row in DelawareAdapter().parse(raw, source) if row.citation == "8 Del. C. § 101")
    return source, document


def test_link_comes_from_the_quote_checked_document_version():
    _, document = official_document()
    chunk = chunk_document(document)[0]
    passage = {"doc_id": document.id, "version_id": document.version_id, "citation": document.citation,
               "url": document.url, "text": chunk.text, "status": document.status}
    statements = [{"citation": document.citation, "quote": chunk.text}]
    checked = cite_check(statements, [passage])
    assert checked["passed"]
    assert cited_sources(statements, checked, [passage], [document.citation], require_links=True) == [
        {"citation": document.citation, "quote": chunk.text, "url": document.url}]
    assert cited_sources(statements, checked, [{**passage, "url": ""}], [document.citation], require_links=True) is None
    assert cited_sources(statements, checked, [{**passage, "version_id": ""}], [document.citation], require_links=True) is None


@pytest.mark.parametrize("value", [None, "", "http://delcode.delaware.gov/title8/c001/sc01/index.html", "https://localhost/", "https://127.0.0.1/", "https://example.com/", "https://law.test/", "https://delcode.delaware.gov/?api_key="])
def test_nonpublic_or_credential_source_links_are_not_returned(value):
    assert public_source_url(value) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("region,jurisdiction", [("us-east", "us"), ("za", "za")])
async def test_source_metadata_uses_each_box_region_and_exact_version(region, jurisdiction):
    # Only transport is mocked: the real bridge constructs the scoped query.
    _, document = official_document()
    calls = []

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield self

        async def fetch(self, query, *args):
            calls.append((query, args))
            return [{"document_id": document.id, "version_id": document.version_id,
                     "url": document.url, "title": document.title, "kind": document.kind,
                     "level": document.level, "status": document.status}]

    law = AsyncpgLawPool(Pool(), home_region=region)
    chunks = [SimpleNamespace(version_id=document.version_id)]
    result = await law.source_metadata(chunks, jurisdiction=jurisdiction)
    assert result[(document.id, document.version_id)]["url"] == document.url
    query, args = calls.pop()
    assert "d.jurisdiction=$1 AND s.region=$2 AND d.version_id=ANY($3::text[])" in query
    assert args == (jurisdiction, region, [document.version_id])
    assert await law.source_metadata(chunks, jurisdiction="za" if jurisdiction == "us" else "us") == {}
    assert await law.source_metadata([], jurisdiction=jurisdiction) == {}
    assert await AsyncpgLawPool(Pool(), home_region="").source_metadata(chunks, jurisdiction=jurisdiction) == {}
    assert calls == []


@pytest.mark.asyncio
async def test_real_postgres_passages_keep_the_stored_source_url():
    from test_rig_postgres import _admin_url, _create_database, _database_url, _drop_database
    admin = _admin_url()
    name = "zenas_sources_" + uuid4().hex
    await _create_database(admin, name)
    pool = await asyncpg.create_pool(_database_url(admin, name))
    try:
        source, document = official_document()
        async with pool.acquire() as conn:
            store = PostgresStore(conn, home_region="us-east")
            await store.apply_schema()
            await store.record_source(source)
            await store.upsert_version(document, chunk_document(document), on=date.today())
        law = AsyncpgLawPool(pool, home_region="us-east")
        passages = await _passages_from_pool(law, query="", citation=document.citation, jurisdiction="us", as_at=None, level=None, kind=None)
        assert passages
        assert all(row["url"] == document.url and row["title"] == document.title for row in passages)
        assert law.requires_source_links
        chunks = await law.find_by_citation(document.citation, jurisdiction="us")
        assert await law.source_metadata(chunks, jurisdiction="za") == {}
        assert await AsyncpgLawPool(pool, home_region="za").source_metadata(chunks, jurisdiction="us") == {}
    finally:
        await pool.close()
        await _drop_database(admin, name)


async def saved_law_card():
    """Reuse the saved official statute; no model or live connection is needed."""
    _, document = official_document()
    chunk = chunk_document(document)[0]
    passage = {"doc_id": document.id, "version_id": document.version_id, "citation": document.citation,
               "url": document.url, "text": chunk.text, "status": document.status}
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT, "us-east")
    card = await ladder.add_rule_card({
        "region": "us-east", "tenant_id": TENANT, "jurisdiction": "us", "question": QUESTION,
        "status": "lawyer_approved", "elements": [
            {"id": "formation", "test": chunk.text, "citation": document.citation, "quote": chunk.text}],
        "decision_table": [{"element_id": "formation", "when": {"form": "written"}, "finding": "met"}],
    })
    store = MemoryStore()
    await store.register_tenant(TENANT, "us-east")
    matter = await store.add_matter({"tenant_id": TENANT, "jurisdiction": "us", "title": QUESTION})
    return ladder, store, matter, card, passage


@pytest.mark.asyncio
@pytest.mark.parametrize("review", ["off", "on"])
async def test_answer_and_saved_memo_keep_the_verified_link_and_exact_notice(monkeypatch, review):
    monkeypatch.setenv("ZENAS_REVIEW", review)
    ladder, store, matter, card, passage = await saved_law_card()
    answer = await answer_question(
        store, ladder, None, tenant_id=TENANT, home_region="us-east", box_region="us-east",
        jurisdiction="us", question=QUESTION, passages=[passage], features={"form": "written"},
        matter_id=matter["id"], pool=SimpleNamespace(requires_source_links=True),
    )
    assert answer["ok"], answer
    assert answer["cost_usd"] == 0
    assert answer["state"] == ("released" if review == "off" else "in_review")
    memo = answer["memo"]
    assert memo["text"].endswith("\n\n" + NOTICE)
    assert memo["text"].count(NOTICE) == 1
    assert memo["sources"] == [{"citation": passage["citation"], "quote": passage["text"], "url": passage["url"]}]
    saved = await store.get_document(TENANT, answer["document_id"])
    assert saved["body"] == memo["text"]
    assert saved["sources"] == memo["sources"]
    assert await store.get_document("another-tenant", answer["document_id"]) is None

    standalone = await write_memo(
        ladder, None, tenant_id=TENANT, home_region="us-east", box_region="us-east",
        card_id=card["id"], features={"form": "written"}, question=QUESTION, smooth=False,
    )
    assert standalone["ok"], standalone
    assert standalone["model_calls"] == 0
    assert standalone["memo"]["text"].endswith("\n\n" + NOTICE)
    assert standalone["memo"]["text"].count(NOTICE) == 1


@pytest.mark.asyncio
async def test_answer_without_verified_link_is_not_saved():
    ladder, store, matter, _, passage = await saved_law_card()
    answer = await answer_question(
        store, ladder, None, tenant_id=TENANT, home_region="us-east", box_region="us-east",
        jurisdiction="us", question=QUESTION, passages=[{**passage, "url": ""}],
        features={"form": "written"}, matter_id=matter["id"], pool=SimpleNamespace(requires_source_links=True),
    )
    assert answer["ok"] is False
    assert answer["reason"] == "could not verify"
    assert store.documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("region,jurisdiction", [("us-east", "us"), ("za", "za")])
@pytest.mark.parametrize("prior_notice", [None, False, True])
async def test_new_and_reused_drafts_save_and_download_the_notice_once(region, jurisdiction, prior_notice):
    store, ladder = MemoryStore(), LadderMemory()
    await store.register_tenant(TENANT, region)
    await ladder.register_tenant(TENANT, region)
    matter = await store.add_matter({"tenant_id": TENANT, "jurisdiction": jurisdiction})
    if prior_notice is not None:
        # Offline-only prior, never installed as a clause or live matter.
        await ladder.add_worked({
            "region": region, "tenant_id": TENANT, "source": "released", "material_type": "client_work",
            "jurisdiction": jurisdiction, "fact_pattern": {"template": "nda", "features": {},
                "text": "Mutual NDA." + ("\n\n" + NOTICE if prior_notice else "")},
        })
    result = await draft_contract(
        store, ladder, None, tenant_id=TENANT, home_region=region, box_region=region,
        template="nda", jurisdiction=jurisdiction, narrative="A mutual NDA.", matter_id=matter["id"],
    )
    assert result["ok"], result
    assert result["model_calls"] == 0
    assert result["reused"] is (prior_notice is not None)
    assert result["text"].endswith("\n\n" + NOTICE)
    assert result["text"].count(NOTICE) == 1
    saved = await store.get_document(TENANT, result["document_id"])
    assert saved["body"] == result["text"]
    with ZipFile(BytesIO(base64.b64decode(result["docx_base64"]))) as package:
        xml = ElementTree.fromstring(package.read("word/document.xml"))
    word_text = "".join(xml.itertext())
    assert word_text.count(NOTICE) == 1
    assert word_text.endswith(NOTICE)
    # The output notice is the already-approved footer, character for character.
    assert f'>{NOTICE}</p>' in (ROOT / "workspace/app/zenas/view.tsx").read_text()


@pytest.fixture(autouse=True)
def offline_docassemble(monkeypatch):
    from zenas_docassemble.api_stub import install
    return install(monkeypatch)
