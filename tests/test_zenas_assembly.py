"""Zenas assembles civil contracts inside agent-zenas: real Word file, both regions, no server.

Clauses go in through add_approved_clause (approving lawyer + admission number) and are
read back by the store, the same path the room uses. Nothing is stubbed between
draft_contract and the .docx bytes. No network, no model.
"""
from __future__ import annotations

import base64
import json
import socket
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.zenas.core import assembly, drafting  # noqa: E402
from agents.zenas.core.assembly import Assembler, AssemblyUnavailable  # noqa: E402
from agents.zenas.core.docx_redline import docx_bytes  # noqa: E402
from agents.zenas.core.drafting import TEMPLATES  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.service import (  # noqa: E402
    _LEGAL_NOTICE,
    _with_legal_notice,
    add_approved_clause,
    draft_contract,
)
from agents.zenas.core.store import MemoryStore  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000011"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
JURISDICTION = {"us-east": "us", "za": "za"}

# Civil clause wording per slot, with the placeholders a term sheet fills.
WORDING = {
    "parties": "This agreement is between {party} and {counterparty}.",
    "confidentiality": "{counterparty} keeps the information {party} discloses confidential.",
    "term": "This agreement runs for {term} from {start_date}.",
    "return": "On request {counterparty} returns or destroys the information within {days} days.",
    "goods": "{party} supplies {goods} to {counterparty}.",
    "price": "{counterparty} pays {price} within {days} days of invoice.",
    "delivery": "{party} delivers to {place} by {delivery_date}.",
    "risk": "Risk in the goods passes to {counterparty} on delivery at {place}.",
    "cisg": "The United Nations Convention on Contracts for the International Sale of Goods applies.",
    "territory": "{counterparty} distributes only in {territory}.",
    "termination": "Either party may end this agreement on {days} days' written notice.",
    "carriage": "{party} carries the consignment from {origin} to {place}.",
    "freight": "{counterparty} pays freight of {price}.",
    "services": "{party} provides {services} to {counterparty}.",
    "fees": "{counterparty} pays fees of {price} each month.",
    "authority": "{counterparty} may solicit orders for {party} but may not bind {party}.",
    "commission": "{party} pays {counterparty} a commission of {commission} on accepted orders.",
}
TERMS = {
    "us-east": {"party": "Acme Supply LLC", "counterparty": "Riverbend Tools Inc.", "term": "two years",
                "start_date": "1 November 2026", "days": "30", "goods": "500 hand drills",
                "price": "USD 12,500", "place": "Boston, Massachusetts", "delivery_date": "1 December 2026",
                "territory": "New England", "origin": "Newark, New Jersey", "services": "monthly tool servicing",
                "commission": "5 percent"},
    "za": {"party": "Karoo Supplies (Pty) Ltd", "counterparty": "Umhlanga Traders CC", "term": "two years",
           "start_date": "1 November 2026", "days": "30", "goods": "500 hand drills",
           "price": "ZAR 230,000", "place": "Durban, KwaZulu-Natal", "delivery_date": "1 December 2026",
           "territory": "KwaZulu-Natal", "origin": "Johannesburg, Gauteng", "services": "monthly tool servicing",
           "commission": "5 percent"},
}


def word_paragraphs(data: bytes) -> list[tuple[str, bool]]:
    """(text, tracked insertion?) for each paragraph of word/document.xml."""
    assert data[:2] == b"PK"
    with ZipFile(BytesIO(data)) as package:
        assert "[Content_Types].xml" in package.namelist()
        xml = ElementTree.fromstring(package.read("word/document.xml"))
    out = []
    for paragraph in xml.iter(W + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(W + "t"))
        out.append((text, paragraph.find(W + "ins") is not None))
    return out


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("contract assembly must not open a socket")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


