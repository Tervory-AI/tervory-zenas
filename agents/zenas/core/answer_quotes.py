"""Use the stored court text for quotations; no model or invented references."""

from __future__ import annotations

import re
from typing import Any

from agents.zenas.core.precedent import _NEUTRAL
from agents.zenas.core.selftest import _verbatim_quote


def neutral_citations(text: str) -> list[str]:
    return list(dict.fromkeys(" ".join(match[0].split()) for match in _NEUTRAL.finditer(text)))


def named_quotation(question: str, passages: list[dict[str, Any]], citations: list[str],
                    checked: dict[str, Any]) -> dict[str, str] | None:
    named = {citation.casefold() for citation in neutral_citations(question)}
    for passage in passages:
        citation = str(passage.get("citation") or "")
        title = str(passage.get("title") or passage.get("name") or "")
        if citation not in citations or not (
            citation.casefold() in named or citation.casefold() in question.casefold()
            or (title and title.casefold() in question.casefold())
        ):
            continue
        if not any(row.get("resolved") and row.get("quote_match") and row.get("doc_id") == str(passage.get("doc_id") or "")
                   and row.get("version_id", "") == str(passage.get("version_id") or "")
                   for row in checked.get("results", [])):
            continue
        text = str(passage.get("text") or "")
        quote = _verbatim_quote(text, limit=len(text))
        if not quote:
            continue
        folded = " ".join(text.split())
        before = folded[:folded.index(quote)]
        marks = re.findall(r"(?:^|\s)(\[\s*\d+\s*\]|\d{1,3}\.)\s+", before + " ")
        reference = marks[-1] if marks else str(passage.get("path") or "")
        return {"citation": citation, "quote": quote, "reference": reference}
    return None
