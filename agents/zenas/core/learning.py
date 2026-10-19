"""Learning register for Zenas.

One source row records jurisdiction, source, licence, terms status, and last
update. One golden-case row is an empty slot a lawyer can correct later.
This pass does not download a corpus and does not store a model text.
Criminal matters are refused. The South Africa host is not chosen.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from agents.zenas.core.pii_detect import detect
from agents.zenas.core.scope_rules import REFUSED, classify

TERMS_CLEARED = "cleared"
TERMS_NOT_CLEARED = "not_cleared"
PANEL_QUESTIONS = 20
JURISDICTIONS = ("us", "za")

# Not chosen. A later pass names the South Africa host. This one does not.
SOUTH_AFRICA_HOST: str | None = None

_PD = "Public domain. U.S. government work, 17 U.S.C. § 105."
_EDICT = (
    "Public domain. Enacted statute text is an edict of government. "
    "Publisher annotations and unofficial commentary are not stored."
)
_PRIVATE = frozenset(
    {"sa_id", "ssn", "us_passport", "phone", "address", "bank", "dob", "email"}
)


class LearningError(Exception):
    pass


class CriminalRefused(LearningError):
    def __init__(self) -> None:
        super().__init__(REFUSED)


class NotCleared(LearningError):
    def __init__(self) -> None:
        super().__init__("not cleared")


class PersonalRefused(LearningError):
    def __init__(self) -> None:
        super().__init__("Personal identifiers are not stored on a golden case.")


@dataclass(frozen=True)
class SourceRow:
    source_id: str
    jurisdiction: str
    source: str
    licence: str
    terms_status: str
    last_update: datetime | None = None


@dataclass(frozen=True)
class GoldenCase:
    jurisdiction: str
    slot: int
    question: str = ""
    answer: str = ""
    correction: str = ""
    corrected_by: str = ""
    admission_no: str = ""
    corrected_at: datetime | None = None


US_SOURCES: tuple[SourceRow, ...] = (
    SourceRow(
        source_id="govinfo-uscode",
        jurisdiction="us",
        source="United States Code (GovInfo)",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="ecfr",
        jurisdiction="us",
        source="Code of Federal Regulations (eCFR)",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="federal-register",
        jurisdiction="us",
        source="Federal Register",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="cfr-19",
        jurisdiction="us",
        source="19 CFR (customs)",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="usitc-htsus",
        jurisdiction="us",
        source="Harmonized Tariff Schedule of the United States (USITC)",
        licence=_PD + " Enacted schedule published by the U.S. International Trade Commission, 19 U.S.C. § 1202.",
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="ear-15-cfr",
        jurisdiction="us",
        source="EAR (15 CFR 730-774)",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="itar-22-cfr",
        jurisdiction="us",
        source="ITAR (22 CFR 120-130)",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="ofac-31-cfr",
        jurisdiction="us",
        source="OFAC (31 CFR 500s)",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="cogsa",
        jurisdiction="us",
        source="COGSA (46 USC 30701 note)",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="carmack",
        jurisdiction="us",
        source="Carmack (49 USC 14706)",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="uncitral-cisg",
        jurisdiction="us",
        source="CISG (UNCITRAL publication)",
        licence="United Nations / UNCITRAL copyright. The text is not stored.",
        terms_status=TERMS_NOT_CLEARED,
    ),
    SourceRow(
        source_id="uncitral-model-law",
        jurisdiction="us",
        source="UNCITRAL model laws",
        licence="United Nations / UNCITRAL copyright. Model texts are not stored.",
        terms_status=TERMS_NOT_CLEARED,
    ),
    SourceRow(
        source_id="courtlistener",
        jurisdiction="us",
        source="CourtListener (Free Law Project)",
        licence=(
            "Opinion text: public domain (edicts of government). "
            "CourtListener bulk files: free of known copyright restrictions (Free Law Project). "
            "Criminal matters are not stored."
        ),
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="cap",
        jurisdiction="us",
        source="Caselaw Access Project",
        licence=(
            "Unrestricted as of 2024-03-01. Harvard ended the commercial-use limits on this digitization. "
            "Criminal matters are not stored."
        ),
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="frcp",
        jurisdiction="us",
        source="Federal Rules of Civil Procedure",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="fre",
        jurisdiction="us",
        source="Federal Rules of Evidence",
        licence=_PD,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="local-rules",
        jurisdiction="us",
        source="Local rules of the districts the panel practises in",
        licence="Unknown. The panel has not named the districts.",
        terms_status=TERMS_NOT_CLEARED,
    ),
    SourceRow(
        source_id="delaware-code",
        jurisdiction="us",
        source="Delaware General Corporation Law",
        licence=_EDICT,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="ny-laws",
        jurisdiction="us",
        source="New York enacted commercial statutes",
        licence=_EDICT,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="texas-statutes",
        jurisdiction="us",
        source="Texas enacted commercial statutes",
        licence=_EDICT,
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="ucc-enacted",
        jurisdiction="us",
        source="UCC Articles 1, 2 and 9 as enacted by Delaware, New York and Texas",
        licence=_EDICT + " The official ALI text is a different source and is not stored.",
        terms_status=TERMS_CLEARED,
    ),
    SourceRow(
        source_id="ali-ucc-official",
        jurisdiction="us",
        source="UCC official text and comments",
        licence=(
            "Copyright American Law Institute and Uniform Law Commission. "
            "The official text and comments are not stored."
        ),
        terms_status=TERMS_NOT_CLEARED,
    ),
)


def sources() -> tuple[SourceRow, ...]:
    return US_SOURCES


def merge_sources(existing: dict[str, SourceRow] | None = None) -> dict[str, SourceRow]:
    """Insert missing rows. A row already recorded is left as it is."""
    found = dict(existing or {})
    for row in US_SOURCES:
        found.setdefault(row.source_id, row)
    return found


def panel_slots(jurisdiction: str) -> tuple[GoldenCase, ...]:
    if jurisdiction not in JURISDICTIONS:
        raise LearningError("jurisdiction must be us or za")
    return tuple(
        GoldenCase(jurisdiction=jurisdiction, slot=slot)
        for slot in range(1, PANEL_QUESTIONS + 1)
    )


def download_corpus(row: SourceRow) -> None:
    """Refuse a copy. Unknown terms stay not cleared. This pass stores no text."""
    if row.terms_status != TERMS_CLEARED:
        raise NotCleared()
    raise LearningError("this pass does not download a corpus")


def _refuse_criminal(*parts: str) -> None:
    for part in parts:
        if not part or not part.strip():
            continue
        if classify(part)["criminal"]:
            raise CriminalRefused()


def _refuse_personal(*parts: str) -> None:
    for part in parts:
        if not part or not part.strip():
            continue
        found = detect(part, parties=False)
        if any(match["kind"] in _PRIVATE for match in found["matches"]):
            raise PersonalRefused()


def apply_correction(
    case: GoldenCase,
    correction: str,
    *,
    corrected_by: str = "",
    admission_no: str = "",
    corrected_at: datetime | None = None,
) -> GoldenCase:
    """Record a lawyer's correction on one empty slot. The seed itself stays empty."""
    if case.jurisdiction not in JURISDICTIONS:
        raise LearningError("jurisdiction must be us or za")
    if case.slot < 1 or case.slot > PANEL_QUESTIONS:
        raise LearningError("the panel slot is out of range")
    text = (correction or "").strip()
    _refuse_criminal(case.question, case.answer, text, corrected_by, admission_no)
    _refuse_personal(case.question, case.answer, text, corrected_by, admission_no)
    if not text:
        raise LearningError("a correction is empty")
    return replace(
        case,
        correction=text,
        corrected_by=(corrected_by or "").strip(),
        admission_no=(admission_no or "").strip(),
        corrected_at=corrected_at,
    )


def _sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def seed_sql() -> str:
    """Idempotent inserts. A second apply does not overwrite a later correction."""
    parts: list[str] = []
    for row in US_SOURCES:
        parts.append(
            "INSERT INTO agent_zenas.learning_sources\n"
            "    (source_id, jurisdiction, source, licence, terms_status)\n"
            f"VALUES ({_sql(row.source_id)}, {_sql(row.jurisdiction)}, {_sql(row.source)}, "
            f"{_sql(row.licence)}, {_sql(row.terms_status)})\n"
            "ON CONFLICT (source_id) DO NOTHING;"
        )
    values = []
    for jurisdiction in JURISDICTIONS:
        for slot in range(1, PANEL_QUESTIONS + 1):
            values.append(f"    ({_sql(jurisdiction)}, {slot})")
    parts.append(
        "INSERT INTO agent_zenas.golden_cases (jurisdiction, slot)\n"
        "VALUES\n"
        + ",\n".join(values)
        + "\nON CONFLICT (jurisdiction, slot) DO NOTHING;"
    )
    return "\n".join(parts) + "\n"
