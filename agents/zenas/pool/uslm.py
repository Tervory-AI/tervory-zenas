"""United States Legislative Markup (USLM) from uscode.house.gov.

One section element becomes one instrument. Subsections stay whole chunks.
"""

from __future__ import annotations

import re
from datetime import date
from xml.etree import ElementTree as ET

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

_NEST = ("subsection", "paragraph", "subparagraph", "clause", "subclause")


def is_uslm(blob: bytes) -> bool:
    head = blob[:4000].lstrip().lower()
    if b"uscdoc" in head or b"xml.house.gov/schemas/uslm" in head:
        return True
    return b"<title" in head and b"<section" in head


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _direct_text(element: ET.Element, name: str) -> str:
    for child in list(element):
        if _local(child.tag) == name:
            return " ".join("".join(child.itertext()).split())
    return ""


def _flatten(element: ET.Element, *, skip: set[str] | None = None) -> str:
    skip = skip or set()
    parts: list[str] = []
    if element.text and element.text.strip():
        parts.append(element.text.strip())
    for child in list(element):
        if _local(child.tag) in skip:
            if child.tail and child.tail.strip():
                parts.append(child.tail.strip())
            continue
        parts.append(_flatten(child, skip=skip))
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return " ".join(part for part in parts if part)


def _date_of(element: ET.Element) -> date | None:
    raw = element.attrib.get("effective") or element.attrib.get("effectiveDate") or ""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _path_for(num: str) -> str:
    num = num.strip().strip(".")
    if not num:
        return ""
    if num.startswith("("):
        return num
    if re.fullmatch(r"[0-9]+|[A-Za-z]", num):
        return f"({num})"
    return num


def _child_sections(element: ET.Element) -> list[Section]:
    nodes: list[Section] = []
    for child in list(element):
        name = _local(child.tag)
        if name in _NEST:
            num = child.attrib.get("value") or _direct_text(child, "num")
            num = re.sub(r"[()]", "", num).strip()
            heading = _direct_text(child, "heading")
            text = _flatten(child, skip={"num"})
            nodes.append(Section(_path_for(num), heading, text, _child_sections(child)))
        elif name == "content" and not nodes:
            text = _flatten(child)
            if text:
                nodes.append(Section("", "", text, []))
        elif name == "note":
            text = _flatten(child, skip={"num"})
            if text:
                nodes.append(Section("note", _direct_text(child, "heading") or "Note", text, []))
    return nodes


def _section_number(element: ET.Element) -> str:
    num_el = next((child for child in list(element) if _local(child.tag) == "num"), None)
    if num_el is not None:
        value = (num_el.attrib.get("value") or "").strip()
        if value:
            return value
    raw = _direct_text(element, "num")
    match = re.search(r"([0-9]+[0-9A-Za-z\-]*)", raw)
    return match.group(1) if match else ""


def parse_uslm(blob: bytes, source: SourceSpec) -> list[LegalDocument]:
    try:
        root = ET.fromstring(blob)
    except ET.ParseError:
        return []
    parents: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in list(parent):
            parents[child] = parent

    def title_of(element: ET.Element) -> str:
        current: ET.Element | None = element
        while current is not None:
            if _local(current.tag) == "title":
                num = next((child for child in list(current) if _local(child.tag) == "num"), None)
                if num is not None and num.attrib.get("value"):
                    return str(num.attrib["value"])
                match = re.search(r"(\d+)", _direct_text(current, "num"))
                if match:
                    return match.group(1)
            current = parents.get(current)
        return ""

    documents: list[LegalDocument] = []
    for element in root.iter():
        if _local(element.tag) != "section":
            continue
        number = _section_number(element)
        title_no = title_of(element)
        if not number or not title_no:
            continue
        heading = _direct_text(element, "heading") or f"{title_no} U.S.C. § {number}"
        sections = _child_sections(element)
        if not sections:
            text = _flatten(element, skip={"num", "heading"})
            if text:
                sections = [Section("", heading, text, [])]
        if not sections:
            continue
        citation = f"{title_no} U.S.C. § {number}"
        acted = _date_of(element)
        documents.append(
            build_document(
                source,
                citation=citation,
                title=heading,
                level="federal",
                kind="statute",
                body="Congress",
                sections=sections,
                url=f"https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title{title_no}-section{number}",
                status="in_force",
                doc_date=acted,
                effective_from=acted,
            )
        )
    return documents
