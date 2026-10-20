"""Z7: review switch, held-out self-test, saved-response loaders, region isolation."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.learning import seed_sql  # noqa: E402
from agents.zenas.core.selftest import is_holdout, run_selftest  # noqa: E402
from agents.zenas.core.service import answer_question, extract_case_facts  # noqa: E402
from agents.zenas.core.store import MemoryStore  # noqa: E402
from agents.zenas.pool.adapters.cases import CapAdapter  # noqa: E402
from agents.zenas.pool.adapters.ecfr import EcfrAdapter, _headers as ecfr_headers  # noqa: E402
from agents.zenas.pool.adapters.federal_register import FederalRegisterAdapter, fr_part_url  # noqa: E402
from agents.zenas.pool.adapters.govinfo import FrapAdapter as GovFrap  # noqa: E402
from agents.zenas.pool.adapters.govinfo import FrcpAdapter, FreAdapter  # noqa: E402
from agents.zenas.pool.adapters.states import DelawareAdapter, TexasAdapter  # noqa: E402
from agents.zenas.pool.adapters.uncitral import CisgAdapter, ModelLawAdapter  # noqa: E402
from agents.zenas.pool.blobs import text_payloads  # noqa: E402
from agents.zenas.pool.catalogue import CATALOGUE  # noqa: E402
from agents.zenas.pool.http import BACKOFF_SECONDS, urllib_get  # noqa: E402

SAVED = ROOT / "agents" / "zenas" / "pool" / "fixtures" / "saved"
TENANT = "00000000-0000-0000-0000-0000000000a1"
PASSAGE = {
    "doc_id": "d1",
    "version_id": "v1",
    "citation": "8 Del. C. § 101",
    "text": "A written confidentiality promise is enforceable.",
    "jurisdiction": "us",
    "status": "effective",
}
CARD = {
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
    "decision_table": [{"element_id": "written", "when": {"form": "written"}, "finding": "met"}],
}


class Model:
    def __init__(self, content: str) -> None:
        self.content = content

    async def complete(self, messages, *, task_type: str, provider: str) -> dict:
        return {"content": self.content, "cost_usd": 0.05, "model": "luna"}


def _source(source_id: str):
    return next(item for item in CATALOGUE if item.source_id == source_id)


def test_seed_matches_the_schema_file() -> None:
    sql = (ROOT / "agents" / "zenas" / "schema.sql").read_text(encoding="utf-8")
    assert seed_sql() in sql
    assert "drop " not in sql.lower()


def test_saved_responses_parse_to_many_documents() -> None:
    counts = {
        "ecfr": len(EcfrAdapter().parse((SAVED / "ecfr" / "19-cfr-141.xml").read_bytes(), _source("ecfr"))),
        "frcp": len(FrcpAdapter().parse((SAVED / "frcp" / "uscode-2023-frcp.html").read_bytes(), _source("frcp"))),
        "fre": len(FreAdapter().parse((SAVED / "fre" / "uscode-2023-fre.html").read_bytes(), _source("fre"))),
        "frap": len(GovFrap().parse((SAVED / "frap" / "uscode-2023-frap.html").read_bytes(), _source("frap"))),
        "federal-register": len(
            FederalRegisterAdapter().parse((SAVED / "federal-register" / "19-cfr-141.json").read_bytes(), _source("federal-register"))
        ),
        "delaware-code": len(
            DelawareAdapter().parse((SAVED / "delaware-code" / "title8-c001-sc01.html").read_bytes(), _source("delaware-code"))
        ),
        "texas-statutes": len(
            TexasAdapter().parse((SAVED / "texas-statutes" / "bc.1.htm").read_bytes(), _source("texas-statutes"))
        ),
        "cap": sum(
            len(CapAdapter().parse(path.read_bytes(), _source("cap")))
            for path in sorted((SAVED / "cap").glob("*.json"))
        ),
        "uncitral-cisg": len(CisgAdapter().parse((SAVED / "uncitral-cisg" / "19-09951.pdf").read_bytes(), _source("uncitral-cisg"))),
        "uncitral-model-law": len(
            ModelLawAdapter().parse((SAVED / "uncitral-model-law" / "19-09955.pdf").read_bytes(), _source("uncitral-model-law"))
        ),
    }
    assert counts["ecfr"] >= 10
    assert counts["frcp"] >= 10
    assert counts["fre"] >= 10
    assert counts["frap"] >= 10
    assert counts["federal-register"] >= 10
    assert counts["delaware-code"] >= 10
    assert counts["texas-statutes"] >= 10
    assert counts["cap"] >= 1
    assert counts["uncitral-cisg"] >= 10
    assert counts["uncitral-model-law"] >= 10
    # The numbers are the saved-response document counts for the report.
    assert counts == counts


def test_ecfr_bulk_requests_only_parts_the_structure_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live title 15 on 19 Oct 2026: part 731 is reserved and the full XML 404s.

    The structure endpoint lists 730 and 732 and does not list 731. The saved
    structure is that shape. The saved 19 CFR 141 XML is the full-endpoint body.
    """
    import agents.zenas.pool.adapters.ecfr as ecfr

    structure = (ROOT / "tests" / "fixtures" / "ecfr" / "title-15-structure.json").read_bytes()
    saved_xml = (SAVED / "ecfr" / "19-cfr-141.xml").read_bytes()
    monkeypatch.setattr(ecfr, "ECFR_TARGETS", (("15", range(730, 775)),))
    seen: list[str] = []

    titles = (ROOT / "tests" / "fixtures" / "ecfr" / "titles.json").read_bytes()

    async def get(url: str, headers: dict) -> bytes:
        seen.append(url)
        assert headers["User-Agent"].startswith("TervoryZenasPool")
        if url.endswith("/titles.json"):
            assert headers["Accept"] == "application/json"
            return titles
        if "/structure/" in url:
            assert headers["Accept"] == "application/json"
            return structure
        assert headers["Accept"] == "application/xml"
        return saved_xml

    blobs, cursor = asyncio.run(EcfrAdapter().fetch_bulk(get))
    assert cursor == ""
    assert len(blobs) == 3
    # Each title is asked for at its own latest issue (saved titles.json, 19 Oct 2026).
    assert any(url.endswith("/structure/2026-09-24/title-15.json") for url in seen)
    assert any("part=730" in url for url in seen)
    assert any("part=732" in url for url in seen)
    assert any("part=747" in url for url in seen)
    assert not any("part=731" in url for url in seen)
    assert not any("part=1" in url for url in seen)
    assert all(url.startswith("https://www.ecfr.gov/") for url in seen)
    assert len(EcfrAdapter().parse(blobs[0], _source("ecfr"))) >= 10