async def _room(region: str, template: str, *, skip: str | None = None):
    store, ladder = MemoryStore(), LadderMemory()
    await store.register_tenant(TENANT, region)
    await ladder.register_tenant(TENANT, region)
    for slot in TEMPLATES[template]:
        if slot == skip:
            continue
        added = await add_approved_clause(
            store, tenant_id=TENANT, home_region=region, box_region=region,
            jurisdiction=JURISDICTION[region], template=template, kind=slot, text=WORDING[slot],
            approved_by="Ada Counsel", admission_no="12345")
        assert added["ok"], added
    return store, ladder


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["us-east", "za"])
@pytest.mark.parametrize("template", list(TEMPLATES))
async def test_every_template_drafts_a_real_word_file_end_to_end(region, template):
    store, ladder = await _room(region, template)
    model = AsyncMock()
    result = await draft_contract(store, ladder, model, tenant_id=TENANT, home_region=region,
                                  box_region=region, jurisdiction=JURISDICTION[region], template=template,
                                  narrative="A civil commercial contract.", terms=TERMS[region])
    assert result["ok"] is True, result
    slots = list(TEMPLATES[template])
    expected = [drafting.fill_clause(WORDING[slot], TERMS[region])[0] for slot in slots]
    # Slot order, every sentence an approved clause, every field filled, nothing marked.
    assert [item["slot"] for item in result["sentences"]] == slots
    assert [item["text"] for item in result["sentences"]] == expected
    assert result["library"] == slots and result["review"] == []
    assert all(item["clause_id"] for item in result["sentences"])
    assert result["text"] == _with_legal_notice("\n\n".join(expected))
    assert result["model_calls"] == 0 and result["cost_usd"] == 0.0
    model.model.assert_not_awaited()
    paragraphs = word_paragraphs(base64.b64decode(result["docx_base64"]))
    assert paragraphs == [(text, False) for text in expected] + [(_LEGAL_NOTICE, False)]
    assert "{" not in result["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["us-east", "za"])
async def test_missing_fields_and_missing_clause_are_marked_in_word_as_tracked_insertions(region):
    store, ladder = await _room(region, "sale_of_goods", skip="risk")
    terms = dict(TERMS[region])
    del terms["price"]
    terms["delivery_date"] = ""
    result = await draft_contract(store, ladder, None, tenant_id=TENANT, home_region=region,
                                  box_region=region, jurisdiction=JURISDICTION[region],
                                  template="sale_of_goods", narrative="A civil sale of goods.", terms=terms)
    assert result["ok"] is True
    assert result["review"] == ["price", "delivery", "risk"]
    assert result["library"] == ["parties", "goods"]
    by_slot = {item["slot"]: item["text"] for item in result["sentences"]}
    assert "{price}" in by_slot["price"] and "{delivery_date}" in by_slot["delivery"]
    assert by_slot["risk"] == "[Review: no approved clause for risk.]"
    paragraphs = word_paragraphs(base64.b64decode(result["docx_base64"]))
    assert [marked for _, marked in paragraphs] == [False, False, True, True, True, False]
    assert paragraphs[-1] == (_LEGAL_NOTICE, False)
    assert result["model_calls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("region,jurisdiction", [("us-east", "za"), ("za", "us")])
async def test_region_mismatch_is_refused_before_assembly_or_model(region, jurisdiction):
    store, ladder = await _room(region, "nda")
    model = AsyncMock()
    result = await draft_contract(store, ladder, model, tenant_id=TENANT, home_region=region,
                                  box_region=region, jurisdiction=jurisdiction, template="nda",
                                  narrative="A civil NDA.", terms=TERMS[region])
    assert result["ok"] is False and "docx_base64" not in result
    model.model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("region,other", [("us-east", "za"), ("za", "us-east")])
async def test_a_clause_from_the_other_region_is_refused_and_nothing_is_stored(region, other):
    store, ladder = await _room(region, "nda")
    store.clauses[0]["region"] = other  # a row that should never have reached this box
    original = store.list_clauses

    async def leaky(**kwargs):
        rows = await original(**kwargs)
        return rows + [dict(store.clauses[0])]
    store.list_clauses = leaky
    result = await draft_contract(store, ladder, None, tenant_id=TENANT, home_region=region,
                                  box_region=region, jurisdiction=JURISDICTION[region], template="nda",
                                  narrative="A civil NDA.", terms=TERMS[region])
    assert result == {"ok": False, "reason": "Refused. A clause or prior matter for this draft does not belong to this region."}
    assert not store.documents


@pytest.mark.asyncio
async def test_unapproved_clause_and_foreign_prior_are_refused_by_the_assembler():
    rows = [{"id": "c1", "region": "za", "jurisdiction": "za", "status": "draft", "template": "nda",
             "kind": "parties", "text": "This agreement is between {party} and {counterparty}."}]
    with pytest.raises(AssemblyUnavailable):
        await Assembler("za").assemble(template="nda", jurisdiction="za", clauses=rows, terms={}, prior=None)
    prior = {"region": "us-east", "jurisdiction": "us", "source": "released",
             "fact_pattern": {"template": "nda", "features": {}, "text": "Released NDA."}}
    with pytest.raises(AssemblyUnavailable):
        await Assembler("za").assemble(template="nda", jurisdiction="za", clauses=[], terms={}, prior=prior)
    with pytest.raises(AssemblyUnavailable):
        Assembler("eu-west")


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["us-east", "za"])
async def test_released_prior_with_the_same_facts_is_reused_word_for_word(region):
    prior = {"id": "p1", "created_at": "2026-10-01", "region": region, "jurisdiction": JURISDICTION[region],
             "source": "released",
             "fact_pattern": {"template": "nda", "features": {"term": "two years"}, "text": "Released mutual NDA."}}
    assembler = Assembler(region)
    assembled = await assembler.assemble(template="nda", jurisdiction=JURISDICTION[region], clauses=[],
                                         terms={"term": "two years"}, prior=prior)
    assert assembled["reused"] and assembled["text"] == "Released mutual NDA."
    finished = await assembler.finish(template="nda", jurisdiction=JURISDICTION[region],
                                      assembled=assembled, notice=_LEGAL_NOTICE)
    assert finished["text"] == "Released mutual NDA.\n\n" + _LEGAL_NOTICE
    assert word_paragraphs(base64.b64decode(finished["docx_base64"])) == [
        ("Released mutual NDA.", False), (_LEGAL_NOTICE, False)]


@pytest.mark.asyncio
async def test_template_syntax_in_clauses_and_terms_is_text_not_code():
    hostile = "${7 * 7} {{7 * 7}} {% raise %} <% raise Exception() %> A & B <Civil>"
    rows = [{"id": "c1", "region": "za", "jurisdiction": "za", "status": "lawyer_approved", "template": "nda",
             "kind": "parties", "text": "Between {party}. " + hostile}]
    assembler = Assembler("za")
    assembled = await assembler.assemble(template="nda", jurisdiction="za", clauses=rows,
                                         terms={"party": hostile}, prior=None)
    first = assembled["sentences"][0]["text"]
    assert first == "Between " + hostile + ". " + hostile
    finished = await assembler.finish(template="nda", jurisdiction="za", assembled=assembled, notice=_LEGAL_NOTICE)
    assert word_paragraphs(base64.b64decode(finished["docx_base64"]))[0] == (first, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["us-east", "za"])
@pytest.mark.parametrize("template", list(TEMPLATES))
@pytest.mark.parametrize("variant", ["complete", "missing", "empty", "no_clause"])
async def test_mako_assembly_matches_plain_policy_and_redline_word_text(region, template, variant):
    """The Mako path gives the same words as drafting.fill_clause, and Word matches docx_redline."""
    jurisdiction = JURISDICTION[region]
    rows = [{"id": slot, "region": region, "jurisdiction": jurisdiction, "status": "lawyer_approved",
             "template": template, "kind": slot, "text": WORDING[slot]} for slot in TEMPLATES[template]]
    terms = dict(TERMS[region])
    if variant == "missing":
        del terms["party"]
    elif variant == "empty":
        terms["days"] = ""
    elif variant == "no_clause":
        rows.pop()
    expected = drafting.assemble_draft(template=template, clauses=rows, terms=terms)
    assembler = Assembler(region)
    assembled = await assembler.assemble(template=template, jurisdiction=jurisdiction, clauses=rows,
                                         terms=terms, prior=None)
    assert assembled["text"] == expected["text"]
    for key in ("library", "review", "reused", "sentences"):
        assert assembled[key] == expected[key]
    finished = await assembler.finish(template=template, jurisdiction=jurisdiction, assembled=assembled,
                                      notice=_LEGAL_NOTICE)
    blocks = [[{"kind": "ins" if row["review"] else "plain", "text": row["text"]}]
              for row in expected["sentences"]] + [[{"kind": "plain", "text": _LEGAL_NOTICE}]]
    ours = word_paragraphs(base64.b64decode(finished["docx_base64"]))
    theirs = word_paragraphs(docx_bytes(blocks))
    assert [text for text, _ in ours] == [text for text, _ in theirs]


def test_eight_slot_layouts_match_the_slot_order():
    for template, slots in TEMPLATES.items():
        layout = (assembly.TEMPLATE_DIR / (template + ".md")).read_text()
        assert layout == "\n\n".join("${ paragraphs[" + repr(slot) + "] }" for slot in slots)
    assert assembly.DOCUMENT.is_file()


def test_no_docassemble_left_in_the_room():
    for path in [*(ROOT / "agents/zenas").rglob("*"), ROOT / ".env.example", *(ROOT / ".github").rglob("*")]:
        if path.is_file() and path.suffix in {".py", ".yml", ".yaml", ".txt", ".toml", ".example", ""}:
            assert "docassemble" not in path.read_text(errors="ignore").lower(), path
    assert not (ROOT / "agents/zenas/docassemble").exists()


@pytest.mark.asyncio
async def test_trigger_legal_handoff_drafts_in_process_with_notice(monkeypatch):
    import command
    import trigger_routes
    from types import SimpleNamespace
    from uuid import UUID
    from fastapi import FastAPI
    store, ladder = await _room("za", "nda")
    app = FastAPI()

    @app.post("/v1/zenas/drafts", status_code=201)
    async def drafts(body: dict):
        return await draft_contract(store, ladder, None, box_region="za", **body)
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(app=app, box_region=lambda: "za",
                                                             TANK_API_KEY="placeholder-tank-key"))
    response = await trigger_routes.zenas_draft(
        {"template": "nda", "jurisdiction": "za", "text": "Draft a civil NDA.", "terms": TERMS["za"]},
        command.Actor(UUID(TENANT), "founder", True))
    result = json.loads(response.body)
    assert response.status_code == 201
    assert result["text"].endswith(_LEGAL_NOTICE)
    assert result["notice"] == "legal information, not legal advice"
    assert result["model_calls"] == 0 and result["review"] == []
    assert word_paragraphs(base64.b64decode(result["docx_base64"]))[-1] == (_LEGAL_NOTICE, False)
