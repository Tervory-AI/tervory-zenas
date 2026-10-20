import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.core.converted_cards import link_card
from agents.zenas.core.ladder_store import LadderMemory
from agents.zenas.core.selftest import run_selftest


LAW = "A supply agreement requires a written offer and acceptance."


class Pool:
    def __init__(self, region):
        self.home_region = region
        self.queries = []
        self.version = "v1"

    async def search_text(self, query, **kwargs):
        self.queries.append({"query": query, **kwargs})
        if kwargs["kind"] != "statute":
            return []
        return [SimpleNamespace(
            chunk_id="section", document_id="statute", version_id=self.version,
            citation="Civil statute section 1", heading="Supply", text=LAW,
            path="1", jurisdiction=kwargs["jurisdiction"],
        )]


class Model:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, *, task_type, provider):
        self.calls.append((task_type, messages))
        return json.dumps({"elements": [{
            "id": "written", "test": "Written agreement",
            "citation": "Civil statute section 1", "quote": LAW,
        }], "decision_table": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("region,jurisdiction", [("za", "za"), ("us-east", "us")])
async def test_converted_holdout_gets_law_only_card_and_reuses_it(region, jurisdiction):
    ladder = LadderMemory()
    row = await ladder.add_worked({
        "region": region, "jurisdiction": jurisdiction, "source": "case", "tenant_id": None,
        "material_type": "public_record", "fact_pattern": {
            "facts": "PRIVATE-TO-EVALUATION facts about a supply agreement",
            "question": "Was the supply agreement binding?",
        }, "holdout": True, "pool_doc_id": "case1",
        "element_findings": [{"element_id": "written", "finding": "met"}],
        "outcome": "met",
    })
    pool, model = Pool(region), Model()
    assert await link_card(pool, ladder, model, row, region=region)
    assert row["rule_card_ids"]
    assert row["area"] == "contracts"
    assert len(model.calls) == 1
    assert model.calls[0][0] == "zenas.rule_card"
    assert f"What are the legal elements for contracts in {jurisdiction}?" in str(model.calls)
    assert "PRIVATE-TO-EVALUATION" not in str(model.calls)
    assert all(item["jurisdiction"] == jurisdiction for item in pool.queries)
    assert await link_card(pool, ladder, model, row, region=region)
    assert len(model.calls) == 1
    scores = await run_selftest(ladder, region=region)
    assert "no rule card" not in str(scores)
    assert ladder.rule_cards[0]["status"] == "draft"


@pytest.mark.asyncio
async def test_private_criminal_and_other_region_never_build():
    ladder, model = LadderMemory(), Model()
    row = {
        "id": "case", "region": "za", "jurisdiction": "za", "source": "case",
        "tenant_id": None, "material_type": "public_record",
        "fact_pattern": {"facts": "criminal prosecution of a supply agreement"},
    }
    assert not await link_card(Pool("za"), ladder, model, row, region="za")
    row["fact_pattern"] = {"facts": "supply agreement"}
    assert not await link_card(Pool("us-east"), ladder, model, row, region="za")
    row["tenant_id"] = "private"
    assert not await link_card(Pool("za"), ladder, model, row, region="za")
    assert model.calls == []


async def cases(ladder):
    return [await ladder.add_worked({
        "region": "za", "jurisdiction": "za", "source": "case", "tenant_id": None,
        "material_type": "public_record", "fact_pattern": {"facts": phrase},
    }) for phrase in ("supply agreement", "mutual nda", "another supply agreement")]


@pytest.mark.asyncio
async def test_three_cases_share_one_area_card_until_source_superseded():
    ladder, pool, model = LadderMemory(), Pool("za"), Model()
    for row in await cases(ladder):
        assert await link_card(pool, ladder, model, row, region="za")
    assert len(model.calls) == 1 and len(ladder.rule_cards) == 1
    card = ladder.rule_cards[0]
    assert card["question"] == "What are the legal elements for contracts in za?"
    assert card["sources"] and "Source set" not in card["question"]
    original_search = pool.search_text
    async def with_extra_source(*args, **kwargs):
        found = await original_search(*args, **kwargs)
        return found + ([SimpleNamespace(**{**vars(found[0]), "document_id": "another-statute", "chunk_id": "extra"})] if found else [])
    pool.search_text = with_extra_source
    for row in await cases(ladder):
        assert await link_card(pool, ladder, model, row, region="za")
    assert len(model.calls) == 1  # Adding an unrelated source does not supersede the card.
    pool.version = "v2"
    for row in await cases(ladder):
        assert await link_card(pool, ladder, model, row, region="za")
    assert len(model.calls) == 2
    assert card["status"] == "stale"
    assert len([card for card in ladder.rule_cards if card["status"] != "stale"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_failed_build_once_for_three_cases_and_no_retry_until_pool_changes(raises, caplog):
    from agents.zenas.core.convert import _link_converted_card
    class BadModel(Model):
        async def complete(self, *args, **kwargs):
            result = await super().complete(*args, **kwargs)
            if raises:
                raise RuntimeError("fixture failure")
            return result.replace(LAW, "Unsupported quote")
    ladder, pool, model = LadderMemory(), Pool("za"), BadModel()
    rows = await cases(ladder)
    for _ in range(2):
        for row in rows:
            await _link_converted_card(pool, ladder, model, row, region="za")
    assert len(model.calls) == 1
    assert all(card["status"] == "stale" and not card["active"] for card in ladder.rule_cards)
    assert all(not row.get("rule_card_ids") for row in rows)
    failures = [run for run in ladder.runs if run["kind"] == "converted_card_failed"]
    assert len(failures) == 1
    if raises:
        assert any(record.exc_info for record in caplog.records)
    pool.version = "v2"
    for row in rows:
        await _link_converted_card(pool, ladder, model, row, region="za")
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_topic_uses_word_boundaries_and_only_own_area_phrases():
    ladder, pool, model = LadderMemory(), Pool("za"), Model()
    row = (await cases(ladder))[0]
    row["fact_pattern"] = {"facts": "trademarked product intellectual property"}
    assert await link_card(pool, ladder, model, row, region="za")
    assert pool.queries[0]["query"] == "intellectual property"
    pool.queries.clear()
    row["fact_pattern"] = {"facts": "supply agreement and ofac sanctions screening"}
    assert await link_card(pool, ladder, model, row, region="za")
    assert all(query["query"] in {"regulatory compliance", "compliance programme", "compliance program"}
               for query in pool.queries)