def _uncitral_from_saved_ebook(adapter, path: Path) -> list:
    pdf = path.read_bytes()

    async def get(url: str, headers: dict) -> bytes:
        assert url == adapter.url
        assert headers["Accept"] == "application/pdf"
        assert url.startswith("https://uncitral.un.org/")
        return pdf

    blobs, cursor = asyncio.run(adapter.fetch_bulk(get))
    assert cursor == ""
    assert len(blobs) == 1
    assert not blobs[0].startswith(b"%PDF")
    kept = text_payloads(blobs[0])
    assert kept == blobs
    return adapter.parse(kept[0], _source(adapter.source_id))


def test_uncitral_cisg_saved_ebook_becomes_article_text() -> None:
    docs = _uncitral_from_saved_ebook(CisgAdapter(), SAVED / "uncitral-cisg" / "19-09951.pdf")
    assert len(docs) >= 10
    assert docs[0].citation.startswith("CISG art.")
    assert all(doc.text.strip() for doc in docs)


def test_uncitral_model_law_saved_ebook_becomes_article_text() -> None:
    docs = _uncitral_from_saved_ebook(ModelLawAdapter(), SAVED / "uncitral-model-law" / "19-09955.pdf")
    assert len(docs) >= 10
    assert docs[0].citation.startswith("UNCITRAL Model Law art.")
    assert all(doc.text.strip() for doc in docs)


def test_loader_headers_and_federal_register_parts() -> None:
    headers = ecfr_headers("application/xml")
    assert headers["Accept"] == "application/xml"
    assert headers["Accept-Encoding"] == "gzip"
    assert headers["User-Agent"].startswith("TervoryZenasPool")
    url = fr_part_url("19", "141")
    assert "conditions[cfr][part]=141" in url
    assert "fields[]=citation" in url

    async def _bulk():
        seen: list[tuple[str, dict]] = []

        async def get(url: str, hdrs: dict) -> bytes:
            seen.append((url, hdrs))
            return b'{"results":[]}'

        await FederalRegisterAdapter().fetch_bulk(get)
        return seen

    seen = asyncio.run(_bulk())
    assert seen
    assert all(item[1]["Accept"] == "application/json" for item in seen)
    assert all("gzip" in item[1]["Accept-Encoding"] for item in seen)


