"""Parse the API's AKN HTML, using its table of contents for section anchors."""

from __future__ import annotations

from datetime import date
import json
from html.parser import HTMLParser
from urllib.parse import quote
from xml.etree import ElementTree as ET

from agents.zenas.pool.adapters.laws_africa_api import national_work, representation
from agents.zenas.pool.adapters.za_akn import ACTS, _direct, _section_node
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.model import LegalDocument, SourceSpec
from agents.zenas.pool.textutil import content_hash, document_id_for


class AknHTML(HTMLParser):
    """Translate the published akn-* HTML classes to the existing AKN parser."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = ET.Element("root")
        self.stack = [("root", self.root)]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        if self.skip:
            return
        attrs = dict(attrs)
        kind = next((part[4:] for part in (attrs.get("class") or "").split() if part.startswith("akn-")), tag)
        node = ET.SubElement(self.stack[-1][1], kind, {key: value for key, value in attrs.items() if value is not None})
        if tag not in {"br", "hr", "img", "input", "meta", "link", "wbr", "source", "area", "base", "col", "embed", "param"}:
            self.stack.append((tag, node))

    def handle_startendtag(self, tag, attrs):
        size = len(self.stack)
        self.handle_starttag(tag, attrs)
        if len(self.stack) > size:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.skip:
            return
        node = self.stack[-1][1]
        if len(node):
            node[-1].tail = (node[-1].tail or "") + data
        else:
            node.text = (node.text or "") + data


def toc_sections(toc):
    if isinstance(toc, dict):
        toc = toc.get("toc", toc.get("children", []))
    if not isinstance(toc, list):
        raise ValueError("invalid table of contents")
    for entry in toc:
        if not isinstance(entry, dict):
            raise ValueError("invalid table of contents entry")
        if entry.get("type") in {"section", "article", "rule"} or entry.get("basic_unit") is True:
            yield entry
        else:
            yield from toc_sections(entry.get("children", []))


def parse_expression(payload: dict, source: SourceSpec) -> list[LegalDocument]:
    work, expression = payload["work"], payload["expression"]
    frbr = national_work(work["frbr_uri"])
    if not frbr:
        raise ValueError("not a national work")
    parser = AknHTML()
    parser.feed(payload["html"])
    parser.close()
    by_id = {node.get("id"): node for node in parser.root.iter() if node.get("id")}
    version_date = date.fromisoformat(expression["date"])
    publication = work.get("publication_date")
    published_on = date.fromisoformat(publication) if publication else None
    parts = frbr.split("/")
    year, number = parts[-2:]
    statute = len(parts) == 6
    rows = []
    seen = set()
    for entry in toc_sections(payload["toc"]):
        anchor = entry.get("id") or entry.get("subcomponent")
        if not isinstance(anchor, str) or not anchor or anchor in seen or anchor not in by_id:
            raise ValueError("section anchor is missing or repeated")
        seen.add(anchor)
        element = by_id[anchor]
        num = (_direct(element, "num") or str(entry.get("num") or "")).strip().rstrip(".")
        if not num:
            raise ValueError("section has no number")
        section = _section_node(element)
        # The document citation already contains the section number.
        section.path = ""
        section.heading = section.heading or str(entry.get("heading") or "")
        if not section.text and not section.children:
            raise ValueError("section has no text")
        def name_work(node):
            # Each indexed subsection needs its actual instrument title as
            # context, not just "(1)" or a heading detached from its parent Act.
            node.heading = " — ".join(part for part in (work["title"], node.heading) if part)
            for child in node.children:
                name_work(child)
        name_work(section)
        citation = f"Act {number} of {year}, s {num}" if statute else f"{work['title']}, {num}"
        document = build_document(
            source, citation=citation, title=section.heading or work["title"],
            level="national", kind="statute" if statute else "regulation", body="Laws.Africa",
            sections=[section], url=representation(expression["url"], ".html") + "#" + quote(anchor, safe="_-"),
            status="repealed" if work.get("repealed") else "in_force",
            doc_date=published_on, effective_from=version_date,
        )
        # FRBR + language + anchor is stable and avoids collisions between
        # different regulations and language expressions with the same numbers.
        if source.source_id not in {row[0] for row in ACTS} or expression["language"] != "eng":
            document.id = document_id_for(source.source_id, f"{frbr}/{expression['language']}#{anchor}")
        # An unchanged section in a new consolidation still has a new source
        # edition/date. Do not let text-only deduplication discard that metadata.
        document.content_hash = content_hash(json.dumps([document.text, expression], sort_keys=True))
        rows.append(document)
    if not rows:
        raise ValueError("no numbered sections in expression")
    return rows
