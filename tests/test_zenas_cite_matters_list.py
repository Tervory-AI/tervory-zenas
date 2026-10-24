"""Lane E: cite memo quotes + GET /matters list (M1/M3 gaps)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.core.engine import compose_memo  # noqa: E402
from agents.zenas.core.selftest import _verbatim_quote  # noqa: E402
from agents.zenas.core.service import answer_question, list_matters  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.store import MemoryStore  # noqa: E402


def test_compose_memo_appends_element_quotes() -> None:
    memo = compose_memo(
        {
            "question": "Is a written NDA enforceable?",
            "elements": [
                {
                    "id": "written",
                    "test": "The promise is in writing.",
                    "citation": "8 Del. C. § 101",
                    "quote": "A written confidentiality promise is enforceable.",
                }
            ],
        },
        [{"element_id": "written", "finding": "met", "support": "form=written", "missing": ""}],
    )
    assert "A written confidentiality promise is enforceable." in memo["rule"]
    assert "A written confidentiality promise is enforceable." in memo["text"]
    assert "8 Del. C. § 101" in memo["citations"]


def test_verbatim_quote_skips_judge_byline() -> None:
    text = (
        "CIRCUIT JUDGE\n"
        "After stating the facts the court held as follows.\n"
        "A written confidentiality promise is enforceable under this statute.\n"
    )
    quote = _verbatim_quote(text)
    assert quote
    assert "circuit judge" not in quote.lower()
    assert "A written confidentiality promise is enforceable" in quote


@pytest.mark.asyncio
async def test_list_matters_is_tenant_scoped_and_released_only() -> None:
    store = MemoryStore()
    await store.register_tenant("00000000-0000-0000-0000-0000000000a1", "us-east")
    await store.register_tenant("00000000-0000-0000-0000-0000000000b2", "us-east")
    mine = await store.add_matter(
        {
            "id": "00000000-0000-0000-0000-0000000000c1",
            "tenant_id": "00000000-0000-0000-0000-0000000000a1",
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Supply",
            "parties": [],
            "status": "open",
            "created_by": "zena",
            "created_at": "2026-10-24T12:00:00",
        }
    )
    await store.add_matter(
        {
            "id": "00000000-0000-0000-0000-0000000000c2",
            "tenant_id": "00000000-0000-0000-0000-0000000000b2",
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Other tenant",
            "parties": [],
            "status": "open",
            "created_by": "other",
            "created_at": "2026-10-24T12:01:00",
        }
    )
    await store.add_document(
        {
            "id": "00000000-0000-0000-0000-0000000000d1",
            "matter_id": mine["id"],
            "tenant_id": "00000000-0000-0000-0000-0000000000a1",
            "kind": "memo",
            "version": 1,
            "body": "draft body",
            "sources": [{"citation": "8 Del. C. § 101"}],
            "state": "in_review",
        }
    )
    await store.add_document(
        {
            "id": "00000000-0000-0000-0000-0000000000d2",
            "matter_id": mine["id"],
            "tenant_id": "00000000-0000-0000-0000-0000000000a1",
            "kind": "memo",
            "version": 2,
            "body": "released body",
            "sources": [{"citation": "8 Del. C. § 101"}],
            "state": "released",
        }
    )
    listed = await list_matters(
        store,
        tenant_id="00000000-0000-0000-0000-0000000000a1",
        home_region="us-east",
        box_region="us-east",
    )
    assert listed["ok"] is True
    assert len(listed["matters"]) == 1
    assert listed["matters"][0]["id"] == mine["id"]
    assert listed["matters"][0]["title"] == "Supply"
    assert len(listed["matters"][0]["documents"]) == 1
    assert listed["matters"][0]["documents"][0]["body"] == "released body"
    assert listed["matters"][0]["documents"][0]["state"] == "released"


@pytest.mark.asyncio
async def test_answer_question_passes_model_into_apply_when_features_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ask Workspace sends no features; apply_law must keep the model (not None)."""
    from agents.zenas.core import service as svc

    ladder = LadderMemory()
    store = MemoryStore()
    tenant = "00000000-0000-0000-0000-0000000000a1"
    await ladder.register_tenant(tenant, "us-east")
    await store.register_tenant(tenant, "us-east")
    await store.add_matter(
        {
            "id": "00000000-0000-0000-0000-0000000000c1",
            "tenant_id": tenant,
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Supply",
            "parties": [],
            "status": "open",
            "created_by": "zena",
        }
    )
    passage = {
        "doc_id": "d1",
        "version_id": "v1",
        "citation": "8 Del. C. § 101",
        "text": "A written confidentiality promise is enforceable.",
        "jurisdiction": "us",
        "status": "effective",
    }
    card_payload = {
        "elements": [
            {
                "id": "written",
                "test": "The promise is in writing.",
                "citation": "8 Del. C. § 101",
                "quote": "A written confidentiality promise is enforceable.",
            }
        ],
        "exceptions": [],
        "defences": [],
        "remedies": [],
        "decision_table": [],
    }

    class Model:
        async def complete(self, messages, *, task_type: str, provider: str) -> dict:
            if task_type == "zenas.apply":
                return {
                    "content": '{"finding":"met","support":"the promise is written","missing":""}',
                    "cost_usd": 0.01,
                    "model": "luna",
                }
            import json

            return {"content": json.dumps(card_payload), "cost_usd": 0.05, "model": "luna"}

    seen: dict[str, object] = {}
    _orig = svc.apply_law

    async def wrapped(ladder_arg, model_arg, **kwargs):
        seen["model"] = model_arg
        return await _orig(ladder_arg, model_arg, **kwargs)

    async def review_off(*_a, **_k):
        return False

    monkeypatch.setattr(svc, "apply_law", wrapped)
    monkeypatch.setattr(svc, "review_required", review_off)
    model = Model()
    result = await answer_question(
        store,
        ladder,
        model,
        tenant_id=tenant,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question="Is a mutual NDA enforceable under Delaware law in the United States?",
        passages=[passage],
        features={},
        matter_id="00000000-0000-0000-0000-0000000000c1",
    )
    assert "model" in seen, f"apply_law was not called; result={result}"
    assert seen["model"] is model
    assert result.get("reason") != "no model is configured"
