"""Consolidated South African Acts from the Laws.Africa content API.

The token is read from LAWS_AFRICA_API_TOKEN and sent as Authorization: Bearer.
It is never placed in a URL. A live load is skipped until the token is set.
Snapshots of Akoma Ntoso still parse without it.
"""

from __future__ import annotations

from datetime import date
from xml.etree import ElementTree as ET

from agents.zenas.pool.adapters.za_common import bearer_headers, laws_africa_token, reject_credential_url
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

API = "https://api.laws.africa/v3"

# (source_id, short title, FRBR path without a leading slash, number, year, regulatory_only)
ACTS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    ("companies-act", "Companies Act 71 of 2008", "akn/za/act/2008/71", "71", "2008", False),
    ("consumer-protection-act", "Consumer Protection Act 68 of 2008", "akn/za/act/2008/68", "68", "2008", False),
    ("popia", "Protection of Personal Information Act 4 of 2013", "akn/za/act/2013/4", "4", "2013", False),
    ("customs-excise-act", "Customs and Excise Act 91 of 1964", "akn/za/act/1964/91", "91", "1964", False),
    ("ita-act", "International Trade Administration Act 71 of 2002", "akn/za/act/2002/71", "71", "2002", False),
    ("ecta", "Electronic Communications and Transactions Act 25 of 2002", "akn/za/act/2002/25", "25", "2002", False),
    ("lra", "Labour Relations Act 66 of 1995", "akn/za/act/1995/66", "66", "1995", False),
    ("bcea", "Basic Conditions of Employment Act 75 of 1997", "akn/za/act/1997/75", "75", "1997", False),
    ("trade-marks-act", "Trade Marks Act 194 of 1993", "akn/za/act/1993/194", "194", "1993", False),
    (
        "conventional-arms-act",
        "National Conventional Arms Control Act 41 of 2002",
        "akn/za/act/2002/41",
        "41",
        "2002",
        True,
    ),
    ("firearms-control-act", "Firearms Control Act 60 of 2000", "akn/za/act/2000/60", "60", "2000", True),
)


def expression_url(frbr: str) -> str:
    """Latest English XML expression. The token is not part of this URL."""
    path = frbr.strip().strip("/")
    return f"{API}/{path}.xml"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _direct(element: ET.Element, name: str) -> str:
    for child in list(element):
        if _local(child.tag) == name:
            return " ".join("".join(child.itertext()).split())
    return ""


def _own_text(element: ET.Element) -> str:
    """Text of this node, without the text of nested structural children."""
    skip = {"subsection", "paragraph", "subparagraph", "clause", "subclause", "section"}
    parts: list[str] = []

    def walk(node: ET.Element, *, top: bool) -> None:
        if not top and _local(node.tag) in skip:
            return
        if node.text and node.text.strip():
            parts.append(node.text.strip())
        for child in list(node):
            walk(child, top=False)
            if child.tail and child.tail.strip():
                parts.append(child.tail.strip())

    walk(element, top=True)
    return " ".join(" ".join(parts).split())


def _section_node(element: ET.Element) -> Section:
    children = [
        _section_node(child)
        for child in list(element)
        if _local(child.tag) in {"subsection", "paragraph", "subparagraph", "clause", "subclause"}
    ]
    return Section(_direct(element, "num"), _direct(element, "heading"), _own_text(element), children)


def _top_sections(root: ET.Element) -> list[ET.Element]:
    parent: dict[ET.Element, ET.Element] = {}
    for node in root.iter():
        for child in list(node):
            parent[child] = node
    found: list[ET.Element] = []
    for element in root.iter():
        if _local(element.tag) != "section":
            continue
        above = parent.get(element)
        nested = False
        while above is not None:
            if _local(above.tag) == "section":
                nested = True
                break
            above = parent.get(above)
        if not nested:
            found.append(element)
    return found


def _frbr_date(root: ET.Element) -> date | None:
    for element in root.iter():
        if _local(element.tag) != "frbrdate":
            continue
        raw = (element.attrib.get("date") or "")[:10]
        try:
            return date.fromisoformat(raw)
        except ValueError:
            continue
    return None


def _section_number(raw: str) -> str:
    return raw.strip().strip(".").strip()


def parse_akn(blob: bytes, source: SourceSpec, *, number: str, year: str) -> list[LegalDocument]:
    """One AKN section becomes one instrument. Subsections stay chunks."""
    try:
        root = ET.fromstring(blob)
    except ET.ParseError:
        return []
    enacted = _frbr_date(root)
    documents: list[LegalDocument] = []
    for element in _top_sections(root):
        num = _section_number(_direct(element, "num"))
        if not num:
            continue
        node = _section_node(element)
        if not node.text and not node.children:
            continue
        citation = f"Act {number} of {year}, s {num}"
        title = node.heading or f"{source.name}, s {num}"
        documents.append(
            build_document(
                source,
                citation=citation,
                title=title,
                level="national",
                kind="statute",
                body="Parliament of South Africa",
                sections=[node],
                url=source.canonical_url,
                status="in_force",
                doc_date=enacted,
                effective_from=enacted,
            )
        )
    return documents


class LawsAfricaAdapter:
    def __init__(self, source_id: str, frbr: str, number: str, year: str) -> None:
        self.source_id = source_id
        self.frbr = frbr
        self.number = number
        self.year = year

    def _url(self) -> str:
        url = expression_url(self.frbr)
        reject_credential_url(url, laws_africa_token())
        return url

    async def _get(self, http_get) -> bytes:
        token = laws_africa_token()
        if not token:
            raise FetchNotConfigured("LAWS_AFRICA_API_TOKEN is not set")
        url = self._url()
        return await http_get(url, bearer_headers(token, "application/xml"))

    async def fetch(self, http_get) -> list[bytes]:
        return [await self._get(http_get)]

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        del resume
        url = self._url()
        blob = await self._get(http_get)
        return [blob], url

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_akn(blob, source, number=self.number, year=self.year)


def laws_africa_adapters() -> list[LawsAfricaAdapter]:
    return [LawsAfricaAdapter(source_id, frbr, number, year) for source_id, _title, frbr, number, year, _reg in ACTS]
