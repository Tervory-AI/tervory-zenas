"""A founder sign-off is accepted by the Zenas room. A reviewer still needs an admission number.

The HTTP calls hit the room. The store is memory so the test needs no database.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

os.environ.setdefault("TANK_REGION", "us-east")

from agents.zenas import app as room  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.learning import apply_correction, panel_slots  # noqa: E402
from agents.zenas.core.learning import LearningError  # noqa: E402
from agents.zenas.core.store import MemoryStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-0000000000a1"
PASSAGE = {
    "doc_id": "d1",
    "version_id": "v1",
    "citation": "8 Del. C. § 101",
    "text": "A written confidentiality promise is enforceable.",
    "jurisdiction": "us",
    "status": "effective",
}
CLAUSE = "Each party keeps the other party's information confidential."


async def _document(store: MemoryStore) -> str:
    await store.register_tenant(TENANT, "us-east")
    matter = await store.add_matter(
        {
            "tenant_id": TENANT,
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Mutual NDA",
            "parties": [],
            "status": "open",
            "created_by": "founder",
        }
    )
    document = await store.add_document(
        {
            "matter_id": matter["id"],
            "tenant_id": TENANT,
            "kind": "draft",
            "version": 1,
            "body": "The mutual NDA was in writing.",
            "sources": [],
            "state": "in_review",
        }
    )
    return str(document["id"])


async def _card(ladder: LadderMemory) -> str:
    await ladder.register_tenant(TENANT, "us-east")
    saved = await ladder.add_rule_card(
        {
            "region": "us-east",
            "tenant_id": TENANT,
            "jurisdiction": "us",
            "question": "Is a mutual NDA enforceable under Delaware law in the United States?",
            "elements": [
                {
                    "id": "written",
                    "test": "The promise is in writing.",
                    "citation": "8 Del. C. § 101",
                    "quote": "A written confidentiality promise is enforceable.",
                }
            ],
            "status": "draft",
            "sources": [{"doc_id": "d1"}],
        }
    )
    return str(saved["id"])


@pytest.mark.asyncio
async def test_the_room_stores_a_founder_sign_off_and_refuses_a_reviewer_without_an_admission_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TANK_REGION", "us-east")
    store = MemoryStore()
    ladder = LadderMemory()
    document_id = await _document(store)
    card_id = await _card(ladder)
    monkeypatch.setattr(room, "_store", lambda: store)
    monkeypatch.setattr(room, "_ready", _ready(ladder))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=room.app),
        base_url="http://zenas",
    ) as client:
        founder = await client.post(
            f"/documents/{document_id}/reviews",
            json={
                "tenant_id": TENANT,
                "reviewer": "Founder",
                "jurisdiction": "us",
                "admission_no": "",
                "decision": "approved",
                "reviewer_kind": "founder",
            },
        )
        bare = await client.post(
            f"/documents/{document_id}/reviews",
            json={
                "tenant_id": TENANT,
                "reviewer": "Ada Counsel",
                "jurisdiction": "us",
                "admission_no": "",
                "decision": "approved",
                "reviewer_kind": "reviewer",
            },
        )
        admitted = await client.post(
            f"/documents/{document_id}/reviews",
            json={
                "tenant_id": TENANT,
                "reviewer": "Ada Counsel",
                "jurisdiction": "za",
                "admission_no": "12345",
                "decision": "rejected",
                "changes": "narrow the term",
                "reviewer_kind": "reviewer",
            },
        )
        founder_card = await client.post(
            f"/rule-cards/{card_id}/approve",
            json={
                "tenant_id": TENANT,
                "home_region": "us-east",
                "reviewer": "Founder",
                "admission_no": "",
                "jurisdiction": "us",
                "passages": [PASSAGE],
                "reviewer_kind": "founder",
            },
        )
        bare_card = await client.post(
            f"/rule-cards/{card_id}/approve",
            json={
                "tenant_id": TENANT,
                "home_region": "us-east",
                "reviewer": "Ada Counsel",
                "admission_no": "   ",
                "jurisdiction": "us",
                "passages": [PASSAGE],
                "reviewer_kind": "reviewer",
            },
        )
        second = await _card(ladder)
        admitted_card = await client.post(
            f"/rule-cards/{second}/approve",
            json={
                "tenant_id": TENANT,
                "home_region": "us-east",
                "reviewer": "Ada Counsel",
                "admission_no": "12345",
                "jurisdiction": "us",
                "passages": [PASSAGE],
                "reviewer_kind": "reviewer",
            },
        )
        founder_clause = await client.post(
            "/clauses",
            json={
                "tenant_id": TENANT,
                "home_region": "us-east",
                "jurisdiction": "us",
                "template": "nda",
                "kind": "confidentiality",
                "text": CLAUSE,
                "approved_by": "Founder",
                "admission_no": "",
                "reviewer_kind": "founder",
            },
        )
        bare_clause = await client.post(
            "/clauses",
            json={
                "tenant_id": TENANT,
                "home_region": "us-east",
                "jurisdiction": "us",
                "template": "nda",
                "kind": "term",
                "text": "The term is two years.",
                "approved_by": "Ada Counsel",
                "admission_no": "",
                "reviewer_kind": "reviewer",
            },
        )
        admitted_clause = await client.post(
            "/clauses",
            json={
                "tenant_id": TENANT,
                "home_region": "us-east",
                "jurisdiction": "za",
                "template": "nda",
                "kind": "term",
                "text": "The term is two years.",
                "approved_by": "Ada Counsel",
                "admission_no": "67890",
                "reviewer_kind": "reviewer",
            },
        )

    assert founder.status_code == 201
    assert founder.json()["reviewer_kind"] == "founder"
    assert store.reviews[0]["reviewer_kind"] == "founder"
    assert store.reviews[0]["admission_no"] == ""
    assert store.reviews[0]["decision"] == "approved"
    assert bare.status_code == 422
    assert admitted.status_code == 201
    assert store.reviews[1]["reviewer_kind"] == "reviewer"
    assert store.reviews[1]["admission_no"] == "12345"
    assert len(store.reviews) == 2

    assert founder_card.status_code == 200
    assert founder_card.json()["rule_card"]["reviewer_kind"] == "founder"
    assert ladder.rule_cards[0]["reviewer_kind"] == "founder"
    assert ladder.rule_cards[0]["admission_no"] == ""
    assert ladder.rule_cards[0]["status"] == "lawyer_approved"
    assert bare_card.status_code == 422
    assert admitted_card.status_code == 200
    assert ladder.rule_cards[1]["reviewer_kind"] == "reviewer"
    assert ladder.rule_cards[1]["admission_no"] == "12345"
    assert ladder.rule_cards[1]["status"] == "lawyer_approved"

    assert founder_clause.status_code == 201
    assert founder_clause.json()["clause"]["reviewer_kind"] == "founder"
    assert store.clauses[0]["reviewer_kind"] == "founder"
    assert store.clauses[0]["admission_no"] == ""
    assert store.clauses[0]["kind"] == "confidentiality"
    assert bare_clause.status_code == 422
    assert admitted_clause.status_code == 201
    assert store.clauses[1]["reviewer_kind"] == "reviewer"
    assert store.clauses[1]["admission_no"] == "67890"
    assert len(store.clauses) == 2

    slot = panel_slots("us")[0]
    with pytest.raises(LearningError, match="admission"):
        apply_correction(slot, "Use the enacted wording.", corrected_by="Ada Counsel", reviewer_kind="reviewer")
    founder_slot = apply_correction(
        slot,
        "Use the enacted wording.",
        corrected_by="Founder",
        admission_no="",
        reviewer_kind="founder",
    )
    assert founder_slot.reviewer_kind == "founder"
    assert founder_slot.admission_no == ""
    reviewer_slot = apply_correction(
        slot,
        "Use the enacted wording.",
        corrected_by="Ada Counsel",
        admission_no="12345",
        reviewer_kind="reviewer",
    )
    assert reviewer_slot.reviewer_kind == "reviewer"
    assert reviewer_slot.admission_no == "12345"

    route = (ROOT / "workspace/app/api/zenas/documents/[id]/reviews/route.ts").read_text(encoding="utf-8")
    assert 'reviewer_kind: reviewer.founder ? "founder" : "reviewer"' in route
    schema = (ROOT / "agents/zenas/schema.sql").read_text(encoding="utf-8").lower()
    assert "add column if not exists reviewer_kind" in schema
    assert "drop " not in schema


def _ready(ladder: LadderMemory):
    async def ready() -> LadderMemory:
        return ladder

    return ready
