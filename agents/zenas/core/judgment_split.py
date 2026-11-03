"""Conservative, code-only training splits from a court's own headed judgment."""

from __future__ import annotations

import re
from typing import Any

from agents.zenas.core.scope_rules import ruled

_HEAD = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?\s+)?([A-Za-z][A-Za-z &/\-]{1,80})\s*:?[ \t]*$", re.I)
_GROUPS = {
    "facts": frozenset({"facts", "background", "factual background", "common cause facts"}),
    "issues": frozenset({"issue", "issues", "issues for determination", "questions for determination"}),
    "reasoning": frozenset({"analysis", "reasoning", "discussion", "evaluation", "applicable law"}),
    "order": frozenset({"order", "orders", "the order", "conclusion and order", "disposition"}),
}
_OUTCOMES = {
    "upheld": re.compile(r"\b(?:appeal|application|claim|review)\s+(?:is\s+)?upheld\b", re.I),
    "dismissed": re.compile(r"\b(?:appeal|application|claim|review)\s+(?:is\s+)?dismissed\b", re.I),
    "set aside": re.compile(r"\bset\s+aside\b", re.I),
    "granted": re.compile(r"\b(?:application|relief|leave|order)\s+(?:is\s+)?granted\b", re.I),
    "refused": re.compile(r"\b(?:application|relief|leave|order)\s+(?:is\s+)?refused\b", re.I),
    "settled": re.compile(r"\b(?:settled|settlement agreement|by agreement|consent order)\b", re.I),
}
_NO_COSTS = re.compile(
    r"\b(?:no order (?:is made )?as to costs|each party (?:must|shall|is to) pay (?:its|their) own costs|without costs)\b",
    re.I,
)


def _heading(line: str) -> str | None:
    match = _HEAD.fullmatch(line)
    if not match:
        return None
    value = " ".join(match.group(1).casefold().split())
    return next((group for group, names in _GROUPS.items() if value in names), None)


def outcome_label(order: str) -> dict[str, Any] | None:
    """Map an operative order only when it names exactly one supported result."""
    found = [label for label, pattern in _OUTCOMES.items() if pattern.search(order or "")]
    if len(found) != 1:
        return None
    costs = "without costs" if _NO_COSTS.search(order) or "cost" not in order.casefold() else "with costs"
    return {"outcome": found[0], "costs": costs}


def split_judgment(title: str, text: str) -> dict[str, Any]:
    """Return verbatim headed sections, or an explicit exclusion without guessing."""
    body = (text or "").strip()
    if not body:
        return {"status": "excluded", "reason": "empty"}
    if ruled(f"{title}\n{body}") == "criminal":
        return {"status": "excluded", "reason": "criminal"}
    sections: dict[str, list[str]] = {name: [] for name in _GROUPS}
    current: str | None = None
    for line in body.splitlines():
        group = _heading(line)
        if group:
            current = group
            continue
        if current is not None:
            sections[current].append(line)
    cleaned = {name: "\n".join(lines).strip() for name, lines in sections.items()}
    if any(not cleaned[name] for name in ("facts", "issues", "reasoning", "order")):
        return {"status": "excluded", "reason": "headings"}
    label = outcome_label(cleaned["order"])
    if label is None:
        return {"status": "excluded", "reason": "order"}
    return {
        "status": "split",
        "input": {"facts": cleaned["facts"], "issues": cleaned["issues"]},
        "output": {"reasoning": cleaned["reasoning"], "order": cleaned["order"], **label},
    }