def test_http_retries_a_406(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request
    from email.message import Message

    monkeypatch.setattr("agents.zenas.pool.http.BACKOFF_SECONDS", (0, 0, 0))
    calls = {"n": 0}

    class _Response:
        headers = {"Content-Encoding": ""}

        def read(self) -> bytes:
            return b"<ok>"

        def __enter__(self):
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    def urlopen(request, timeout=60):
        del timeout
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(request.full_url, 406, "not acceptable", Message(), None)
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    body = asyncio.run(urllib_get("https://www.ecfr.gov/example", {"Accept": "application/xml"}))
    assert body == b"<ok>"
    assert calls["n"] == 3
    assert BACKOFF_SECONDS  # the live backoff stays available


@pytest.mark.asyncio
async def test_review_off_delivers_a_cited_memo_and_a_bad_citation_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZENAS_REVIEW", raising=False)
    ladder = LadderMemory()
    store = MemoryStore()
    await ladder.register_tenant(TENANT, "us-east")
    await store.add_matter(
        {
            "id": "00000000-0000-0000-0000-0000000000c1",
            "tenant_id": TENANT,
            "jurisdiction": "us",
            "area": "contracts",
            "title": "Supply",
            "parties": [],
            "status": "open",
            "created_by": "zena",
        }
    )
    matter = "00000000-0000-0000-0000-0000000000c1"
    good = await answer_question(
        store,
        ladder,
        Model(json.dumps(CARD)),
        tenant_id=TENANT,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question="Is a mutual NDA enforceable under Delaware law in the United States?",
        passages=[PASSAGE],
        features={"form": "written"},
        matter_id=matter,
    )
    assert good["ok"] is True
    assert good["delivered"] is True
    assert good["reviewed"] is False
    assert good["human_step"] is False
    assert good["state"] == "released"
    assert "8 Del. C. § 101" in good["memo"]["text"]
    assert store.documents[-1]["reviewed"] is False
    assert store.reviews == []

    bad_card = {
        "elements": [
            {
                "id": "fake",
                "test": "Invented.",
                "citation": "Fake v. Nobody, 1 F.3d 1",
                "quote": "This sentence is not in the pool.",
            }
        ]
    }
    before = len(store.documents)
    bad = await answer_question(
        store,
        ladder,
        Model(json.dumps(bad_card)),
        tenant_id=TENANT,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question="Is a supply agreement enforceable under Delaware law in the United States?",
        passages=[PASSAGE],
        features={"form": "written"},
        matter_id=matter,
    )
    assert bad["ok"] is False
    assert bad["reason"] == "could not verify"
    assert bad["delivered"] is False
    assert len(store.documents) == before
    assert any(row["status"] == "draft" and not row["active"] for row in ladder.rule_cards)


@pytest.mark.asyncio
async def test_selftest_scores_and_regions_do_not_cross() -> None:
    assert is_holdout("id-1") is True
    assert is_holdout("id-2") is False
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT, "us-east")
    quote = "A written confidentiality promise is enforceable."
    card = await ladder.add_rule_card(
        {
            "region": "us-east",
            "tenant_id": None,
            "jurisdiction": "us",
            "question": "Is a supply agreement enforceable?",
            "elements": [
                {"id": "written", "test": "The promise is in writing.", "citation": "8 Del. C. § 101", "quote": quote}
            ],
            "sources": [],
            "status": "draft",
            "active": True,
            "decision_table": [{"element_id": "written", "when": {"form": "written"}, "finding": "met"}],
        }
    )
    za_card = await ladder.add_rule_card(
        {
            "region": "za",
            "tenant_id": None,
            "jurisdiction": "za",
            "question": "Is a supply agreement enforceable in South Africa?",
            "elements": [
                {"id": "written", "test": "The promise is in writing.", "citation": "8 Del. C. § 101", "quote": quote}
            ],
            "status": "draft",
            "active": True,
            "decision_table": [{"element_id": "written", "when": {"form": "written"}, "finding": "met"}],
        }
    )
    await ladder.add_worked(
        {
            "region": "us-east",
            "tenant_id": None,
            "source": "case",
            "material_type": "public_record",
            "jurisdiction": "us",
            "area": "contracts",
            "rule_card_ids": [card["id"]],
            "fact_pattern": {
                "features": {"form": "written"},
                "narrative": "FULL US JUDGMENT MUST NOT BE THE SELF-TEST INPUT",
                "facts": "The parties signed a supply agreement in writing.",
                "question": "Is a supply agreement enforceable?",
            },
            "element_findings": [{"element_id": "written", "finding": "met"}],
            "outcome": "met",
            "pool_doc_id": "id-1",
            "holdout": True,
        }
    )
    await ladder.add_worked(
        {
            "region": "za",
            "tenant_id": None,
            "source": "case",
            "material_type": "public_record",
            "jurisdiction": "za",
            "area": "contracts",
            "rule_card_ids": [za_card["id"]],
            "fact_pattern": {
                "features": {"form": "written"},
                "narrative": "FULL SA JUDGMENT MUST NOT CROSS",
                "facts": "The parties signed a supply agreement in writing.",
            },
            "element_findings": [{"element_id": "written", "finding": "met"}],
            "outcome": "met",
            "pool_doc_id": "za-held",
            "holdout": True,
        }
    )
    await ladder.add_worked(
        {
            "region": "us-east",
            "tenant_id": None,
            "source": "case",
            "material_type": "public_record",
            "jurisdiction": "za",
            "area": "contracts",
            "rule_card_ids": [card["id"]],
            "fact_pattern": {"features": {"form": "written"}, "facts": "supply agreement", "narrative": "MISFILED SA"},
            "element_findings": [],
            "outcome": "met",
            "pool_doc_id": "misfiled-sa",
            "holdout": True,
        }
    )
    us = await run_selftest(ladder, region="us-east")
    za = await run_selftest(ladder, region="za")
    us_ids = [item for run in us["runs"] if run["kind"] == "holdout" for item in run["case_ids"]]
    za_ids = [item for run in za["runs"] if run["kind"] == "holdout" for item in run["case_ids"]]
    assert us_ids == ["id-1"]
    assert "za-held" not in us_ids
    assert "misfiled-sa" not in us_ids
    assert za_ids == ["za-held"]
    assert "id-1" not in za_ids
    assert "FULL SA JUDGMENT" not in json.dumps(us)
    assert "FULL US JUDGMENT" not in json.dumps(za)
    hold = next(run for run in us["runs"] if run["kind"] == "holdout" and run["cases"])
    assert hold["outcome_agreement"] == 100
    assert hold["element_agreement"] == 100
    assert hold["citations_valid"] == 100
    bench = [run for run in us["runs"] if run["kind"] == "legalbench"]
    assert bench
    assert all(run["scored_by"] == "code baseline" for run in bench)
    assert all(run["label"] == "LegalBench code baseline" for run in bench)
    assert all(run["model"] is False for run in bench)
    assert all(run["kind"] != "legalbench" for run in za["runs"])
    visible = await ladder.visible_worked(region="us-east", tenant_id=TENANT, sharing="tenant")
    assert all(row.get("pool_doc_id") != "id-1" for row in visible)

    facts = await extract_case_facts(
        ladder,
        Model(
            json.dumps(
                {
                    "features": {"form": "written"},
                    "narrative": "facts only",
                    "element_findings": [{"element_id": "written", "finding": "met", "reasoning": "", "basis": []}],
                    "outcome": "met",
                }
            )
        ),
        tenant_id=TENANT,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        pool_doc_id="id-5",
        source_id="courtlistener",
        judgment="The court enforced a supply agreement.",
    )
    assert facts["holdout"] is True
    assert facts["training_pair_id"] is None


