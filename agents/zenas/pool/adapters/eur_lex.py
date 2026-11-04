"""Official EU trade legislation, using the existing EUR-Lex metadata fetch."""
from __future__ import annotations

import json
import re
from datetime import date
from html.parser import HTMLParser

from agents.zenas.pool.adapters.cases import html_text
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.http import USER_AGENT
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import Section, SourceSpec

_CELEX = re.compile(r"3[0-9]{4}[RLD][0-9]{4}")
_SPECIAL = re.compile(r"international (?:financial reporting|accounting) standards|third.party copyright|all rights reserved|permission of the (?:copyright|rights) holder", re.I)
_TOPICS = ("Union Customs Code", "common rules for imports", "common rules for exports")


def source_url(celex: str) -> str:
    if not _CELEX.fullmatch(celex):
        raise ValueError("EU legislation CELEX required")
    return "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:" + celex


class _LegalHTML(HTMLParser):
    """Take only the legal document container, excluding site navigation/media."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []
        self.complete = False
        self.omit = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif values.get("id", "").lower() == "dochtml":
                self.depth = 1
        if not self.depth:
            return
        if tag in {"script", "style", "svg"}:
            self.omit += 1
        if not self.omit and tag in {"p", "div", "br", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg"} and self.omit:
            self.omit -= 1
        if tag == "div" and self.depth:
            self.depth -= 1
            if not self.depth:
                self.complete = True
        if self.depth and not self.omit and tag in {"p", "tr"}:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self.depth and not self.omit:
            self.parts.append(data)


def legal_text(blob: bytes) -> str:
    page = blob.decode("utf-8", "strict")
    if _SPECIAL.search(page):
        raise FetchNotConfigured("EUR-Lex special reuse conditions require source review")
    parser = _LegalHTML()
    parser.feed(page)
    if not parser.complete:
        raise FetchNotConfigured("EUR-Lex legal text unavailable")
    # Reuse the existing pool's paragraph-preserving plain-text normalization.
    from html import escape
    text = html_text("<p>" + "</p><p>".join(escape(p) for p in "".join(parser.parts).split("\n\n")) + "</p>")
    if len(text) < 100:
        raise FetchNotConfigured("EUR-Lex legal text unavailable")
    return text


class EurLexAdapter:
    source_id = "eur-lex"

    async def fetch(self, http_get) -> list[bytes]:
        # Local imports avoid the catalogue/adapters/regulation_sources import cycle.
        from agents.zenas.core.regulation_sources import Sources
        from agents.zenas.core.regulations import civil

        sources = Sources("us-east", http_get=http_get)
        found = {}
        for topic in _TOPICS:
            for row in await sources.eur_lex({"topic": topic}):
                if civil(row["title"]):
                    found[row["id"]] = row
        blobs = []
        for row in found.values():
            url = source_url(row["id"])
            raw = await http_get(url, {"Accept": "text/html", "User-Agent": USER_AGENT})
            text = legal_text(raw)
            blobs.append(json.dumps({**row, "link": url, "text": text}, ensure_ascii=False).encode())
        return blobs

    def parse(self, blob: bytes, source: SourceSpec) -> list:
        from agents.zenas.core.regulations import civil

        rows = json.loads(blob)
        if not isinstance(rows, list):
            rows = [rows]
        documents = []
        for row in rows:
            celex = row["id"]
            url = source_url(celex)
            if row.get("source") != self.source_id or row.get("link") != url:
                raise ValueError("EUR-Lex source mismatch")
            title, text = row["title"].strip(), row["text"].strip()
            if not title or len(text) < 100 or text == title:
                raise ValueError("EUR-Lex full legal text required")
            if _SPECIAL.search(text) or not civil(title):
                continue
            # Trade acts can also contain criminal provisions. Keep only civil
            # articles, with original article headings and source attribution.
            starts = list(re.finditer(r"(?m)^Article[ \t]+(\d+[a-z]?)\s*$", text))
            sections = []
            for index, match in enumerate(starts):
                end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
                article = text[match.end():end].strip()
                if article and civil(article):
                    sections.append(Section("Article " + match[1], "Article " + match[1], article))
            if not sections:
                continue
            documents.append(build_document(
                source, citation="CELEX:" + celex, title=title, level="international",
                kind="regulation" if celex[5] == "R" else "statute", body="European Union",
                sections=sections, url=url, status="published",
                doc_date=date.fromisoformat(row["date"]), effective_from=None,
            ))
        return documents
