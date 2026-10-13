"""GovInfo HTML for the United States Code and the federal rules appendices."""

from __future__ import annotations

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.htmlparse import last_effective, parse_govinfo_html
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

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


async def _fetch_all(http_get, urls: tuple[str, ...]) -> list[bytes]:
    blobs = []
    for url in urls:
        blobs.append(await http_get(url, {"Accept": "text/html", "User-Agent": "TervoryZenasPool/0.1"}))
    return blobs


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

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
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

    async def fetch(self, http_get) -> list[bytes]:
        return await _fetch_all(http_get, self.wave)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
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


class FreAdapter(_RulesAdapter):
    source_id = "fre"
    citation_prefix = "Fed. R. Evid."
    wave = FRE_WAVE
