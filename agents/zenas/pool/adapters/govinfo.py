"""GovInfo HTML for the United States Code and the federal rules appendices.

The checked-in wave stays on ``fetch``. ``fetch_bulk`` is the full-title path:
public USLM from uscode.house.gov, or api.govinfo.gov when GOVINFO_API_KEY is set.
The key is a header, never part of the URL.
"""

from __future__ import annotations

import os
import re

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.htmlparse import last_effective, parse_govinfo_html
from agents.zenas.pool.http import USER_AGENT
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec
from agents.zenas.pool.uslm import is_uslm, parse_uslm

# Titles for the launch areas. 49 is subtitle IV in the title download.
USCODE_TITLES = (9, 11, 15, 17, 19, 35, 46, 49, 50)

USCODE_WAVE = (
    "https://www.govinfo.gov/content/pkg/USCODE-2023-title49/html/"
    "USCODE-2023-title49-subtitleIV-partB-chap147-sec14706.htm",
)
FRCP_WAVE = (
    "https://www.govinfo.gov/content/pkg/USCODE-2023-title28/html/"
    "USCODE-2023-title28-app-federalru-dup1-rule1.htm",
)
FRE_WAVE = (
    "https://www.govinfo.gov/content/pkg/USCODE-2023-title28/html/"
    "USCODE-2023-title28-app-federalru-dup2-rule401.htm",
)
FRAP_WAVE = (
    "https://www.govinfo.gov/content/pkg/USCODE-2023-title28/html/"
    "USCODE-2023-title28-app-federalru-dup3-rule1.htm",
)
# Whole appendices. FRAP's dup3 file redirects to an error page; the title 28
# appendix holds FRAP, then FRCP. FRAP is the text before FRCP Rule 1.
FRCP_BULK = (
    "https://www.govinfo.gov/content/pkg/USCODE-2023-title28/html/"
    "USCODE-2023-title28-app-federalru-dup1.htm"
)
FRE_BULK = (
    "https://www.govinfo.gov/content/pkg/USCODE-2023-title28/html/"
    "USCODE-2023-title28-app-federalru-dup2.htm"
)
FRAP_BULK = (
    "https://www.govinfo.gov/content/pkg/USCODE-2023-title28/html/USCODE-2023-title28-app.htm"
)
_FRCP_RULE1 = b"Rule 1. Scope and Purpose"
_H3 = re.compile(br'(?i)<h3[^>]*class="[^"]*section-head[^"]*"')


def _headers() -> dict[str, str]:
    return {"Accept": "text/html", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}


_HEADERS = _headers()


def before_frcp(blob: bytes) -> bytes:
    """FRAP lives in the title 28 appendix before FRCP Rule 1's section heading."""
    start = 0
    needle = _FRCP_RULE1
    while True:
        index = blob.find(needle, start)
        if index < 0:
            return blob
        window = blob[max(0, index - 180) : index]
        if b"section-head" in window:
            return blob[:index]
        start = index + len(needle)


def split_rule_html(blob: bytes) -> list[bytes]:
    """One chunk per rule heading. A single-rule file stays one chunk.

    The page head (edition comment, title) is repeated on each chunk so the
    existing parser still sees the rule number and the effective date.
    """
    matches = list(_H3.finditer(blob))
    if len(matches) <= 1:
        return [blob]
    head = blob[: matches[0].start()]
    chunks: list[bytes] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        chunks.append(head + blob[match.start() : end])
    return chunks


async def _fetch_all(http_get, urls: tuple[str, ...]) -> list[bytes]:
    blobs = []
    for url in urls:
        _reject_credential_url(url)
        blobs.append(await http_get(url, dict(_HEADERS)))
    return blobs


def _reject_credential_url(url: str) -> None:
    lowered = url.lower()
    if any(mark in lowered for mark in ("api_key=", "token=", "?key=", "&key=")):
        raise FetchNotConfigured("refusing a URL that contains a credential")
    for name in ("GOVINFO_API_KEY", "COURTLISTENER_API_TOKEN", "NY_OPENLEG_API_KEY", "LAWS_AFRICA_API_TOKEN"):
        value = os.environ.get(name, "").strip()
        if len(value) >= 8 and value in url:
            raise FetchNotConfigured("refusing a URL that contains a credential")


def uscode_bulk_url(title: int) -> str:
    """Public USLM zip. No key. ``USCODE_RELEASE`` is ``congress-law``, default 118-234."""
    release = os.environ.get("USCODE_RELEASE", "118-234").strip() or "118-234"
    congress, _, law = release.partition("-")
    return (
        "https://uscode.house.gov/download/releasepoints/us/pl/"
        f"{congress}/{law}/xml_usc{title:02d}@{release}.zip"
    )


