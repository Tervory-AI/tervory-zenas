"""Zenas on the ladder. Models are mocks. No database and no network."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import skala

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.zenas.core.engine import (  # noqa: E402
    IDENTIFIER_REFUSED,
    prepare_prompt,
    region_refusal,
    sharing_mode,
)
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.matters import region_refusal as matters_region_refusal  # noqa: E402
from agents.zenas.core.service import (  # noqa: E402
    apply_law,
    approve_rule_card,
    build_rule_card,
    copy_across_regions,
    extract_case_facts,
    ladder_metrics,
    mark_stale,
    match_matters,
    release_matter,
    remember_for_tenant,
    retrieve,
    review_queue,
    write_memo,
)
from agents.zenas.core.store import MemoryStore  # noqa: E402

TENANT_A = "00000000-0000-0000-0000-0000000000a1"
TENANT_B = "00000000-0000-0000-0000-0000000000b2"
SECRET = "ACME-SECRET-PARTY"
SA_ID = "8001015009087"
QUESTION = "Is a mutual NDA enforceable under Delaware law in the United States?"
PASSAGE = {
    "doc_id": "d1",
    "version_id": "v1",
    "citation": "8 Del. C. § 101",
    "text": "A written confidentiality promise is enforceable.",
    "jurisdiction": "us",
    "status": "effective",
}
CARD_JSON = {
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
SKILLS = (
    "zenas.retrieve",
    "zenas.cite_check",
    "zenas.rule_card",
    "zenas.case_facts",
    "zenas.match",
    "zenas.apply",
    "zenas.compose",
    "zenas.plain_language",
)


class Model:
    def __init__(self, content: str, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.calls = 0
        self.sent: list[dict] = []

    async def complete(self, messages, *, task_type: str, provider: str) -> dict:
        if self.fail:
            raise AssertionError("the model was called")
        self.calls += 1
        self.sent.append({"task_type": task_type, "provider": provider, "messages": messages})
        return {"content": self.content, "cost_usd": 0.05 if task_type == "zenas.rule_card" else 0.01, "model": "luna"}


class SkillModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0
        self.messages: list[object] = []

    async def model(self, messages, *_args, **_kwargs) -> str:
        self.calls += 1
        self.messages.append(messages)
        return self.content


class Memory:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    async def remember(self, tenant_id: str, text: str, kind: str) -> None:
        self.rows.append((tenant_id, text, kind))


def _load_run(package: Path):
    path = package / "run.py"
    spec = importlib.util.spec_from_file_location(f"run_{package.parent.name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


def _cases(package: Path) -> list[dict]:
    rows = []
    for line in (package / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@pytest.mark.asyncio
async def test_request_home_region_only_cross_checks_the_tenant_record() -> None:
    ladder = LadderMemory()
    missing = await build_rule_card(
        ladder,
        None,
        Model("{}", fail=True),
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question=QUESTION,
        passages=[PASSAGE],
    )
    assert missing["ok"] is False
    assert missing["reason"] == "home region is not on the tenant record"
    assert ladder.rule_cards == []

    await ladder.register_tenant(TENANT_A, "us-east")
    claimed = await build_rule_card(
        ladder,
        None,
        Model("{}", fail=True),
        tenant_id=TENANT_A,
        home_region="za",
        box_region="us-east",
        jurisdiction="us",
        question=QUESTION,
        passages=[PASSAGE],
    )
    assert claimed["ok"] is False
    assert claimed["reason"] == "home region does not match the tenant record"
    assert ladder.rule_cards == []

    za_box = LadderMemory()
    await za_box.register_tenant(TENANT_A, "za")
    abroad = await build_rule_card(
        za_box,
        None,
        Model("{}", fail=True),
        tenant_id=TENANT_A,
        home_region="za",
        box_region="us-east",
        jurisdiction="za",
        question=QUESTION,
        passages=[PASSAGE],
    )
    assert abroad["ok"] is False
    assert abroad["reason"] == "needs the South Africa region"
    assert za_box.rule_cards == []


@pytest.mark.asyncio
async def test_review_queue_shape_stays_in_the_home_region() -> None:
    matters = MemoryStore()
    ladder = LadderMemory()
    await matters.register_tenant(TENANT_A, "us-east")
    matter = await matters.add_matter(
        {
            "tenant_id": TENANT_A,
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Mutual NDA",
            "parties": [],
            "status": "open",
            "created_by": "ada",
        }
    )
    document = await matters.add_document(
        {
            "matter_id": matter["id"],
            "tenant_id": TENANT_A,
            "kind": "memo",
            "version": 1,
            "body": "A written confidentiality promise is enforceable.",
            "sources": [{"citation": "8 Del. C. § 101", "quote": "A written confidentiality promise is enforceable."}],
            "state": "in_review",
        }
    )
    await ladder.add_worked(
        {
            "region": "us-east",
            "tenant_id": None,
            "source": "case",
            "material_type": "public_record",
            "jurisdiction": "us",
            "fact_pattern": {"narrative": "A written confidentiality promise is enforceable.", "features": {}},
            "element_findings": [{"element_id": "written", "finding": "met"}],
            "outcome": "enforceable",
        }
    )
    await ladder.add_worked(
        {
            "region": "za",
            "tenant_id": TENANT_A,
            "source": "released",
            "jurisdiction": "za",
            "fact_pattern": {"narrative": SECRET, "features": {}},
            "element_findings": [{"element_id": "party", "finding": "met"}],
            "outcome": SECRET,
        }
    )
    queued = await review_queue(
        matters,
        ladder,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
    )
    assert queued["ok"] is True
    assert len(queued["items"]) == 1
    item = queued["items"][0]
    assert item["document"]["id"] == document["id"]
    assert item["document"]["state"] == "in_review"
    assert item["document"]["sources"][0]["citation"] == "8 Del. C. § 101"
    assert item["matter"]["title"] == "Mutual NDA"
    assert item["matter"]["jurisdiction"] == "us"
    assert item["matches"]
    assert item["matches"][0]["outcome"] == "enforceable"
    assert item["matches"][0]["element_findings"][0]["element"] == "written"
    assert SECRET not in json.dumps(queued)
    wrong = await review_queue(
        matters,
        ladder,
        tenant_id=TENANT_A,
        home_region="za",
        box_region="us-east",
        jurisdiction="us",
    )
    assert wrong["ok"] is False
    assert wrong["reason"] == "home region does not match the tenant record"


@pytest.mark.asyncio
async def test_released_matters_are_shared_inside_one_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """Released worked matters are shared in the home region. Drafts are not, and the other region never sees them."""
    monkeypatch.delenv("ZENAS_MATTER_SHARING", raising=False)
    assert sharing_mode() == "region"
    assert sharing_mode("tenant") == "tenant"
    monkeypatch.setenv("ZENAS_MATTER_SHARING", "tenant")
    assert sharing_mode() == "tenant"
    monkeypatch.setenv("ZENAS_MATTER_SHARING", "elsewhere")
    assert sharing_mode() == "region"
    monkeypatch.delenv("ZENAS_MATTER_SHARING", raising=False)

    tenant_c = "00000000-0000-0000-0000-0000000000c3"
    draft_body = "DRAFT-ONLY-NEVER-SHARED"
    pending_body = "IN-REVIEW-NOT-RELEASED"
    unreleased_narrative = "UNRELEASED-WORKED-NOT-SHARED"
    matters = MemoryStore()
    ladder = LadderMemory()
    for store in (matters, ladder):
        await store.register_tenant(TENANT_A, "us-east")
        await store.register_tenant(TENANT_B, "us-east")
        await store.register_tenant(tenant_c, "za")
    matter = await matters.add_matter(
        {
            "tenant_id": TENANT_A,
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Mutual NDA",
            "parties": ["Northwind"],
            "status": "open",
            "created_by": "ada",
        }
    )
    draft = await matters.add_document(
        {
            "matter_id": matter["id"],
            "tenant_id": TENANT_A,
            "kind": "memo",
            "version": 1,
            "body": draft_body,
            "sources": [],
            "state": "draft",
        }
    )
    pending = await matters.add_document(
        {
            "matter_id": matter["id"],
            "tenant_id": TENANT_A,
            "kind": "memo",
            "version": 2,
            "body": pending_body,
            "sources": [],
            "state": "in_review",
        }
    )
    ready = await matters.add_document(
        {
            "matter_id": matter["id"],
            "tenant_id": TENANT_A,
            "kind": "memo",
            "version": 3,
            "body": "The mutual NDA was in writing.",
            "sources": [],
            "state": "in_review",
        }
    )
    released = await release_matter(
        matters,
        ladder,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        matter_id=matter["id"],
        document_id=ready["id"],
        reviewer="Ada Counsel",
        admission_no="12345",
        jurisdiction="us",
        rule_card_ids=[],
        features={"form": "written"},
        narrative="The mutual NDA was in writing.",
        element_findings=[{"element_id": "written", "finding": "met", "basis": ["form"]}],
        outcome="enforceable",
        memo="Issue: mutual NDA. The promise is in writing under Delaware law in the United States.",
    )
    assert released["ok"] is True
    unreleased = await ladder.add_worked(
        {
            "region": "us-east",
            "tenant_id": TENANT_A,
            "source": "draft",
            "jurisdiction": "us",
            "fact_pattern": {"narrative": unreleased_narrative, "features": {"form": "written"}},
            "element_findings": [{"element_id": "written", "finding": "met", "basis": ["form"]}],
            "outcome": "not released",
        }
    )
    b_matter = await matters.add_matter(
        {
            "tenant_id": TENANT_B,
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Another mutual NDA",
            "parties": [],
            "status": "open",
            "created_by": "bea",
        }
    )
    await matters.add_document(
        {
            "matter_id": b_matter["id"],
            "tenant_id": TENANT_B,
            "kind": "memo",
            "version": 1,
            "body": "The mutual NDA was in writing.",
            "sources": [],
            "state": "in_review",
        }
    )

    matched = await match_matters(
        ladder,
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        rule_card_ids=[],
        features={"form": "written"},
        narrative="The mutual NDA was in writing.",
        question=QUESTION,
    )
    assert matched["ok"] is True
    seen = {item["worked_matter_id"] for item in matched["matches"]}
    assert released["worked_matter_id"] in seen
    assert unreleased["id"] not in seen
    blob = json.dumps(matched)
    assert draft_body not in blob
    assert pending_body not in blob
    assert unreleased_narrative not in blob

    queued = await review_queue(
        matters,
        ladder,
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
    )
    assert queued["ok"] is True
    document_ids = {item["document"]["id"] for item in queued["items"]}
    assert draft["id"] not in document_ids
    assert pending["id"] not in document_ids
    assert ready["id"] not in document_ids
    match_ids = {item["id"] for row in queued["items"] for item in row["matches"]}
    assert released["worked_matter_id"] in match_ids
    assert unreleased["id"] not in match_ids
    queued_blob = json.dumps(queued)
    assert draft_body not in queued_blob
    assert pending_body not in queued_blob
    assert unreleased_narrative not in queued_blob

    abroad = await match_matters(
        ladder,
        tenant_id=tenant_c,
        home_region="za",
        box_region="za",
        jurisdiction="za",
        rule_card_ids=[],
        features={"form": "written"},
        narrative="The mutual NDA was in writing.",
        question="Is a mutual NDA enforceable under South African law?",
    )
    assert abroad["ok"] is True
    assert released["worked_matter_id"] not in {item["worked_matter_id"] for item in abroad["matches"]}
    assert draft_body not in json.dumps(abroad)
    assert unreleased_narrative not in json.dumps(abroad)
    za_queue = await review_queue(
        matters,
        ladder,
        tenant_id=tenant_c,
        home_region="za",
        box_region="za",
    )
    assert za_queue["ok"] is True
    assert za_queue["items"] == []
    assert released["worked_matter_id"] not in json.dumps(
        await ladder.visible_worked(region="za", tenant_id=tenant_c, sharing=sharing_mode())
    )

    monkeypatch.setenv("ZENAS_MATTER_SHARING", "tenant")
    private = await match_matters(
        ladder,
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        rule_card_ids=[],
        features={"form": "written"},
        narrative="The mutual NDA was in writing.",
        question=QUESTION,
    )
    assert released["worked_matter_id"] not in {item["worked_matter_id"] for item in private["matches"]}
    private_queue = await review_queue(
        matters,
        ladder,
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
    )
    private_ids = {item["id"] for row in private_queue["items"] for item in row["matches"]}
    assert released["worked_matter_id"] not in private_ids


def test_engine_copies_match_and_regions_match_the_box() -> None:
    core = (ROOT / "agents" / "zenas" / "core" / "engine.py").read_bytes()
    shared = (ROOT / "tank" / "skills" / "zenas.shared" / "engine.py").read_bytes()
    assert core == shared
    assert region_refusal("za", "us-east") == matters_region_refusal("za", "us-east")
    assert region_refusal("us-east", "za") == matters_region_refusal("us-east", "za")
    assert region_refusal("za", "za") is None


def test_za_prompt_is_masked_for_a_provider_outside_south_africa() -> None:
    text = f"Mutual NDA for ID {SA_ID}."
    blocked = prepare_prompt(text, home_region="za", provider="luna")
    assert blocked["ok"] is True
    assert SA_ID not in blocked["text"]
    assert "[SA_ID_1]" in blocked["text"]
    local = prepare_prompt(text, home_region="za", provider="za-local")
    assert local["ok"] is True
    assert SA_ID in local["text"]
    us = prepare_prompt(text, home_region="us-east", provider="luna")
    assert SA_ID in us["text"]

    def stuck(value: str) -> dict:
        return {"text": value, "matches": [{"kind": "sa_id", "value": SA_ID, "placeholder": "[SA_ID_1]"}]}

    refused = prepare_prompt(text, home_region="za", provider="openai", detect=stuck)
    assert refused["ok"] is False
    assert refused["reason"] == IDENTIFIER_REFUSED


@pytest.mark.asyncio
async def test_skill_evals() -> None:
    failed: list[str] = []
    for name in SKILLS:
        package = ROOT / "tank" / "skills" / name / "1.0.0"
        run = _load_run(package)
        assert (package / "CHANGELOG.md").is_file()
        for case in _cases(package):
            ctx = SkillModel(str(case.get("model") or "{}"))
            output = await run(case["input"], ctx)
            ok, why = skala.score_case(case["expected"], output, case.get("tolerance") or "exact")
            if not ok:
                failed.append(f"{name} {case['name']}: {why}")
            if case.get("group") == "model":
                assert ctx.calls == 1, case["name"]
            else:
                assert ctx.calls == 0, case["name"]
    assert not failed, "\n".join(failed)


@pytest.mark.asyncio
async def test_second_client_reuses_the_approved_card_for_free() -> None:
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "us-east")
    await ladder.register_tenant(TENANT_B, "us-east")
    model = Model(json.dumps(CARD_JSON))
    first = await build_rule_card(
        ladder,
        None,
        model,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question=QUESTION,
        passages=[PASSAGE],
        client_facts=SECRET,
    )
    assert first["ok"] is True
    assert first["reused"] is False
    assert first["cost_usd"] == 0.05
    assert first["approvable"] is True
    assert SECRET not in json.dumps(first["rule_card"])
    approved = await approve_rule_card(
        ladder,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        card_id=first["rule_card"]["id"],
        reviewer="Ada Counsel",
        admission_no="12345",
        jurisdiction="us",
        passages=[PASSAGE],
        changes="tightened the writing element",
    )
    assert approved["ok"] is True
    assert approved["rule_card"]["status"] == "lawyer_approved"
    second = await build_rule_card(
        ladder,
        None,
        model,
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question=QUESTION,
        passages=[PASSAGE],
    )
    assert second["ok"] is True
    assert second["reused"] is True
    assert second["cost_usd"] == 0
    assert second["model_calls"] == 0
    assert model.calls == 1
    assert second["rule_card"]["id"] == first["rule_card"]["id"]
    assert SECRET not in json.dumps(second)


@pytest.mark.asyncio
async def test_fake_citation_is_never_approved_and_a_change_marks_the_card_stale() -> None:
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "us-east")
    await ladder.register_tenant(TENANT_B, "us-east")
    fake = {
        "elements": [
            {
                "id": "fake",
                "test": "Invented.",
                "citation": "Fake v. Nobody, 1 F.3d 1",
                "quote": "This sentence is not in the pool.",
            }
        ]
    }
    built = await build_rule_card(
        ladder,
        None,
        Model(json.dumps(fake)),
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question=QUESTION,
        passages=[PASSAGE],
    )
    assert built["approvable"] is False
    refused = await approve_rule_card(
        ladder,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        card_id=built["rule_card"]["id"],
        reviewer="Ada Counsel",
        admission_no="12345",
        jurisdiction="us",
        passages=[PASSAGE],
    )
    assert refused["ok"] is False
    assert ladder.rule_cards[0]["status"] == "draft"

    real = await build_rule_card(
        ladder,
        None,
        Model(json.dumps(CARD_JSON)),
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question="Is a supply agreement enforceable under Delaware law in the United States?",
        passages=[PASSAGE],
    )
    await approve_rule_card(
        ladder,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        card_id=real["rule_card"]["id"],
        reviewer="Ada Counsel",
        admission_no="12345",
        jurisdiction="us",
        passages=[PASSAGE],
    )
    stale = await mark_stale(ladder, box_region="us-east", doc_id="d1", event="version")
    assert real["rule_card"]["id"] in stale["stale_ids"]
    again = await build_rule_card(
        ladder,
        None,
        Model(json.dumps(CARD_JSON)),
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question="Is a supply agreement enforceable under Delaware law in the United States?",
        passages=[PASSAGE],
    )
    assert again["reused"] is False
    assert again["model_calls"] == 1


@pytest.mark.asyncio
async def test_code_decides_a_table_and_a_case_is_reused() -> None:
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "us-east")
    await ladder.register_tenant(TENANT_B, "us-east")
    card = await ladder.add_rule_card(
        {
            "region": "us-east",
            "tenant_id": None,
            "jurisdiction": "us",
            "question": QUESTION,
            "elements": [
                {"id": "written", "test": "The promise is in writing.", "citation": "8 Del. C. § 101", "quote": PASSAGE["text"]},
                {"id": "signed", "test": "The parties signed.", "citation": "8 Del. C. § 101", "quote": PASSAGE["text"]},
            ],
            "sources": [{"doc_id": "d1", "version_id": "v1", "citation": "8 Del. C. § 101"}],
            "status": "lawyer_approved",
            "decision_table": [
                {"element_id": "written", "when": {"form": "written"}, "finding": "met"},
                {"element_id": "signed", "when": {"signed": True}, "finding": "met"},
                {"element_id": "signed", "when": {"signed": False}, "finding": "not_met"},
            ],
        }
    )
    coded = await apply_law(
        ladder,
        Model("{}", fail=True),
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        card_id=card["id"],
        features={"form": "written", "signed": True},
        question=QUESTION,
    )
    assert coded["ok"] is True
    assert coded["coded"] == 2
    assert coded["cost_usd"] == 0
    assert coded["model_calls"] == 0
    assert [item["rung"] for item in coded["elements"]] == ["code", "code"]

    judgment = "The court enforced a mutual NDA that was in writing for two years."
    facts = await extract_case_facts(
        ladder,
        Model(
            json.dumps(
                {
                    "features": {"form": "written", "term_years": 2},
                    "narrative": "a paraphrase that must not replace the judgment",
                    "element_findings": [
                        {
                            "element_id": "written",
                            "finding": "met",
                            "reasoning": "The court found a writing.",
                            "basis": ["form"],
                        }
                    ],
                    "outcome": "enforced",
                }
            )
        ),
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        pool_doc_id="case-1",
        citation="Example v. Buyer",
        source_id="courtlistener",
        judgment=judgment,
        rule_card_ids=[card["id"]],
    )
    assert facts["cached"] is False
    assert facts["worked_matter"]["fact_pattern"]["narrative"] == judgment
    assert facts["worked_matter"]["fact_pattern"]["narrative"] != "a paraphrase that must not replace the judgment"
    again = await extract_case_facts(
        ladder,
        Model("{}", fail=True),
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        pool_doc_id="case-1",
        source_id="courtlistener",
        judgment=judgment,
    )
    assert again["cached"] is True
    assert again["cost_usd"] == 0
    assert again["model_calls"] == 0
    pairs = await ladder.list_pairs(region="us-east", tenant_id=TENANT_B)
    assert any(row["source"] == "case" and row["tenant_id"] is None for row in pairs)

    await ladder.add_worked(
        {
            "region": "us-east",
            "tenant_id": TENANT_A,
            "source": "released",
            "jurisdiction": "us",
            "rule_card_ids": [card["id"]],
            "fact_pattern": {
                "features": {"form": "written", "term_years": 2},
                "narrative": f"{SECRET} signed a mutual NDA.",
            },
            "element_findings": [
                {
                    "element_id": "written",
                    "finding": "met",
                    "reasoning": f"Written for {SECRET}.",
                    "basis": ["form"],
                },
                {
                    "element_id": "term",
                    "finding": "met",
                    "reasoning": "Two years.",
                    "basis": ["term_years"],
                },
            ],
            "outcome": "enforced",
        }
    )
    # The fixture card has no term element, so the differing term is not sent on.
    # A second card element that differs does go to the model.
    card["elements"] = list(card["elements"]) + [
        {"id": "term", "test": "The term is stated.", "citation": "8 Del. C. § 101", "quote": PASSAGE["text"]}
    ]
    ladder.rule_cards[0]["elements"] = card["elements"]
    differ = Model(json.dumps({"finding": "unclear", "missing": "the term differs", "support": ""}))
    applied = await apply_law(
        ladder,
        differ,
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        card_id=card["id"],
        features={"form": "written", "signed": False, "term_years": 5},
        question=QUESTION,
        narrative="A new mutual NDA.",
    )
    assert applied["ok"] is True
    by_id = {item["element_id"]: item for item in applied["elements"]}
    assert by_id["written"]["rung"] == "reuse"
    assert by_id["written"]["cost_usd"] == 0
    assert by_id["signed"]["rung"] == "code"
    assert by_id["term"]["rung"] == "model"
    assert differ.calls == 1
    blob = json.dumps(applied)
    assert SECRET not in blob
    matched = await match_matters(
        ladder,
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        rule_card_ids=[card["id"]],
        features={"form": "written", "term_years": 5},
        question=QUESTION,
    )
    assert SECRET not in json.dumps(matched)
    private = await match_matters(
        ladder,
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        rule_card_ids=[card["id"]],
        features={"form": "written"},
        question=QUESTION,
        sharing="tenant",
    )
    assert all(item["source"] == "case" for item in private["matches"])
    memo = await write_memo(
        ladder,
        Model("The law is now 9 Del. C. § 999 in addition to 8 Del. C. § 101."),
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        card_id=card["id"],
        features={"form": "written", "signed": True, "term_years": 2},
        question=QUESTION,
        smooth=True,
    )
    assert memo["ok"] is True
    assert "9 Del. C. § 999" not in memo["memo"]["text"]
    assert SECRET not in json.dumps(memo)
    assert "8 Del. C. § 101" in memo["memo"]["text"]


@pytest.mark.asyncio
async def test_release_writes_a_training_pair_and_regions_do_not_cross() -> None:
    matters = MemoryStore()
    ladder = LadderMemory()
    await matters.register_tenant(TENANT_A, "us-east")
    await ladder.register_tenant(TENANT_A, "us-east")
    matter = await matters.add_matter(
        {
            "tenant_id": TENANT_A,
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Mutual NDA",
            "parties": ["Northwind"],
            "status": "open",
            "created_by": "ada",
        }
    )
    document = await matters.add_document(
        {
            "matter_id": matter["id"],
            "tenant_id": TENANT_A,
            "kind": "memo",
            "version": 1,
            "body": "Draft memo.",
            "sources": [],
            "state": "in_review",
        }
    )
    released = await release_matter(
        matters,
        ladder,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        matter_id=matter["id"],
        document_id=document["id"],
        reviewer="Ada Counsel",
        admission_no="12345",
        jurisdiction="us",
        rule_card_ids=["card-1"],
        features={"form": "written"},
        narrative="The mutual NDA was in writing.",
        element_findings=[{"element_id": "written", "finding": "met", "basis": ["form"]}],
        outcome="enforceable",
        memo="Issue: mutual NDA. The promise is in writing under Delaware law in the United States.",
        lawyer_edits="clarified the writing element",
    )
    assert released["ok"] is True
    pairs = await ladder.list_pairs(region="us-east", tenant_id=TENANT_A)
    own = [row for row in pairs if row["source"] == "released"]
    assert len(own) == 1
    assert own[0]["lawyer_edits"] == "clarified the writing element"
    assert own[0]["memo"].startswith("Issue:")
    assert own[0]["tenant_id"] == TENANT_A
    assert document["state"] == "released"
    other = await ladder.list_pairs(region="us-east", tenant_id=TENANT_B)
    assert all(row.get("tenant_id") != TENANT_A for row in other)

    blocked = await release_matter(
        matters,
        ladder,
        tenant_id=TENANT_A,
        home_region="za",
        box_region="us-east",
        matter_id=matter["id"],
        document_id=document["id"],
        reviewer="Ada Counsel",
        admission_no="12345",
        jurisdiction="za",
        rule_card_ids=[],
        features={},
        narrative="ZA facts must stay in ZA.",
        element_findings=[],
        outcome="",
        memo="A mutual NDA under South African law.",
    )
    assert blocked["ok"] is False
    assert blocked["reason"] == "home region does not match the tenant record"
    assert all(row["region"] == "us-east" for row in ladder.worked)
    assert all(row["region"] == "us-east" for row in ladder.pairs)
    copied = await copy_across_regions(ladder, row_id=released["worked_matter_id"], to_region="za")
    assert copied["copied"] is False
    assert len(ladder.worked) == 1

    memory = Memory()
    refused = await remember_for_tenant(
        ladder,
        memory,
        tenant_id=TENANT_A,
        home_region="za",
        box_region="us-east",
        text=f"Remember {SECRET}",
        kind="matter",
    )
    assert refused["stored"] is False
    assert memory.rows == []
    assert ladder.notes == []
    kept = await remember_for_tenant(
        ladder,
        memory,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        text=f"Mutual NDA note. ID {SA_ID}.",
        kind="matter",
    )
    assert kept["stored"] is True
    assert SA_ID not in kept["body"]
    assert SA_ID not in memory.rows[0][1]
    assert ladder.notes[0]["region"] == "us-east"

    await ladder.add_pair(
        {
            "region": "za",
            "tenant_id": TENANT_A,
            "source": "released",
            "request": f"{SECRET} in South Africa",
            "findings": [],
            "outcome": "local",
            "memo": SECRET,
        }
    )
    visible = await ladder.list_pairs(region="us-east", tenant_id=TENANT_A)
    assert SECRET not in json.dumps(visible)
    za_only = await ladder.list_pairs(region="za", tenant_id=TENANT_A)
    assert len(za_only) == 1


@pytest.mark.asyncio
async def test_client_rows_stay_in_the_home_region() -> None:
    """Worked matters, released rows, training pairs, tenant-tied cards, and memory
    stay in the tenant's home region. Public law can be read from either box.
    """
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "za")
    client = "CLIENT-FACT-STAYS-IN-ZA"
    card = await ladder.add_rule_card(
        {
            "region": "za",
            "tenant_id": TENANT_A,
            "jurisdiction": "za",
            "question": f"mutual NDA for {client}",
            "status": "lawyer_approved",
            "elements": [{"id": "party", "test": client, "citation": "", "quote": ""}],
        }
    )
    await ladder.add_worked(
        {
            "region": "za",
            "tenant_id": TENANT_A,
            "source": "released",
            "jurisdiction": "za",
            "fact_pattern": {"narrative": client, "features": {"party": client}},
            "element_findings": [{"element_id": "party", "finding": "met"}],
            "outcome": client,
        }
    )
    await ladder.add_pair(
        {
            "region": "za",
            "tenant_id": TENANT_A,
            "source": "released",
            "request": client,
            "findings": [],
            "outcome": client,
            "memo": client,
        }
    )
    await ladder.add_note({"region": "za", "tenant_id": TENANT_A, "kind": "matter", "body": client})

    assert await ladder.get_rule_card(card["id"], region="us-east", tenant_id=TENANT_A) is None
    assert await ladder.find_approved(
        region="us-east", jurisdiction="za", key=card["question_key"], tenant_id=TENANT_A
    ) is None
    assert client not in json.dumps(await ladder.list_cards(region="us-east"))
    assert await ladder.visible_worked(region="us-east", tenant_id=TENANT_A, sharing="region") == []
    assert await ladder.list_pairs(region="us-east", tenant_id=TENANT_A) == []
    assert all(row["region"] == "za" for row in ladder.notes)
    copied = await copy_across_regions(ladder, row_id=card["id"], to_region="us-east")
    assert copied["copied"] is False
    assert copied["reason"] == "client facts do not leave their region"
    assert len(ladder.rule_cards) == 1
    assert ladder.rule_cards[0]["region"] == "za"
    assert len(ladder.worked) == 1
    assert len(ladder.pairs) == 1

    us_law = {
        "doc_id": "us-statute",
        "version_id": "v1",
        "citation": "8 Del. C. § 101",
        "text": "A written confidentiality promise is enforceable.",
        "jurisdiction": "us",
        "status": "effective",
    }
    read = await retrieve(
        ladder,
        None,
        tenant_id=TENANT_A,
        home_region="za",
        box_region="za",
        jurisdiction="us",
        query="written confidentiality promise mutual NDA",
        corpus=[us_law],
    )
    assert read["ok"] is True
    assert read["passages"][0]["jurisdiction"] == "us"
    assert client not in json.dumps(read)
    assert ladder.notes[0]["body"] == client


@pytest.mark.asyncio
async def test_public_law_is_readable_and_criminal_requests_store_nothing() -> None:
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "us-east")
    corpus = [
        {
            "doc_id": "za-act",
            "version_id": "v1",
            "citation": "Act 71 of 2008 s 1",
            "text": "A written confidentiality promise is enforceable between the parties.",
            "jurisdiction": "za",
            "status": "effective",
        }
    ]
    found = await retrieve(
        ladder,
        None,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="za",
        query="written confidentiality promise mutual NDA",
        corpus=corpus,
    )
    assert found["ok"] is True
    assert found["passages"][0]["jurisdiction"] == "za"
    assert ladder.rule_cards == []
    refused = await retrieve(
        ladder,
        None,
        tenant_id=TENANT_A,
        home_region="za",
        box_region="us-east",
        jurisdiction="za",
        query="mutual NDA",
        corpus=corpus,
    )
    assert refused["ok"] is False
    assert ladder.notes == []
    criminal = await build_rule_card(
        ladder,
        None,
        Model("{}", fail=True),
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question="We have a bail hearing tomorrow.",
        passages=[PASSAGE],
    )
    assert criminal["ok"] is False
    assert ladder.rule_cards == []


@pytest.mark.asyncio
async def test_za_model_call_masks_before_a_non_sa_provider() -> None:
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "za")
    question = f"{QUESTION} ID {SA_ID}."
    model = Model(json.dumps(CARD_JSON))
    result = await build_rule_card(
        ladder,
        None,
        model,
        tenant_id=TENANT_A,
        home_region="za",
        box_region="za",
        jurisdiction="us",
        question=question,
        passages=[PASSAGE],
        provider="luna",
    )
    assert result["ok"] is True
    sent = json.dumps(model.sent)
    assert SA_ID not in sent
    assert SA_ID not in json.dumps(result["rule_card"])
    local = Model(json.dumps(CARD_JSON))
    await build_rule_card(
        ladder,
        None,
        local,
        tenant_id=TENANT_A,
        home_region="za",
        box_region="za",
        jurisdiction="us",
        question=question + " supply agreement",
        passages=[PASSAGE],
        provider="za-local",
    )
    assert SA_ID in json.dumps(local.sent)


@pytest.mark.asyncio
async def test_metrics_count_reuse_code_and_cards() -> None:
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "us-east")
    await ladder.register_tenant(TENANT_B, "us-east")
    model = Model(json.dumps(CARD_JSON))
    built = await build_rule_card(
        ladder,
        None,
        model,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question=QUESTION,
        passages=[PASSAGE],
    )
    await approve_rule_card(
        ladder,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        card_id=built["rule_card"]["id"],
        reviewer="Ada Counsel",
        admission_no="12345",
        jurisdiction="us",
        passages=[PASSAGE],
        changes="one edit",
    )
    card = built["rule_card"]
    ladder.rule_cards[0]["decision_table"] = [
        {"element_id": "written", "when": {"form": "written"}, "finding": "met"}
    ]
    ladder.rule_cards[0]["elements"] = card["elements"]
    await apply_law(
        ladder,
        Model("{}", fail=True),
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        card_id=card["id"],
        features={"form": "written"},
        question=QUESTION,
    )
    metrics = await ladder_metrics(
        ladder,
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
    )
    assert metrics["ok"] is True
    assert metrics["cards_approved"] == 1
    assert metrics["elements_coded_share"] == 1
    assert metrics["later_matter_cost_usd"] == 0
    first = await ladder_metrics(
        ladder,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
    )
    assert first["first_answer_cost_usd"] == 0.05
    assert first["lawyer_edits_per_card"] == 1


def test_ladder_routes_are_registered() -> None:
    path = ROOT / "agents" / "zenas" / "app.py"
    spec = importlib.util.spec_from_file_location("zenas_ladder_app", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from http_routes import http_paths

    paths = http_paths(module.app.routes)
    for expected in (
        "/ladder/retrieve",
        "/ladder/cite-check",
        "/rule-cards",
        "/rule-cards/{card_id}",
        "/rule-cards/{card_id}/approve",
        "/ladder/stale",
        "/cases/facts",
        "/ladder/match",
        "/ladder/apply",
        "/ladder/compose",
        "/ladder/plain",
        "/matters/{matter_id}/release",
        "/ladder/metrics",
        "/tenants",
        "/review",
    ):
        assert expected in paths


def test_policy_rows_are_additive() -> None:
    policy = (ROOT / "tank" / "schema.sql").read_text(encoding="utf-8")
    for task, model, scope in (
        ("zenas.rule_card", "luna", "region"),
        ("zenas.case_facts", "luna", "region"),
        ("zenas.apply", "luna", "tenant"),
        ("zenas.compose", "luna", "tenant"),
        ("zenas.plain_language", "luna", "tenant"),
    ):
        assert f"'{task}'" in policy
        assert f"'{model}'" in policy
        assert f"'{scope}'" in policy
    assert "ON CONFLICT DO NOTHING" in policy


class BuildModel(Model):
    """The build-only ChatGPT sign-in (docs/ops/CHATGPT-BUILD-MODEL.md)."""

    async def complete(self, messages, *, task_type: str, provider: str) -> dict:
        result = await super().complete(messages, task_type=task_type, provider=provider)
        return {**result, "model": "build-chatgpt", "cost_usd": 0}


@pytest.mark.asyncio
async def test_a_build_model_card_never_reaches_another_tenant_and_cannot_be_approved() -> None:
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "us-east")
    await ladder.register_tenant(TENANT_B, "us-east")
    first = await build_rule_card(
        ladder, None, BuildModel(json.dumps(CARD_JSON)), tenant_id=TENANT_A, home_region="us-east",
        box_region="us-east", jurisdiction="us", question=QUESTION, passages=[PASSAGE],
    )
    assert first["ok"] is True
    assert [str(row.get("tenant_id")) for row in ladder.rule_cards] == [TENANT_A]
    refused = await approve_rule_card(
        ladder, tenant_id=TENANT_A, home_region="us-east", box_region="us-east",
        card_id=first["rule_card"]["id"], reviewer="Ada Counsel", admission_no="12345",
        jurisdiction="us", passages=[PASSAGE],
    )
    assert refused["ok"] is False
    assert "build-only" in refused["reason"]
    other = Model(json.dumps(CARD_JSON))
    second = await build_rule_card(
        ladder, None, other, tenant_id=TENANT_B, home_region="us-east",
        box_region="us-east", jurisdiction="us", question=QUESTION, passages=[PASSAGE],
    )
    assert second["ok"] is True and second["reused"] is False and other.calls == 1
