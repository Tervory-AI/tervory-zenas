"""Zenas learning register. No database and no network.

The register lists sources and empty panel slots. It does not download a corpus.
"""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.zenas.core.learning import (  # noqa: E402
    PANEL_QUESTIONS,
    SOUTH_AFRICA_HOST,
    US_SOURCES,
    CriminalRefused,
    GoldenCase,
    LearningError,
    NotCleared,
    PersonalRefused,
    SourceRow,
    apply_correction,
    download_corpus,
    merge_sources,
    panel_slots,
    seed_sql,
    sources,
)
from agents.zenas.core.scope_rules import REFUSED  # noqa: E402
from agents.zenas.core.store import schema_statements  # noqa: E402
from agents.zenas.pool.catalogue import CATALOGUE  # noqa: E402

SCHEMA = ROOT / "agents" / "zenas" / "schema.sql"

_PLAN_NAMES = (
    "United States Code",
    "Code of Federal Regulations",
    "Federal Register",
    "19 CFR",
    "Harmonized Tariff Schedule",
    "15 CFR 730-774",
    "22 CFR 120-130",
    "31 CFR 500s",
    "30701",
    "14706",
    "CISG",
    "UNCITRAL model laws",
    "CourtListener",
    "Caselaw Access Project",
    "Federal Rules of Civil Procedure",
    "Federal Rules of Evidence",
    "Local rules",
    "Delaware General Corporation Law",
    "New York enacted commercial statutes",
    "Texas enacted commercial statutes",
    "UCC Articles 1, 2 and 9",
)

_NOT_CLEARED = frozenset(
    {"uncitral-cisg", "uncitral-model-law", "local-rules", "ali-ucc-official"}
)


def _create_body(sql: str, table: str) -> str:
    marker = f"CREATE TABLE IF NOT EXISTS agent_zenas.{table} ("
    start = sql.index(marker) + len(marker)
    end = sql.index(");", start)
    return sql[start:end]


def _column_names(body: str) -> list[str]:
    names: list[str] = []
    for line in body.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or stripped.startswith("CONSTRAINT"):
            continue
        names.append(stripped.split()[0])
    return names


def test_schema_tables_are_additive_and_hold_no_corpus() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    lowered = sql.lower()
    assert "drop " not in lowered
    assert "rename " not in lowered
    assert "serial" not in lowered
    assert "generated always" not in lowered
    sources_body = _create_body(sql, "learning_sources")
    cases_body = _create_body(sql, "golden_cases")
    assert _column_names(sources_body) == [
        "source_id",
        "jurisdiction",
        "source",
        "licence",
        "terms_status",
        "last_update",
    ]
    assert "body" not in sources_body.lower()
    assert "corpus" not in sources_body.lower()
    assert _column_names(cases_body) == [
        "id",
        "jurisdiction",
        "slot",
        "question",
        "answer",
        "correction",
        "corrected_by",
        "admission_no",
        "corrected_at",
    ]
    assert "ADD COLUMN IF NOT EXISTS correction" in sql
    assert seed_sql() in sql
    for statement in schema_statements():
        upper = statement.upper()
        if upper.startswith("INSERT"):
            assert "ON CONFLICT" in upper and "DO NOTHING" in upper
        else:
            assert "IF NOT EXISTS" in upper


def test_us_sources_match_the_plan_and_are_not_downloaded() -> None:
    rows = sources()
    assert rows == US_SOURCES
    assert len({row.source_id for row in rows}) == len(rows)
    names = "\n".join(row.source for row in rows)
    for needle in _PLAN_NAMES:
        assert needle in names
    assert [field.name for field in fields(SourceRow)] == [
        "source_id",
        "jurisdiction",
        "source",
        "licence",
        "terms_status",
        "last_update",
    ]
    for row in rows:
        assert row.jurisdiction == "us"
        assert row.licence.strip()
        assert row.terms_status in ("cleared", "not_cleared")
        assert row.last_update is None
        with pytest.raises(LearningError):
            download_corpus(row)
    not_cleared = {row.source_id for row in rows if row.terms_status == "not_cleared"}
    assert not_cleared == _NOT_CLEARED
    local = next(row for row in rows if row.source_id == "local-rules")
    assert local.licence.startswith("Unknown.")
    with pytest.raises(NotCleared, match="not cleared"):
        download_corpus(local)
    model = next(row for row in rows if row.source_id == "uncitral-model-law")
    official = next(row for row in rows if row.source_id == "ali-ucc-official")
    assert "not stored" in model.licence.lower()
    assert "not stored" in official.licence.lower()
    assert SOUTH_AFRICA_HOST is None


def test_catalogue_terms_are_not_reclassified() -> None:
    by_id = {spec.source_id: spec for spec in CATALOGUE}
    for row in US_SOURCES:
        spec = by_id.get(row.source_id)
        if spec is None:
            continue
        if spec.bulk_use in ("forbidden", "unknown"):
            assert row.terms_status == "not_cleared"
        if spec.bulk_use == "allowed":
            assert row.terms_status == "cleared"
            assert spec.licence.split(".")[0] in row.licence or row.licence.startswith(spec.licence[:24])


def test_seed_does_not_overwrite_a_recorded_row() -> None:
    edited = US_SOURCES[0]
    changed = SourceRow(
        source_id=edited.source_id,
        jurisdiction=edited.jurisdiction,
        source=edited.source,
        licence=edited.licence,
        terms_status="not_cleared",
        last_update=None,
    )
    once = merge_sources({changed.source_id: changed})
    twice = merge_sources(once)
    assert once[changed.source_id].terms_status == "not_cleared"
    assert twice == once
    assert len(twice) == len(US_SOURCES)


def test_panel_slots_are_empty_and_criminal_matters_are_refused() -> None:
    module = (ROOT / "agents" / "zenas" / "core" / "learning.py").read_text(encoding="utf-8")
    for banned in ("urllib", "httpx", "requests", "socket"):
        assert banned not in module
    assert SOUTH_AFRICA_HOST is None
    for jurisdiction in ("us", "za"):
        slots = panel_slots(jurisdiction)
        assert len(slots) == PANEL_QUESTIONS == 20
        assert [slot.slot for slot in slots] == list(range(1, 21))
        for slot in slots:
            assert slot.question == ""
            assert slot.answer == ""
            assert slot.correction == ""
            assert slot.corrected_by == ""
            assert slot.admission_no == ""
            assert slot.corrected_at is None
    sql = seed_sql()
    for jurisdiction in ("us", "za"):
        for slot in range(1, 21):
            assert f"('{jurisdiction}', {slot})" in sql
    case = panel_slots("us")[0]
    with pytest.raises(CriminalRefused, match="criminal") as refused:
        apply_correction(case, "This is a bail hearing.")
    assert str(refused.value) == REFUSED
    assert case.correction == ""
    with pytest.raises(PersonalRefused):
        apply_correction(case, "The number on file is 123-45-6789.")
    assert case.question == "" and case.answer == ""
    updated = apply_correction(case, "Use the enacted wording.")
    assert updated.correction == "Use the enacted wording."
    assert updated.question == "" and updated.answer == ""
    assert all(row.correction == "" for row in panel_slots("us"))
    with pytest.raises(LearningError, match="empty"):
        apply_correction(case, "   ")
    with pytest.raises(LearningError):
        panel_slots("eu")


def test_source_fields_do_not_name_a_host() -> None:
    names = {field.name for field in fields(SourceRow)} | {field.name for field in fields(GoldenCase)}
    assert "host" not in names
    assert SOUTH_AFRICA_HOST is None
