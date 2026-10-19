"""eCFR renderer HTML and versioner XML.

The checked-in wave stays on ``fetch``. ``fetch_bulk`` walks the launch parts:
all of title 19, 15 CFR 730–774, 22 CFR 120–130, and 31 CFR 500–599.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from xml.etree import ElementTree as ET

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.htmlparse import ecfr_sections
from agents.zenas.pool.http import USER_AGENT
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

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

# None means every part the structure endpoint lists for that title.
ECFR_TARGETS: tuple[tuple[str, range | None], ...] = (
    ("19", None),
    ("15", range(730, 775)),
    ("22", range(120, 131)),
    ("31", range(500, 600)),
)


def ecfr_url(on: str, title: str, part: str, section: str) -> str:
    return (
        "https://www.ecfr.gov/api/renderer/v1/content/enhanced/"
        f"{on}/title-{title}?part={part}&section={section}"
    )


def ecfr_structure_url(on: str, title: str) -> str:
    return f"https://www.ecfr.gov/api/versioner/v1/structure/{on}/title-{title}.json"


def ecfr_full_url(on: str, title: str, part: str) -> str:
    return f"https://www.ecfr.gov/api/versioner/v1/full/{on}/title-{title}.xml?part={part}"


def _ecfr_date() -> str:
    return os.environ.get("ECFR_DATE", "2024-07-01").strip() or "2024-07-01"


def _headers(accept: str) -> dict[str, str]:
    """Accept matches the endpoint. Gzip is required by the versioner XML."""
    return {"Accept": accept, "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}


def _part_numbers(payload: dict) -> list[str]:
    found: list[str] = []

    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        if str(node.get("type") or "").lower() == "part" and node.get("identifier"):
            found.append(str(node["identifier"]))
        for child in node.get("children") or []:
            walk(child)

    walk(payload)
    return found


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
            blobs.append(await http_get(url, _headers("text/html")))
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        on = _ecfr_date()
        headers = _headers("application/xml")
        blobs: list[bytes] = []
        seen_resume = resume == ""
        for title, parts in ECFR_TARGETS:
            if parts is None:
                structure = await http_get(
                    ecfr_structure_url(on, title),
                    _headers("application/json"),
                )
                numbers = _part_numbers(json.loads(structure.decode("utf-8")))
            else:
                numbers = [str(part) for part in parts]
            for part in numbers:
                marker = f"{title}:{part}"
                if not seen_resume:
                    if marker == resume:
                        seen_resume = True
                    continue
                blobs.append(await http_get(ecfr_full_url(on, title, part), headers))
        return blobs, ""

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        if b"TYPE=\"SECTION\"" in blob or b"<DIV8" in blob:
            return _parse_ecfr_xml(blob, source)
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


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].upper()


def _parse_ecfr_xml(blob: bytes, source: SourceSpec) -> list[LegalDocument]:
    try:
        root = ET.fromstring(blob)
    except ET.ParseError:
        return []
    parents: dict[ET.Element, ET.Element] = {child: parent for parent in root.iter() for child in list(parent)}
    documents: list[LegalDocument] = []
    for element in root.iter():
        if _local(element.tag) not in {"DIV8", "SECTION"}:
            continue
        if element.attrib.get("TYPE", "SECTION").upper() not in {"SECTION", ""}:
            continue
        identifier = element.attrib.get("N") or ""
        if not identifier:
            continue
        title_no = ""
        current: ET.Element | None = element
        while current is not None:
            if _local(current.tag) in {"DIV1", "TITLE"} and current.attrib.get("N"):
                title_no = str(current.attrib["N"])
                break
            current = parents.get(current)
        if not title_no:
            meta = element.attrib.get("hierarchy_metadata") or ""
            found = re.search(r"(\d+)\s+CFR", meta) or re.search(r"title-(\d+)", meta)
            if found:
                title_no = found.group(1)
        if not title_no:
            continue
        heading = ""
        paragraphs: list[str] = []
        for child in list(element):
            name = _local(child.tag)
            text = " ".join("".join(child.itertext()).split())
            if name in {"HEAD", "HEADING"} and not heading:
                heading = text
            elif text:
                paragraphs.append(text)
        if not paragraphs and heading:
            paragraphs = [heading]
        if not paragraphs:
            continue
        citation = f"{title_no} C.F.R. § {identifier}"
        sections = [Section("", heading or citation, " ".join(paragraphs), [])]
        acted = None
        raw_date = element.attrib.get("effective") or ""
        if raw_date:
            try:
                acted = date.fromisoformat(raw_date[:10])
            except ValueError:
                acted = None
        documents.append(
            build_document(
                source,
                citation=citation,
                title=heading or citation,
                level="federal",
                kind="regulation",
                body=_BODIES.get(title_no, "Code of Federal Regulations"),
                sections=sections,
                url=f"https://www.ecfr.gov/current/title-{title_no}/part-{identifier.split('.')[0]}",
                status="in_force",
                doc_date=acted,
                effective_from=acted,
            )
        )
    return documents
