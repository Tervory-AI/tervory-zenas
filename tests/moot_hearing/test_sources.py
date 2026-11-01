from uuid import uuid4

import pytest

from agents.zenas.core.moot.hearing import HearingError
from agents.zenas.core.moot.hearing_sources import WORKED, build_problem
from .test_hearing import OWNER


def row():
    facts = "The notice reached the recipient on the day before the deadline."
    holding = "The appeal was dismissed because the notice was timely."
    rule = "Notice received before the deadline satisfies the time requirement."
    return {"id": str(uuid4()), "region": "us-east", "source_region": "us-east", "tenant_id": None,
        "holdout": False, "source": "case", "material_type": "public_record", "jurisdiction": "us",
        "fact_pattern": {"facts": facts, "issues": ["Was notice timely?"]}, "outcome": holding,
        "text": facts + "\n" + rule + "\n" + holding, "title": "Offline civil source", "citation": "Offline v Source [2026] US 1",
        "url": "https://example.invalid/judgment", "status": "current", "pool_doc_id": "source-1", "pool_version_id": "version-1",
        "docket_number": "1:26-cv-0001", "nature_of_suit": "civil appeal",
        "element_findings": [{"basis": [{"citation": "Offline v Source [2026] US 1", "quote": rule}]}]}


def test_source_snapshot_uses_real_stored_quotes_and_holding_is_separate():
    p = build_problem(row(), OWNER)
    assert p["authorities"][0]["verified"] and p["authorities"][0]["weight"] == "unknown"
    assert p["real_holding"] not in str(p["bench_notes"]) + str(p["bundles"])
    assert "w.region=$2" in WORKED and "w.tenant_id=$3 OR w.tenant_id IS NULL" in WORKED
    assert "w.holdout=false" in WORKED and "w.material_type='public_record'" in WORKED


@pytest.mark.parametrize("field,value", [("region", "za"), ("tenant_id", "99999999-9999-4999-8999-999999999999"), ("holdout", True),
    ("material_type", "client_work"), ("nature_of_suit", "criminal prosecution"), ("outcome", "Invented holding"),
    ("fact_pattern", {"facts": "Invented facts", "issues": ["Question"]})])
def test_no_foreign_private_holdout_criminal_or_invented_source(field, value):
    r = row(); r[field] = value
    with pytest.raises(HearingError): build_problem(r, OWNER)


def test_unsupported_rule_quote_is_never_upgraded_to_verified_authority():
    r = row(); r["element_findings"][0]["basis"][0]["quote"] = "Unsupported factor supposedly controls"
    assert build_problem(r, OWNER)["authorities"] == []


def test_paragraph_is_resolved_from_source_not_trusted_from_stored_model_metadata():
    from agents.zenas.core.moot.hearing_sources import verified_paragraph
    quote = "Notice received before the deadline satisfies the time requirement."
    source = "[1] A different procedural fact is recorded in this first paragraph.\n[2] " + quote
    assert verified_paragraph(source, quote) == 2
    assert verified_paragraph(source + "\n[3] " + quote, quote) is None
    r = row(); r["url"] = "javascript:alert(1)"
    with pytest.raises(HearingError, match="invalid_source_url"):
        build_problem(r, OWNER)
