"""Enacted state statutes. Official comments and the ALI text are not parsed here."""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from urllib.parse import urljoin

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.htmlparse import parse_delaware_html
from agents.zenas.pool.http import USER_AGENT
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

DELAWARE_WAVE = ("https://delcode.delaware.gov/title8/c001/sc01/index.html",)
DELAWARE_BULK = (
    "https://delcode.delaware.gov/title8/index.html",
    "https://delcode.delaware.gov/title6/index.html",
)


_DE_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)


def _de_headers() -> dict[str, str]:
    return {"Accept": "text/html", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}


def _stamp_title(blob: bytes, url: str) -> bytes:
    if "/title6/" in url:
        return b"<!-- title 6 -->\n" + blob
    if "/title8/" in url:
        return b"<!-- title 8 -->\n" + blob
    return blob


def _delaware_title(blob: bytes) -> str:
    head = blob[:2000].lower()
    if b"title 6" in head or b"/title6/" in head:
        return "6"
    return "8"


class DelawareAdapter:
    source_id = "delaware-code"

    async def fetch(self, http_get) -> list[bytes]:
        blobs = []
        for url in DELAWARE_WAVE:
            blobs.append(await http_get(url, _de_headers()))
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        """Title indexes have no sections. Follow chapter, then subchapter, pages."""
        del resume
        cap = int(os.environ.get("DELAWARE_MAX_PAGES", "24") or "24")
        headers = _de_headers()
        queue = list(DELAWARE_BULK)
        seen: set[str] = set()
        blobs: list[bytes] = []
        while queue and len(seen) < cap:
            url = queue.pop(0)
            if url in seen or not url.startswith("https://delcode.delaware.gov/"):
                continue
            seen.add(url)
            blob = await http_get(url, headers)
            stamped = _stamp_title(blob, url)
            if b"SectionHead" in blob or b"sectionhead" in blob.lower():
                blobs.append(stamped)
                continue
            text = blob.decode("utf-8", errors="replace")
            for match in _DE_HREF.finditer(text):
                href = match.group(1).split("#", 1)[0].strip()
                if "index.html" not in href:
                    continue
                nxt = urljoin(url, href)
                if nxt not in seen:
                    queue.append(nxt)
        return blobs, ""

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        documents = []
        title_no = _delaware_title(blob)
        for number, heading, sections in parse_delaware_html(blob):
            if not number or not sections:
                continue
            citation = f"{title_no} Del. C. § {number}"
            documents.append(
                build_document(
                    source,
                    citation=citation,
                    title=heading or citation,
                    level="state",
                    kind="statute",
                    body="Delaware General Assembly",
                    sections=sections,
                    url=DELAWARE_WAVE[0],
                    status="in_force",
                    doc_date=None,
                    effective_from=None,
                )
            )
        return documents


class _JsonStateAdapter:
    """Normalized enacted-text JSON. No snapshot is shipped for NY or Texas."""

    source_id = ""
    citation_template = ""
    body_name = ""
    reason = ""

    async def fetch(self, http_get) -> list[bytes]:
        raise FetchNotConfigured(self.reason)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        payload = json.loads(blob.decode("utf-8"))
        rows = payload if isinstance(payload, list) else [payload.get("result", payload)]
        documents = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = (row.get("text") or "").strip()
            location = str(row.get("locationId") or row.get("citation") or "").strip()
            title = (row.get("title") or "").strip()
            if not text or not location:
                continue
            citation = location if location[:1].isdigit() is False and "§" in location else self.citation_template.format(location=location)
            if citation == location and "§" not in citation and self.citation_template:
                citation = self.citation_template.format(location=location)
            paragraphs = row.get("paragraphs") or []
            if paragraphs:
                sections = [
                    Section(item.get("path") or "", item.get("heading") or "", item.get("text") or "", [])
                    for item in paragraphs
                    if (item.get("text") or "").strip()
                ]
            else:
                sections = [Section("", title, text, [])]
            documents.append(
                build_document(
                    source,
                    citation=citation,
                    title=title or citation,
                    level="state",
                    kind="statute",
                    body=self.body_name,
                    sections=sections,
                    url=row.get("url") or source.canonical_url,
                    status="in_force",
                    doc_date=None,
                    effective_from=None,
                    text=text,
                )
            )
        return documents


