"""Citation strings for the graph.

eyecite (Free Law Project, BSD) extracts reporters it knows. A few forms it
does not ship — federal rules, the Delaware Code, HTSUS — are added here so
those pool documents still link. South African forms are added beside them.
"""

from __future__ import annotations

import re

_KEEP = {
    "FullLawCitation",
    "FullCaseCitation",
    "ShortCaseCitation",
    "FullJournalCitation",
    "SupraCitation",
    "IdCitation",
}

# Forms eyecite 2.7 does not return. Information for the graph, not a licence call.
_EXTRA = (
    re.compile(r"\bFed\. R\. Civ\. P\. \d+(?:\.\d+)?(?:\([0-9A-Za-z]+\))*", re.I),
    re.compile(r"\bFed\. R\. Evid\. \d+(?:\.\d+)?(?:\([0-9A-Za-z]+\))*", re.I),
    re.compile(r"\bFed\. R\. App\. P\. \d+(?:\.\d+)?(?:\([0-9A-Za-z]+\))*", re.I),
    re.compile(r"\b\d+\s+Del\. C\.\s+§\s*[0-9A-Za-z.\-]+", re.I),
    re.compile(r"\bHTSUS\s+[0-9]+(?:\.[0-9]+)*", re.I),
    re.compile(r"\[\d{4}\]\s+ZA[A-Z]{2,6}\s+\d+", re.I),
    re.compile(r"\b\d{4}\s+\(\d+\)\s+SA\s+\d+\s+\([A-Za-z]{2,6}\)", re.I),
)


def extract_citations(text: str) -> list[str]:
    """Unique citation strings, eyecite first, then the forms it skips."""
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        matched = " ".join(raw.split())
        if len(matched) < 4:
            return
        key = matched.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(matched)

    from eyecite import get_citations

    for cite in get_citations(text or ""):
        if type(cite).__name__ not in _KEEP:
            continue
        add(cite.matched_text())
    for pattern in _EXTRA:
        for match in pattern.finditer(text or ""):
            add(match.group(0))
    return found
