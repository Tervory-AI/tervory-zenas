"""Drafts, playbook redlines, and tenant registration. Models are mocks."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.zenas.app import TenantIn  # noqa: E402
from agents.zenas.core.drafting import TEMPLATES  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.service import (  # noqa: E402
    add_approved_clause,
    add_playbook,
    draft_contract,
    review_contract,
)
from agents.zenas.core.store import MemoryStore, register_home  # noqa: E402

TENANT_A = "00000000-0000-0000-0000-0000000000a1"
TENANT_B = "00000000-0000-0000-0000-0000000000b2"
SA_ID = "8001015009087"
CLAUSE = "Keep the mutual NDA confidential for {term}."


class Model:
    def __init__(self, content: str) -> None:
        self.content = content
        self.sent: list[object] = []

    async def model(self, messages: list[dict[str, str]], task_type: str) -> str:
        self.sent.append({"task_type": task_type, "messages": messages})
        return self.content


def _docx(payload: dict) -> str:
    raw = base64.b64decode(payload["docx_base64"])
    assert raw[:2] == b"PK"
    with zipfile.ZipFile(BytesIO(raw)) as package:
        xml = package.read("word/document.xml").decode("utf-8")
        assert "[Content_Types].xml" in package.namelist()
        assert package.read("[Content_Types].xml")
    return xml


@pytest.mark.asyncio
async def test_tenant_home_is_set_once_and_never_defaulted() -> None:
    with pytest.raises(ValidationError):
        TenantIn.model_validate({"tenant_id": TENANT_A})
    assert TenantIn.model_fields["home_region"].is_required()
    column = [
        line
        for line in (ROOT / "agents" / "zenas" / "schema.sql").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("home_region")
    ]
    assert column == ["    home_region  text NOT NULL,"]

    store = MemoryStore()
    blank = await register_home(store, tenant_id=TENANT_A, home_region="  ", box_region="us-east")
    assert blank["ok"] is False
    assert blank["reason"] == "home region is required"
    missing = await register_home(store, tenant_id=TENANT_A, home_region=None, box_region="us-east")
    assert missing["ok"] is False
    abroad = await register_home(store, tenant_id=TENANT_A, home_region="za", box_region="us-east")
    assert abroad["ok"] is False
    assert abroad["reason"] == "needs the South Africa region"
    assert store.tenants == []

    made = await register_home(store, tenant_id=TENANT_A, home_region="us-east", box_region="us-east")
    assert made["ok"] is True
    assert made["created"] is True
    assert made["home_region"] == "us-east"
    again = await register_home(store, tenant_id=TENANT_A, home_region="us-east", box_region="us-east")
    assert again["created"] is False
    changed = await register_home(store, tenant_id=TENANT_A, home_region="za", box_region="za")
    assert changed["ok"] is False
    assert changed["reason"] == "home region is already set"
    assert store.tenants == [{"tenant_id": TENANT_A, "home_region": "us-east"}]


@pytest.mark.asyncio
async def test_draft_uses_approved_clauses_and_marks_the_rest() -> None:
    store = MemoryStore()
    ladder = LadderMemory()
    await store.register_tenant(TENANT_A, "us-east")
    await ladder.register_tenant(TENANT_A, "us-east")
    refused = await add_approved_clause(
        store,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        template="nda",
        kind="confidentiality",
        text=CLAUSE,
        approved_by="",
        admission_no="",
    )
    assert refused["ok"] is False
    assert store.clauses == []
    identified = await add_approved_clause(
        store,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        template="nda",
        kind="confidentiality",
        text=f"ID {SA_ID} keeps the mutual NDA.",
        approved_by="Ada Counsel",
        admission_no="12345",
    )
    assert identified["ok"] is False
    added = await add_approved_clause(
        store,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        template="nda",
        kind="confidentiality",
        text=CLAUSE,
        approved_by="Ada Counsel",
        admission_no="12345",
    )
    assert added["ok"] is True
    criminal = await draft_contract(
        store,
        ladder,
        Model("{}"),
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        template="nda",
        jurisdiction="us",
        narrative="We have a bail hearing tomorrow.",
    )
    assert criminal["ok"] is False
    assert store.documents == []

    model = Model(json.dumps({"sentences": [{"slot": "confidentiality", "text": "Now see 9 Del. C. § 999."}]}))
    quiet = await draft_contract(
        store,
        ladder,
        model,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        template="nda",
        jurisdiction="us",
        terms={"term": "two years"},
        narrative="A mutual NDA for two years.",
    )
    assert quiet["ok"] is True
    assert quiet["model_calls"] == 0
    assert quiet["library"] == ["confidentiality"]
    assert "parties" in quiet["review"]
    assert "Keep the mutual NDA confidential for two years." in quiet["text"]
    assert "9 Del. C. § 999" not in quiet["text"]
    xml = _docx(quiet)
    assert "w:ins" in xml
    assert "w:del" not in xml

    await ladder.add_worked(
        {
            "region": "us-east",
            "tenant_id": TENANT_A,
            "source": "released",
            "jurisdiction": "us",
            "fact_pattern": {
                "template": "nda",
                "features": {"term": "one year"},
                "text": "Edited sentence.",
                "sentences": [
                    {
                        "slot": "confidentiality",
                        "text": "Edited sentence.",
                        "library": False,
                        "review": True,
                        "keys": ["term"],
                    }
                ],
            },
            "element_findings": [],
            "outcome": "released",
        }
    )
    revised = await draft_contract(
        store,
        ladder,
        model,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        template="nda",
        jurisdiction="us",
        terms={"term": "two years"},
        narrative="A mutual NDA.",
    )
    assert revised["model_calls"] == 1
    assert revised["cost_usd"] > 0
    assert "9 Del. C. § 999" not in revised["text"]
    assert "Keep the mutual NDA confidential for two years." in revised["text"]

    await ladder.add_worked(
        {
            "region": "us-east",
            "tenant_id": TENANT_A,
            "source": "released",
            "jurisdiction": "us",
            "fact_pattern": {
                "template": "supply",
                "features": {"goods": "widgets"},
                "text": "Reused supply agreement.",
                "sentences": [
                    {"slot": slot, "text": "Reused supply agreement.", "library": True, "review": False, "keys": ["goods"]}
                    for slot in TEMPLATES["supply"]
                ],
            },
            "element_findings": [],
            "outcome": "released",
        }
    )
    await store.register_tenant(TENANT_B, "us-east")
    await ladder.register_tenant(TENANT_B, "us-east")
    shared = await draft_contract(
        store,
        ladder,
        Model("should not be called"),
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        template="supply",
        jurisdiction="us",
        terms={"goods": "widgets"},
        narrative="A supply agreement for widgets.",
    )
    assert shared["ok"] is True
    assert shared["reused"] is True
    assert shared["text"] == "Reused supply agreement."
    assert shared["model_calls"] == 0

    za = MemoryStore()
    za_ladder = LadderMemory()
    await za.register_tenant(TENANT_A, "za")
    await za_ladder.register_tenant(TENANT_A, "za")
    foreign = await draft_contract(
        za,
        za_ladder,
        Model("{}"),
        tenant_id=TENANT_A,
        home_region="za",
        box_region="za",
        template="nda",
        jurisdiction="za",
        terms={"term": "two years"},
        narrative="A mutual NDA.",
    )
    assert foreign["ok"] is True
    assert CLAUSE.split("{")[0] not in foreign["text"]
    assert foreign["library"] == []


@pytest.mark.asyncio
async def test_review_redlines_are_tracked_changes_for_that_tenant() -> None:
    store = MemoryStore()
    await store.register_tenant(TENANT_A, "us-east")
    await store.register_tenant(TENANT_B, "us-east")
    book = await add_playbook(
        store,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        name="NDA",
        template="nda",
        positions=[
            {
                "kind": "confidentiality",
                "stance": "must",
                "text": "Keep the mutual NDA confidential for two years.",
            }
        ],
    )
    assert book["ok"] is True
    hidden = await review_contract(
        store,
        Model("low"),
        tenant_id=TENANT_B,
        home_region="us-east",
        box_region="us-east",
        playbook_id=book["playbook"]["id"],
        text="Keep the mutual NDA confidential for five years.",
    )
    assert hidden["ok"] is False
    assert hidden["reason"] == "no such playbook"

    model = Model("low")
    reviewed = await review_contract(
        store,
        model,
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        playbook_id=book["playbook"]["id"],
        text="Keep the mutual NDA confidential for five years.",
    )
    assert reviewed["ok"] is True
    assert reviewed["deviations"] == 1
    assert reviewed["model_calls"] == 1
    assert reviewed["findings"][0]["risk"] == "low"
    assert reviewed["inserts"] == ["Keep the mutual NDA confidential for two years."]
    xml = _docx(reviewed)
    assert "w:ins" in xml
    assert "w:del" in xml
    assert "Keep the mutual NDA confidential for five years." in xml
    assert "Keep the mutual NDA confidential for two years." in xml

    same = await review_contract(
        store,
        Model("high"),
        tenant_id=TENANT_A,
        home_region="us-east",
        box_region="us-east",
        playbook_id=book["playbook"]["id"],
        text="Keep the mutual NDA confidential for two years.",
    )
    assert same["deviations"] == 0
    assert same["model_calls"] == 0
    assert "w:ins" not in _docx(same)


def test_templates_and_copies_and_policy() -> None:
    assert set(TEMPLATES) == {
        "nda",
        "supply",
        "distribution",
        "sale_of_goods",
        "sale_of_goods_cisg",
        "freight",
        "services",
        "cross_border_agency",
    }
    for name in ("drafting.py", "review.py", "docx_redline.py"):
        core = (ROOT / "agents" / "zenas" / "core" / name).read_bytes()
        shared = (ROOT / "tank" / "skills" / "zenas.shared" / name).read_bytes()
        assert core == shared
    policy = (ROOT / "tank" / "schema.sql").read_text(encoding="utf-8")
    assert "'zenas.draft'" in policy
    assert "'zenas.review'" in policy
    app = (ROOT / "agents" / "zenas" / "app.py").read_text(encoding="utf-8")
    for path in ("/tenants", "/clauses", "/playbooks", "/drafts", "/contract-reviews"):
        assert path in app
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "push:" in ci
    assert "branches: [main]" in ci


@pytest.mark.asyncio
async def test_draft_and_review_skill_evals() -> None:
    import skala

    failed: list[str] = []
    for name in ("zenas.draft", "zenas.review"):
        package = ROOT / "tank" / "skills" / name / "1.0.0"
        spec = importlib.util.spec_from_file_location(name.replace(".", "_"), package / "run.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for line in (package / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            result = await module.run(case["input"], None)
            ok, why = skala.score_case(case["expected"], result, case.get("tolerance") or "exact")
            if not ok:
                failed.append(f"{name} {case['name']}: {why}")
    assert not failed, "\n".join(failed)
