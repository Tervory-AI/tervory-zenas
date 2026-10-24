"""Ask Zenas fills passages from the knowledge pool when the client sends none."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.core.law_pool import AsyncpgLawPool, bind_law_pool
from agents.zenas.core.ladder_store import LadderMemory
from agents.zenas.core.service import _passages_from_pool, _search_query, answer_question
from agents.zenas.core.store import MemoryStore as MattersStore
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.chunk import chunk_document
from agents.zenas.pool.model import Section
from agents.zenas.pool.store import MemoryStore

TENANT = "00000000-0000-0000-0000-0000000000b1"
MATTER = "00000000-0000-0000-0000-0000000000b2"
# Law-only quote (same shape as Z7). Search terms live in the heading so the
# Ask question can hit MemoryStore without putting client words on the card.
PASSAGE_TEXT = "A written confidentiality promise is enforceable."
HEADING = "Mutual NDA Delaware writing enforceable"
CITATION = "8 Del. C. § 101"
QUESTION = "Is a mutual NDA enforceable under Delaware law in the United States?"
CARD = {
    "elements": [
        {
            "id": "written",
            "test": "The promise is in writing.",
            "citation": CITATION,
            "quote": PASSAGE_TEXT,
        }
    ],
    "exceptions": [],
    "defences": [],
    "remedies": [],
    "decision_table": [{"element_id": "written", "when": {"form": "written"}, "finding": "met"}],
}


class Model:
    def __init__(self, content: str) -> None:
        self.content = content

    async def complete(self, messages, *, task_type: str, provider: str) -> dict:
        return {"content": self.content, "cost_usd": 0.0, "model": "luna"}


async def _seed_pool() -> MemoryStore:
    source = next(item for item in sources_for_region("us-east") if item.source_id == "govinfo-uscode")
    store = MemoryStore(home_region="us-east")
    await store.record_source(source)
    doc = build_document(
        source,
        citation=CITATION,
        title="Formation of corporations [TEST]",
        level="state",
        kind="statute",
        body="Delaware",
        sections=[Section("(a)", HEADING, PASSAGE_TEXT, [])],
        url="https://example.test/delcode/8/101",
        status="in_force",
        doc_date=date(2020, 1, 1),
        effective_from=date(2020, 1, 1),
    )
    chunks = chunk_document(doc)
    for chunk in chunks:
        chunk.citation = CITATION
        chunk.heading = HEADING
    assert await store.upsert_version(doc, chunks, on=date(2020, 1, 1))
    return store


def test_search_query_drops_stop_words() -> None:
    cleaned = _search_query(QUESTION)
    assert "is" not in cleaned.split()
    assert "under" not in cleaned.split()
    assert "united" not in cleaned.split()
    assert "?" not in cleaned
    assert "mutual" in cleaned
    assert "nda" in cleaned
    assert "delaware" in cleaned
    assert "enforceable" in cleaned


def test_passages_from_pool_search_returns_fixture_text() -> None:
    async def run() -> None:
        pool = await _seed_pool()
        found = await _passages_from_pool(
            pool,
            query=QUESTION,
            citation="",
            jurisdiction="us",
            as_at=None,
            level=None,
            kind=None,
        )
        assert found
        assert any(PASSAGE_TEXT in (row.get("text") or "") for row in found)
        assert any(CITATION in (row.get("citation") or "") for row in found)

    asyncio.run(run())


def test_answer_question_fills_empty_passages_from_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZENAS_REVIEW", raising=False)

    async def run() -> None:
        pool = await _seed_pool()
        ladder = LadderMemory()
        matters = MattersStore()
        await ladder.register_tenant(TENANT, "us-east")
        await matters.add_matter(
            {
                "id": MATTER,
                "tenant_id": TENANT,
                "jurisdiction": "us",
                "area": "contracts",
                "title": "NDA [TEST]",
                "parties": [],
                "status": "open",
                "created_by": "tester",
            }
        )
        good = await answer_question(
            matters,
            ladder,
            Model(json.dumps(CARD)),
            tenant_id=TENANT,
            home_region="us-east",
            box_region="us-east",
            jurisdiction="us",
            question=QUESTION,
            passages=[],  # Ask Workspace sends none
            features={"form": "written"},
            matter_id=MATTER,
            pool=pool,
        )
        assert good["ok"] is True, good
        assert good["delivered"] is True
        assert CITATION in good["memo"]["text"]

        empty = await answer_question(
            matters,
            ladder,
            Model(json.dumps(CARD)),
            tenant_id=TENANT,
            home_region="us-east",
            box_region="us-east",
            jurisdiction="us",
            question=QUESTION,
            passages=[],
            features={"form": "written"},
            matter_id=MATTER,
            pool=None,
        )
        assert empty["ok"] is False
        assert empty["reason"] == "could not verify"

    asyncio.run(run())



def test_law_only_ignores_pool_version_id_dates() -> None:
    from agents.zenas.core.engine import LAW_ONLY, law_only_problem

    card = {
        "question": "",
        "elements": [
            {
                "id": "written",
                "test": "The promise is in writing.",
                "citation": CITATION,
                "quote": PASSAGE_TEXT,
                "doc_id": "govinfo-uscode:8 delc 101",
                "version_id": "govinfo-uscode:8 delc 101:abc:2020-01-01:unseen",
            }
        ],
        "exceptions": [],
        "defences": [],
        "remedies": [],
        "sources": [
            {
                "doc_id": "govinfo-uscode:8 delc 101",
                "version_id": "govinfo-uscode:8 delc 101:abc:2020-01-01:unseen",
                "citation": CITATION,
            }
        ],
        "decision_table": [],
    }
    assert law_only_problem(card, []) is None
    dirty = {
        **card,
        "elements": [
            {
                **card["elements"][0],
                "quote": "Acme Corp signed on 2020-01-01.",
            }
        ],
    }
    assert law_only_problem(dirty, []) == LAW_ONLY


def test_bind_law_pool_sets_app_state() -> None:
    class _Pool:
        def acquire(self):
            raise AssertionError("not used in bind test")

    app = SimpleNamespace(state=SimpleNamespace(pool=_Pool()))
    bridge = bind_law_pool(app, home_region="us-east")
    assert app.state.law_pool is bridge
    assert isinstance(bridge, AsyncpgLawPool)
    assert bridge.home_region == "us-east"