class NewYorkAdapter(_JsonStateAdapter):
    source_id = "ny-laws"
    citation_template = "N.Y. U.C.C. Law § {location}"
    body_name = "New York Legislature"
    reason = "NY_OPENLEG_API_KEY is not set"

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        del http_get, resume
        token = os.environ.get("NY_OPENLEG_API_KEY", "").strip()
        if not token:
            raise FetchNotConfigured("NY_OPENLEG_API_KEY is not set")
        # The Open Legislation API takes the key in the query string.
        # This loader will not put a key in a URL. Use a snapshot.
        raise FetchNotConfigured("NY_OPENLEG_API_KEY is set but this loader will not place it in a URL")


_TEXAS_ZIPS = (
    (
        "https://tcss.legis.texas.gov/resources/Zips/BC.htm.zip",
        "Tex. Bus. & Com. Code",
    ),
    (
        "https://tcss.legis.texas.gov/resources/Zips/BO.htm.zip",
        "Tex. Bus. Orgs. Code",
    ),
)
_TEXAS_SEC = re.compile(r"Sec\.\s+(\d+\.\d+[0-9A-Za-z]*)\.\s+([^<]{1,180})")
_TAG = re.compile(r"<[^>]+>")


def _texas_headers() -> dict[str, str]:
    return {"Accept": "application/zip", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}


def _parse_texas_html(blob: bytes, source: SourceSpec) -> list[LegalDocument]:
    text = blob.decode("utf-8", errors="replace")
    prefix = "Tex. Bus. & Com. Code"
    marker = re.search(r"texas-cite:([^<\n]+)", text)
    if marker:
        prefix = marker.group(1).strip()
    elif "BUSINESS ORGANIZATIONS CODE" in text[:800].upper():
        prefix = "Tex. Bus. Orgs. Code"
    matches = list(_TEXAS_SEC.finditer(text))
    documents: list[LegalDocument] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        plain = _TAG.sub(" ", text[match.start() : end])
        plain = " ".join(plain.split())
        if len(plain) < 24:
            continue
        number = match.group(1)
        heading = " ".join(match.group(2).split()).strip(" .")
        citation = f"{prefix} § {number}"
        documents.append(
            build_document(
                source,
                citation=citation,
                title=heading or citation,
                level="state",
                kind="statute",
                body="Texas Legislature",
                sections=[Section(number, heading, plain, [])],
                url="https://tcss.legis.texas.gov/",
                status="in_force",
                doc_date=None,
                effective_from=None,
                text=plain,
            )
        )
    return documents


class TexasAdapter(_JsonStateAdapter):
    source_id = "texas-statutes"
    citation_template = "{location}"
    body_name = "Texas Legislature"
    reason = (
        "statutes.capitol.texas.gov returned a script shell without the enacted text. "
        "The enacted chapters are in the legislature's HTML zip."
    )

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get)
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        del resume
        headers = _texas_headers()
        blobs: list[bytes] = []
        for url, prefix in _TEXAS_ZIPS:
            raw = await http_get(url, headers)
            try:
                archive = zipfile.ZipFile(io.BytesIO(raw))
            except zipfile.BadZipFile:
                continue
            stamp = f"<!-- texas-cite:{prefix} -->\n".encode()
            for name in archive.namelist():
                if name.lower().endswith((".htm", ".html")):
                    blobs.append(stamp + archive.read(name))
        return blobs, ""

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        sample = blob.lstrip()[:1]
        if sample in (b"{", b"["):
            return _JsonStateAdapter.parse(self, blob, source)
        return _parse_texas_html(blob, source)
