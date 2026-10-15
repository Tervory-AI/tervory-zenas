"""Public record stays as published. Client work is masked only on the shared copy."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.service import extract_case_facts, release_matter  # noqa: E402
from agents.zenas.core.store import MemoryStore  # noqa: E402

TENANT_A = "00000000-0000-0000-0000-0000000000a1"
TENANT_B = "00000000-0000-0000-0000-0000000000b2"
TENANT_C = "00000000-0000-0000-0000-0000000000c3"
SAFLII = ROOT / "agents/zenas/pool/fixtures/za/saflii/2023-zasca-12.html"
COURTLISTENER = (
    b"Fixture Carrier v. Fixture Shipper, 600 F.3d 10.\n"
    b"The court enforced the bill of lading as published.\n"
)
BODY = (
    "Alpha Shipping (Pty) Ltd of 12 Main Street and Ada Nkosi "
    "(ada@alpha.example, +27 82 123 4567, ID 8001015009087) agree a mutual NDA "
    "with Beta Carriers (Pty) Ltd for $250,000 on 2024-03-15. Case no 1234/2024. "
    "Account number 123456789012."
)
REASONING = "The promise was recorded in writing, so the writing element is met for Ada Nkosi."
AMENDMENT = "State the confidentiality period as two years and remove Ada Nkosi from the notice line."
PERSONAL = (
    "Alpha Shipping (Pty) Ltd",
    "Beta Carriers (Pty) Ltd",
    "Ada Nkosi",
    "12 Main Street",
    "ada@alpha.example",
    "+27 82 123 4567",
    "8001015009087",
    "$250,000",
    "2024-03-15",
    "1234/2024",
    "123456789012",
)


class Model:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def model(self, messages: list[dict[str, str]], task_type: str) -> str:
        self.sent.append(messages[0]["content"])
        return json.dumps(
            {
                "features": {},
                "narrative": "a paraphrase that must not replace the judgment",
                "element_findings": [{"element_id": "delivery", "finding": "met", "reasoning": "As published.", "basis": []}],
                "outcome": "enforced",
            }
        )


def _no_personal(blob: str) -> None:
    for token in PERSONAL:
        assert token not in blob


@pytest.mark.asyncio
async def test_court_judgments_stay_byte_identical() -> None:
    saflii = SAFLII.read_bytes()
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "za")
    await ladder.register_tenant(TENANT_B, "us-east")
    model = Model()
    za = await extract_case_facts(
        ladder,
        model,
        tenant_id=TENANT_A,
        home_region="za",
        box_region="za",
        jurisdiction="za",
        pool_doc_id="saflii-2023-zasca-12",
        source_id="saflii",
        citation="Alpha Shipping (Pty) Ltd v Beta Carriers (Pty) Ltd [2023] ZASCA 12",
        judgment=saflii.decode("utf-8"),
    )
    assert za["ok"] is True
    assert za["worked_matter"]["material_type"] == "public_record"
    stored = za["worked_matter"]["fact_pattern"]["narrative"]
    assert stored.encode("utf-8") == saflii
    assert "Alpha Shipping (Pty) Ltd" in stored
    assert "Beta Carriers (Pty) Ltd" in stored
    assert "a paraphrase that must not replace the judgment" not in stored
    assert saflii.decode("utf-8") in model.sent[0]

    us = await extract_case_facts(
        ladder,
        Model(),
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        pool_doc_id="courtlistener-600-f3d-10",
        source_id="courtlistener",
        citation="Fixture Carrier v. Fixture Shipper, 600 F.3d 10",
        judgment=COURTLISTENER.decode("utf-8"),
    )
    assert us["ok"] is True
    opinion = us["worked_matter"]["fact_pattern"]["narrative"]
    assert opinion.encode("utf-8") == COURTLISTENER
    assert "Fixture Carrier v. Fixture Shipper" in opinion
    shared = await ladder.visible_worked(region="us-east", tenant_id=TENANT_A, sharing="region")
    found = [row for row in shared if row["id"] == us["worked_matter"]["id"]]
    assert len(found) == 1
    assert found[0]["fact_pattern"]["narrative"].encode("utf-8") == COURTLISTENER


@pytest.mark.asyncio
async def test_unknown_source_is_not_shared(caplog: pytest.LogCaptureFixture) -> None:
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT_A, "us-east")
    judgment = "Alpha Shipping (Pty) Ltd v Beta Carriers (Pty) Ltd. The court enforced the bill of lading."
    with caplog.at_level(logging.WARNING, logger="zenas.material"):
        missing = await extract_case_facts(
            ladder,
            Model(),
            tenant_id=TENANT_A,
            home_region="us-east",
            box_region="us-east",
            jurisdiction="us",
            pool_doc_id="unknown-1",
            judgment=judgment,
        )
        statute = await extract_case_facts(
            ladder,
            Model(),
            tenant_id=TENANT_A,
            home_region="us-east",
            box_region="us-east",
            jurisdiction="us",
            pool_doc_id="statute-1",
            source_id="govinfo-uscode",
            judgment=judgment,
        )
    assert missing["ok"] is False
    assert missing["shared"] is False
    assert missing["reason"] == "material type is unknown"
    assert statute["reason"] == "material type is unknown"
    assert ladder.worked == []
    assert "material type is unknown" in caplog.text
    assert "Alpha Shipping" not in caplog.text


@pytest.mark.asyncio
async def test_released_client_work_is_masked_and_the_original_stays_private(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    matters = MemoryStore()
    ladder = LadderMemory()
    for store in (matters, ladder):
        await store.register_tenant(TENANT_A, "us-east")
        await store.register_tenant(TENANT_B, "us-east")
        await store.register_tenant(TENANT_C, "za")
    matter = await matters.add_matter(
        {
            "tenant_id": TENANT_A,
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Mutual NDA",
            "parties": ["Alpha Shipping (Pty) Ltd", "Beta Carriers (Pty) Ltd", "Ada Nkosi"],
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
            "body": BODY,
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
        rule_card_ids=[],
        features={"price": "$250,000"},
        narrative=BODY,
        element_findings=[{"element_id": "writing", "finding": "met", "reasoning": REASONING, "basis": []}],
        outcome="The writing element is met.",
        memo=BODY,
        lawyer_edits=AMENDMENT,
    )
    assert released["ok"] is True
    assert released["shared"] is True
    assert document["body"] == BODY
    assert document["state"] == "released"
    assert await matters.get_document(TENANT_B, document["id"]) is None
    assert await matters.get_document(TENANT_A, document["id"]) == document

    visible = await ladder.visible_worked(region="us-east", tenant_id=TENANT_B, sharing="region")
    assert len(visible) == 1
    shared = visible[0]
    assert shared["material_type"] == "client_work"
    blob = json.dumps(shared)
    _no_personal(blob)
    assert "writing element is met" in blob
    assert "two years" in blob
    assert "[Party A]" in blob
    assert "[Address]" in blob
    assert "[Amount]" in blob
    own = await ladder.visible_worked(region="us-east", tenant_id=TENANT_A, sharing="region")
    assert json.dumps(own) == json.dumps(visible)
    abroad = await ladder.visible_worked(region="za", tenant_id=TENANT_C, sharing="region")
    assert abroad == []
    assert "Alpha Shipping" not in json.dumps(abroad)

    with caplog.at_level(logging.WARNING, logger="zenas.material"):
        monkeypatch.setattr(
            "agents.zenas.core.release_mask.mask_client_text",
            lambda *_args, **_kwargs: {"ok": False, "text": "", "reason": "masking failed"},
        )
        blocked = await release_matter(
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
            rule_card_ids=[],
            features={},
            narrative="A mutual NDA for Ada Nkosi.",
            element_findings=[],
            outcome="",
            memo="A mutual NDA.",
            lawyer_edits="Keep Ada Nkosi in the draft.",
        )
    assert blocked["shared"] is False
    assert blocked["reason"] == "masking failed"
    assert blocked["worked_matter_id"] is None
    assert len(ladder.worked) == 1
    assert document["body"] == BODY
    assert "masking failed" in caplog.text
    assert "Ada Nkosi" not in caplog.text


async def _release(matters: MemoryStore, ladder: LadderMemory, matter: dict, document: dict, **kwargs: object) -> dict:
    return await release_matter(
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
        rule_card_ids=[],
        features={},
        element_findings=kwargs.get("element_findings") or [],
        outcome="",
        narrative=str(kwargs.get("narrative") or ""),
        memo=str(kwargs.get("memo") or "A mutual NDA."),
        lawyer_edits=str(kwargs.get("lawyer_edits") or ""),
    )


@pytest.mark.asyncio
async def test_release_without_parties_is_not_shared() -> None:
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
            "body": "Ada Nkosi agrees a mutual NDA.",
            "sources": [],
            "state": "in_review",
        }
    )
    released = await _release(matters, ladder, matter, document, narrative="Ada Nkosi agrees a mutual NDA.")
    assert released["ok"] is True
    assert released["shared"] is False
    assert released["reason"] == "masking failed"
    assert released["worked_matter_id"] is None
    assert ladder.worked == []
    assert document["body"] == "Ada Nkosi agrees a mutual NDA."


@pytest.mark.asyncio
async def test_recorded_names_keep_one_placeholder() -> None:
    matters = MemoryStore()
    ladder = LadderMemory()
    await matters.register_tenant(TENANT_A, "us-east")
    await ladder.register_tenant(TENANT_B, "us-east")
    matter = await matters.add_matter(
        {
            "tenant_id": TENANT_A,
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Mutual NDA",
            "parties": ["Northwind"],
            "counterparties": ["Helios Trading"],
            "status": "open",
            "created_by": "ada",
        }
    )
    body = "Northwind and Ada Nkosi agree a mutual NDA. Northwind keeps the writing for Helios Trading."
    document = await matters.add_document(
        {
            "matter_id": matter["id"],
            "tenant_id": TENANT_A,
            "kind": "memo",
            "version": 1,
            "body": body,
            "signatories": ["Ada Nkosi"],
            "contacts": [{"name": "Sam Phelps"}],
            "sources": [],
            "state": "in_review",
        }
    )
    narrative = "Northwind asks Sam Phelps to keep a mutual NDA."
    reasoning = "Northwind recorded the promise in writing."
    released = await _release(
        matters,
        ladder,
        matter,
        document,
        narrative=narrative,
        memo="A mutual NDA.",
        lawyer_edits="Leave the writing duty with Northwind.",
        element_findings=[{"element_id": "writing", "finding": "met", "reasoning": reasoning, "basis": []}],
    )
    assert released["shared"] is True
    visible = await ladder.visible_worked(region="us-east", tenant_id=TENANT_B, sharing="region")
    assert len(visible) == 1
    shared = visible[0]
    blob = json.dumps(shared)
    for token in ("Northwind", "Ada Nkosi", "Helios Trading", "Sam Phelps"):
        assert token not in blob
    assert shared["fact_pattern"]["body"].count("[Party A]") == 2
    assert "[Party A]" in shared["fact_pattern"]["narrative"]
    assert "[Party A]" in shared["lawyer_edits"]
    assert shared["element_findings"][0]["reasoning"] == "[Party A] recorded the promise in writing."
    assert "[Party B]" in shared["fact_pattern"]["body"]
    assert document["body"] == body