def govinfo_uscode_url(title: int) -> str:
    edition = os.environ.get("USCODE_EDITION", "2023").strip() or "2023"
    return f"https://api.govinfo.gov/packages/USCODE-{edition}-title{title}/zip"


def _govinfo_headers() -> dict[str, str]:
    headers = {"Accept": "application/zip", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}
    key = os.environ.get("GOVINFO_API_KEY", "").strip()
    if key:
        headers["X-Api-Key"] = key
    return headers


def _prefix(nodes: list[Section], prefix: str) -> None:
    for node in nodes:
        node.path = f"{prefix} {node.path}".strip() if node.path else prefix
        _prefix(node.children, node.path)


def _with_notes(sections: list[Section], notes: list[Section]) -> list[Section]:
    if not notes:
        return sections
    _prefix(notes, "note")
    return [*sections, Section("note", "Notes", "", notes)]


class UsCodeAdapter:
    source_id = "govinfo-uscode"

    async def fetch(self, http_get) -> list[bytes]:
        return await _fetch_all(http_get, USCODE_WAVE)

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        """Whole titles. Uses the GovInfo API when the key is set, else house.gov.

        ``resume`` is a title number already finished. The returned cursor is empty
        because one call walks every remaining title.
        """
        del resume
        key = os.environ.get("GOVINFO_API_KEY", "").strip()
        headers = _govinfo_headers()
        blobs: list[bytes] = []
        for title in USCODE_TITLES:
            url = govinfo_uscode_url(title) if key else uscode_bulk_url(title)
            _reject_credential_url(url)
            blobs.append(await http_get(url, headers))
        return blobs, ""

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        if is_uslm(blob):
            return parse_uslm(blob, source)
        parsed = parse_govinfo_html(blob)
        if not parsed.number or not parsed.usc_title:
            return []
        citation = f"{parsed.usc_title} U.S.C. § {parsed.number}"
        title = parsed.heading or citation
        sections = parsed.sections
        return [
            build_document(
                source,
                citation=citation,
                title=title,
                level="federal",
                kind="statute",
                body="Congress",
                sections=sections,
                url=USCODE_WAVE[0] if parsed.number == "14706" else "https://www.govinfo.gov/",
                status="in_force",
                doc_date=parsed.current_through,
                effective_from=parsed.current_through,
            )
        ]


class _RulesAdapter:
    source_id = ""
    citation_prefix = ""
    wave: tuple[str, ...] = ()
    bulk_url = ""
    cut_frcp = False

    async def fetch(self, http_get) -> list[bytes]:
        return await _fetch_all(http_get, self.wave)

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        del resume
        if not self.bulk_url:
            return await self.fetch(http_get), ""
        _reject_credential_url(self.bulk_url)
        blob = await http_get(self.bulk_url, _headers())
        if self.cut_frcp:
            blob = before_frcp(blob)
        return [blob], ""

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        documents: list[LegalDocument] = []
        for chunk in split_rule_html(blob):
            documents.extend(self._one(chunk, source))
        return documents

    def _one(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        parsed = parse_govinfo_html(blob)
        if not parsed.number:
            return []
        citation = f"{self.citation_prefix} {parsed.number}"
        effective = last_effective(parsed.credit) or parsed.current_through
        sections = _with_notes(parsed.sections, parsed.notes)
        # A rule with no subdivisions is one section, the rule text.
        if not sections and parsed.heading:
            sections = [Section("", parsed.heading, parsed.heading, [])]
        return [
            build_document(
                source,
                citation=citation,
                title=parsed.heading or citation,
                level="federal",
                kind="court_rule",
                body="Supreme Court of the United States",
                sections=sections,
                url=self.wave[0],
                status="in_force",
                doc_date=effective,
                effective_from=effective,
            )
        ]


class FrcpAdapter(_RulesAdapter):
    source_id = "frcp"
    citation_prefix = "Fed. R. Civ. P."
    wave = FRCP_WAVE
    bulk_url = FRCP_BULK


class FreAdapter(_RulesAdapter):
    source_id = "fre"
    citation_prefix = "Fed. R. Evid."
    wave = FRE_WAVE
    bulk_url = FRE_BULK


class FrapAdapter(_RulesAdapter):
    source_id = "frap"
    citation_prefix = "Fed. R. App. P."
    wave = FRAP_WAVE
    bulk_url = FRAP_BULK
    cut_frcp = True
