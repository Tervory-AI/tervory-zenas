import json
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import pytest

from agents.zenas.core import law_pool as bridge
from agents.zenas.core.engine import cite_check
from agents.zenas.core.ladder_store import LadderMemory
from agents.zenas.core.service import _passages_from_pool, answer_question
from agents.zenas.core.store import MemoryStore as MatterStore
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.store import MemoryStore

RECORDINGS = Path(__file__).with_name("recordings")
QUESTION = "Under the Companies Act, must the business and affairs of a company be managed by its board?"
TENANT = "00000000-0000-0000-0000-0000000000a1"


@pytest.mark.asyncio
async def test_za_civil_answer_uses_ingested_section_and_preserves_link_in_saved_memo(monkeypatch, clock_and_token):
    monkeypatch.setenv("ZENAS_REVIEW", "off")
    work = json.loads((RECORDINGS / "works-1.json").read_text())["results"][0]

    async def get(url, headers):
        if url.endswith("/toc.json"):
            return (RECORDINGS / "toc.json").read_bytes()
        if ".html?" in url:
            return (RECORDINGS / "section.html").read_bytes()
        return json.dumps(work).encode()

    source = next(item for item in CATALOGUE if item.source_id == "companies-act")
    pool = MemoryStore()
    report = await load_wave(pool, region="za", only=[source], live=True, http_get=get, on=date(2026, 10, 28))
    assert report["sources"][0]["last_status"] == "ok"

    class Connection:
        async def fetch(self, query, jurisdiction, region, versions):
            assert "d.jurisdiction=$1 AND s.region=$2" in query
            assert (jurisdiction, region) == ("za", "za")
            return [{"document_id": doc.id, "version_id": doc.version_id, "url": doc.url, "title": doc.title,
                     "kind": doc.kind, "level": doc.level, "status": doc.status}
                    for doc in pool.docs.values() if doc.version_id in versions]

    class Connections:
        @asynccontextmanager
        async def acquire(self):
            yield Connection()

    # The real production bridge and metadata query run; only the database
    # transport is replaced by the already-ingested in-memory pool.
    monkeypatch.setattr(bridge, "PostgresStore", lambda conn, home_region: pool)
    law = bridge.AsyncpgLawPool(Connections(), home_region="za")
    passages = await _passages_from_pool(law, query=QUESTION, citation="", jurisdiction="za", as_at=None, level=None, kind=None)
    assert passages
    passage = next(row for row in passages if "business and affairs" in row["text"])
    assert passage["citation"] == "Act 71 of 2008, s 66(1)"
    assert "2024-01-01" in passage["url"] and passage["url"].endswith("#sec_66")

    ladder, matters = LadderMemory(), MatterStore()
    await ladder.register_tenant(TENANT, "za")
    await matters.register_tenant(TENANT, "za")
    matter = await matters.add_matter({"tenant_id": TENANT, "jurisdiction": "za", "title": QUESTION})
    await ladder.add_rule_card({
        "region": "za", "tenant_id": TENANT, "jurisdiction": "za", "question": QUESTION,
        "status": "lawyer_approved", "elements": [{"id": "board", "test": passage["text"],
            "citation": passage["citation"], "quote": passage["text"]}],
        "decision_table": [{"element_id": "board", "when": {"entity": "company"}, "finding": "met"}],
    })
    result = await answer_question(
        matters, ladder, None, tenant_id=TENANT, home_region="za", box_region="za", jurisdiction="za",
        question=QUESTION, passages=[], features={"entity": "company"}, matter_id=matter["id"], pool=law,
    )
    assert result["ok"] and result["delivered"], result
    assert result["cost_usd"] == 0
    source_link, = result["memo"]["sources"]
    assert source_link == {"citation": passage["citation"], "quote": passage["text"], "url": passage["url"]}
    assert cite_check([source_link], passages)["passed"]
    assert "Zenas provides legal information, not legal advice." in result["memo"]["text"]
    saved = await matters.get_document(TENANT, result["document_id"])
    assert saved["sources"] == result["memo"]["sources"]
    assert saved["body"] == result["memo"]["text"]

    refused = await answer_question(
        matters, ladder, None, tenant_id=TENANT, home_region="za", box_region="za", jurisdiction="za",
        question="Can you defend a criminal charge?", passages=[], features={}, matter_id=matter["id"], pool=law,
    )
    assert not refused["ok"] and "criminal" in refused["reason"]
    assert len(matters.documents) == 1
