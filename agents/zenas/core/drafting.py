"""Assemble a contract from approved clauses. This module knows no jurisdiction.

The caller passes the template id, the clause rows, and the term sheet.
A sentence that is not an approved clause, filled in, is marked for review.
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")

# Slot order only. The words of each clause come from the library the caller passes.
TEMPLATES: dict[str, tuple[str, ...]] = {
    "nda": ("parties", "confidentiality", "term", "return"),
    "supply": ("parties", "goods", "price", "delivery"),
    "distribution": ("parties", "territory", "term", "termination"),
    "sale_of_goods": ("parties", "goods", "price", "delivery", "risk"),
    "sale_of_goods_cisg": ("parties", "goods", "price", "cisg", "delivery"),
    "freight": ("parties", "carriage", "freight", "delivery"),
    "services": ("parties", "services", "fees", "term"),
    "cross_border_agency": ("parties", "authority", "territory", "commission"),
}


def template_slots(template: str) -> tuple[str, ...] | None:
    return TEMPLATES.get(template)


def _terms(raw: dict[str, Any] | None) -> dict[str, str]:
    return {str(key): str(value) for key, value in (raw or {}).items()}


def fill_clause(text: str, terms: dict[str, str]) -> tuple[str, list[str]]:
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in terms or terms[key] == "":
            missing.append(key)
            return match.group(0)
        return terms[key]

    return _PLACEHOLDER.sub(replace, text or ""), missing


def _same_keys(keys: list[str], prior: dict[str, str], terms: dict[str, str]) -> bool:
    return all(prior.get(key) == terms.get(key) for key in keys)


def select_prior(rows: list[dict[str, Any]], template: str, terms: dict[str, Any] | None) -> dict[str, Any] | None:
    """The closest released worked matter for this template, including one whose facts differ."""
    facts = _terms(terms)
    best: dict[str, Any] | None = None
    best_score = -1.0
    for row in rows:
        if str(row.get("source") or "") != "released":
            continue
        pattern = row.get("fact_pattern") or {}
        if not isinstance(pattern, dict) or str(pattern.get("template") or "") != template:
            continue
        features = _terms(pattern.get("features"))
        keys = set(features) | set(facts)
        if not keys:
            score = 1.0
        else:
            score = sum(1 for key in keys if features.get(key) == facts.get(key)) / len(keys)
        if score > best_score:
            best_score = score
            best = row
    return best


def assemble_draft(
    *,
    template: str,
    clauses: list[dict[str, Any]],
    terms: dict[str, Any] | None,
    prior: dict[str, Any] | None = None,
    render_clause: Any = None,
) -> dict[str, Any]:
    """Code assembly. model_calls stays 0. The caller decides whether a model may revise a difference."""
    slots = template_slots(template)
    if slots is None:
        return {"ok": False, "reason": "no such template"}
    renderer = render_clause or fill_clause
    facts = _terms(terms)
    pattern: dict[str, Any] = {}
    if isinstance(prior, dict):
        nested = prior.get("fact_pattern")
        pattern = nested if isinstance(nested, dict) else prior
    prior_features = _terms(pattern.get("features"))
    prior_sentences = {
        str(item.get("slot") or ""): item
        for item in (pattern.get("sentences") or [])
        if isinstance(item, dict)
    }
    full = bool(prior) and prior_features == facts and str(pattern.get("template") or "") == template
    if full and pattern.get("text"):
        sentences = [dict(item) for item in prior_sentences.values()] or [
            {"slot": "document", "text": str(pattern.get("text") or ""), "library": False, "review": True, "keys": []}
        ]
        # Keep template order when we have one sentence per slot.
        ordered = []
        for slot in slots:
            if slot in prior_sentences:
                ordered.append(dict(prior_sentences[slot]))
        if len(ordered) == len(slots):
            sentences = ordered
        text = str(pattern.get("text") or "")
        return {
            "ok": True,
            "reused": True,
            "text": text,
            "sentences": sentences,
            "library": [item["slot"] for item in sentences if item.get("library") and not item.get("review")],
            "review": [item["slot"] for item in sentences if item.get("review")],
            "differing": [],
        }
    by_kind: dict[str, dict[str, Any]] = {}
    for row in clauses:
        if str(row.get("template") or "") != template:
            continue
        by_kind[str(row.get("kind") or "")] = row
    sentences: list[dict[str, Any]] = []
    differing: list[str] = []
    for slot in slots:
        clause = by_kind.get(slot)
        previous = prior_sentences.get(slot)
        if clause is None:
            if previous and previous.get("library") and _same_keys(list(previous.get("keys") or []), prior_features, facts):
                sentences.append(dict(previous))
                continue
            sentences.append(
                {
                    "slot": slot,
                    "text": f"[Review: no approved clause for {slot}.]",
                    "library": False,
                    "review": True,
                    "keys": [],
                    "clause_id": "",
                }
            )
            continue
        filled, missing = renderer(str(clause.get("text") or ""), facts)
        keys = _PLACEHOLDER.findall(str(clause.get("text") or ""))
        if previous and _same_keys(list(previous.get("keys") or keys), prior_features, facts):
            sentences.append(dict(previous))
            continue
        review = bool(missing)
        if previous and not previous.get("library") and not _same_keys(keys, prior_features, facts):
            differing.append(slot)
            review = True
        sentences.append(
            {
                "slot": slot,
                "text": filled,
                "library": not review,
                "review": review,
                "keys": keys,
                "clause_id": str(clause.get("id") or ""),
            }
        )
    text = "\n\n".join(str(item["text"]) for item in sentences)
    return {
        "ok": True,
        "reused": False,
        "text": text,
        "sentences": sentences,
        "library": [item["slot"] for item in sentences if item.get("library") and not item.get("review")],
        "review": [item["slot"] for item in sentences if item.get("review")],
        "differing": differing,
    }
