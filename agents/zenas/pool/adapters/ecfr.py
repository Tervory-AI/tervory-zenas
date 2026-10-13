"""eCFR renderer HTML. Point-in-time text from the versioner the renderer cites."""

from __future__ import annotations

import re

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.htmlparse import ecfr_sections
from agents.zenas.pool.model import LegalDocument, SourceSpec

# Checked-in wave. The ranges in the plan (EAR 730–774, ITAR 120–130, OFAC 500s,
# 19 CFR) are the target. Only these sections are fetched until the list grows.
ECFR_WAVE = (
    ("2024-07-01", "19", "141", "141.1"),
    ("2024-07-01", "15", "730", "730.1"),
    ("2024-07-01", "22", "120", "120.1"),
    ("2024-07-01", "31", "501", "501.101"),
)

_BODIES = {
    "19": "U.S. Customs and Border Protection",
    "15": "Bureau of Industry and Security",
    "22": "Directorate of Defense Trade Controls",
    "31": "Office of Foreign Assets Control",
}


def ecfr_url(on: str, title: str, part: str, section: str) -> str:
    return (
        "https://www.ecfr.gov/api/renderer/v1/content/enhanced/"
        f"{on}/title-{title}?part={part}&section={section}"
    )


def display_citation(raw: str) -> str:
    match = re.search(r"(\d+)\s+CFR\s+([0-9]+\.[0-9]+)", raw, re.I)
    if not match:
        return raw
    return f"{match.group(1)} C.F.R. § {match.group(2)}"


class EcfrAdapter:
    source_id = "ecfr"

    async def fetch(self, http_get) -> list[bytes]:
        blobs = []
        for on, title, part, section in ECFR_WAVE:
            url = ecfr_url(on, title, part, section)
            blobs.append(
                await http_get(url, {"Accept": "text/html", "User-Agent": "TervoryZenasPool/0.1"})
            )
        return blobs

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        documents = []
        for section, nodes in ecfr_sections(blob):
            citation = display_citation(section.citation)
            if "C.F.R." not in citation:
                continue
            title_no = citation.split(" ", 1)[0]
            documents.append(
                build_document(
                    source,
                    citation=citation,
                    title=section.heading or citation,
                    level="federal",
                    kind="regulation",
                    body=_BODIES.get(title_no, "Code of Federal Regulations"),
                    sections=nodes,
                    url=f"https://www.ecfr.gov/current/title-{title_no}",
                    status="in_force",
                    doc_date=section.as_of,
                    effective_from=section.as_of,
                )
            )
        return documents