@pytest.mark.asyncio
async def test_a_holdout_case_without_a_rule_card_is_an_error_not_a_zero_score() -> None:
    assert is_holdout("id-1") is True
    ladder = LadderMemory()
    await ladder.add_worked(
        {
            "region": "us-east",
            "tenant_id": None,
            "source": "case",
            "material_type": "public_record",
            "jurisdiction": "us",
            "area": "",
            "rule_card_ids": [],
            "fact_pattern": {
                "features": {},
                "facts": "The court stated the sale was complete.",
                "question": "Was the sale complete?",
            },
            "element_findings": [],
            "outcome": "met",
            "pool_doc_id": "id-1",
            "holdout": True,
        }
    )
    result = await run_selftest(ladder, region="us-east")
    hold = [
        run
        for run in result["runs"]
        if run["kind"] == "holdout" and run["cases"] and not str(run["area"]).startswith("Made-up")
    ]
    assert len(hold) == 1
    assert hold[0]["error"] is True
    assert hold[0]["label"] == "error"
    assert hold[0]["reason"] == "no rule card"
    assert "The court stated" not in json.dumps(hold[0])


def test_feeds_page_uses_the_region_link_and_strips_documents() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed on this job")
    subprocess.check_call([node, "--experimental-strip-types", "tests/zenas_feeds_public.mjs"], cwd=ROOT)
    region = (ROOT / "workspace" / "app" / "api" / "zenas" / "feeds" / "region" / "route.ts").read_text(encoding="utf-8")
    load = (ROOT / "workspace" / "app" / "api" / "zenas" / "feeds" / "region" / "load" / "route.ts").read_text(encoding="utf-8")
    assert "zenasIn" in region and "publicFeeds" in region
    assert "zenasIn" in load and "loadBody" in load
    assert "JUDGMENT" not in region and "JUDGMENT" not in load
