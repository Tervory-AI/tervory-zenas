"""Answer a shopper from catalogue fields only.

A word counts when it is a whole token on a row, not a piece of a longer word.
The model may only name rows. The sentence is written here from one row's
fields, or from one approved fit. Anything else is "I don't know".
"""

from __future__ import annotations

import json
import re
from typing import Any

from rigbox.ground import _object
from rigbox.logic import approved

_UNKNOWN = "I don't know"
_TOKEN = re.compile(r"[a-z0-9]+")
_FIT_INTENT = re.compile(r"\b(fits?|fitting|compatible|pairs?|paired)\b", re.IGNORECASE)
# Question words. None of these is a stored fact.
_GLUE = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "what",
        "which",
        "when",
        "where",
        "will",
        "does",
        "have",
        "from",
        "your",
        "about",
        "into",
        "them",
        "they",
        "also",
        "please",
        "would",
        "could",
        "should",
        "there",
        "their",
        "only",
        "just",
        "how",
        "much",
        "many",
        "can",
        "you",
        "are",
        "was",
        "were",
        "has",
        "had",
        "did",
        "not",
        "but",
        "who",
        "why",
        "tell",
        "any",
        "some",
        "over",
        "under",
        "than",
        "then",
        "these",
        "those",
        "come",
        "comes",
        "take",
        "takes",
        "very",
        "buy",
        "get",
        "got",
        "make",
        "want",
        "need",
        "show",
        "find",
        "list",
        "listed",
        "looking",
        "catalogue",
        "catalog",
        "item",
        "items",
        "product",
        "products",
        "one",
        "all",
        "each",
        "our",
        "off",
        "too",
        "hello",
        "thanks",
        "thank",
        "is",
        "a",
        "an",
        "of",
        "in",
        "on",
        "to",
        "it",
        "be",
        "or",
        "if",
        "my",
        "me",
        "we",
        "do",
        "so",
        "at",
        "by",
        "up",
        "no",
        "yes",
        "am",
        "its",
        "out",
        "per",
        "via",
        "onto",
        "upon",
        "within",
        "without",
        "between",
        "across",
        "after",
        "before",
        "again",
        "here",
        "now",
        "today",
        "tomorrow",
        "price",
        "prices",
        "cost",
        "costs",
        "priced",
        "fit",
        "fits",
        "fitting",
        "compatible",
        "pair",
        "pairs",
        "paired",
    }
)


def answerable(question: str, products: list[dict[str, Any]], fits: list[dict[str, Any]]) -> bool:
    """True when every subject word is a whole token on some catalogue row."""
    del fits
    tokens = _subject(question)
    if not tokens:
        return False
    rows = [_tokens_of(row) for row in products]
    return all(any(_in_tokens(token, have) for have in rows) for token in tokens)


def select_prompt(payload: dict[str, Any]) -> str:
    """Ask the model to name rows. It does not write the sentence."""
    catalogue = json.dumps(
        {"products": payload.get("products") or [], "fits": payload.get("fits") or []},
        default=str,
    )
    return (
        "Choose which catalogue rows the question is about. Do not write the answer. "
        'Reply with JSON: {"cites": [product id], "fields": [field name]}. '
        'If no row answers it, reply {"cites": [], "fields": []}. '
        f"Catalogue: {catalogue}\nQuestion: {payload.get('question')}"
    )


def grounded_answer(payload: dict[str, Any], model_text: str) -> dict[str, Any]:
    products = list(payload.get("products") or [])
    fits = [row for row in (payload.get("fits") or []) if isinstance(row, dict) and approved(row)]
    question = str(payload.get("question") or "")
    if not answerable(question, products, fits):
        return {"answer": _UNKNOWN, "cites": []}
    parsed = _object(model_text)
    by_id = _index(products)
    cites = _cited(parsed, by_id)
    if not cites:
        return {"answer": _UNKNOWN, "cites": []}
    built = _from_fit(question, cites, by_id, products, fits)
    if built is None:
        built = _from_one_row(question, cites, by_id)
    if built is None:
        return {"answer": _UNKNOWN, "cites": []}
    text, used = built
    return {"answer": text, "cites": used}


def _subject(text: str) -> list[str]:
    """Subject words. A single digit stays, because it can be the model (TLR-7, Type 3)."""
    found: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        if token in _GLUE:
            continue
        if len(token) >= 2 or token.isdigit():
            found.append(token)
    return found


def _same_word(left: str, right: str) -> bool:
    if left == right:
        return True
    # A category is often the plural of the word a shopper types.
    if len(left) >= 4 and left + "s" == right:
        return True
    if len(right) >= 4 and right + "s" == left:
        return True
    return False


def _in_tokens(needle: str, have: set[str]) -> bool:
    return any(_same_word(needle, token) for token in have)


def _covers(needles: list[str], have: set[str]) -> bool:
    return all(_in_tokens(needle, have) for needle in needles)


def _pieces(row: dict[str, Any]) -> list[str]:
    pieces = [
        str(row.get("name") or ""),
        str(row.get("brand") or ""),
        str(row.get("category") or ""),
        str(row.get("id") or ""),
        str(row.get("sku") or ""),
        str(row.get("family") or ""),
    ]
    price = row.get("price")
    if isinstance(price, dict):
        if price.get("amount") is not None:
            pieces.append(str(price.get("amount")))
        pieces.append(str(price.get("currency") or ""))
    elif price is not None:
        pieces.append(str(price))
        pieces.append(str(row.get("currency") or ""))
    attributes = row.get("attributes") or {}
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            pieces.append(str(key))
            pieces.append(str(value))
    return pieces


def _tokens_of(row: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for piece in _pieces(row):
        found.update(_TOKEN.findall(piece.lower()))
    return found


def _index(products: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for row in products:
        ident = str(row.get("id") or row.get("sku") or "")
        if ident:
            found[ident] = row
    return found


def _cited(parsed: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[str]:
    raw = parsed.get("cites")
    if not isinstance(raw, list):
        raw = parsed.get("rows") if isinstance(parsed.get("rows"), list) else []
    cites: list[str] = []
    for item in raw:
        ident = str(item)
        if ident in by_id and ident not in cites:
            cites.append(ident)
    return cites


def _id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("sku") or "")


def _describe(row: dict[str, Any]) -> str:
    bits: list[str] = []
    name = str(row.get("name") or "").strip()
    if name:
        bits.append(name)
    brand = str(row.get("brand") or "").strip()
    if brand:
        bits.append(brand)
    category = str(row.get("category") or "").strip()
    if category:
        bits.append(category)
    price = row.get("price")
    if isinstance(price, dict) and price.get("amount") is not None:
        amount = str(price.get("amount"))
        currency = str(price.get("currency") or "").strip()
        bits.append(f"{amount} {currency}".strip())
    elif price is not None and not isinstance(price, dict):
        bits.append(str(price))
    attributes = row.get("attributes") or {}
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            bits.append(f"{key} {value}".strip())
    family = str(row.get("family") or "").strip()
    if family:
        bits.append(family)
    return ". ".join(bit for bit in bits if bit)


def _from_one_row(
    question: str, cites: list[str], by_id: dict[str, dict[str, Any]]
) -> tuple[str, list[str]] | None:
    needles = _subject(question)
    if not needles:
        return None
    lines: list[str] = []
    used: list[str] = []
    for ident in cites:
        row = by_id[ident]
        if not _covers(needles, _tokens_of(row)):
            continue
        lines.append(_describe(row))
        used.append(ident)
    if not lines:
        return None
    return " ".join(lines), used


def _fit_intent(question: str) -> bool:
    return _FIT_INTENT.search(question) is not None


def _centres(fit: dict[str, Any], products: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    sku = str(fit.get("fits_sku") or "")
    if sku and sku in by_id:
        return [by_id[sku]]
    family = str(fit.get("fits_family") or "")
    if not family:
        return []
    return [row for row in products if str(row.get("family") or "") == family]


def _from_fit(
    question: str,
    cites: list[str],
    by_id: dict[str, dict[str, Any]],
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
) -> tuple[str, list[str]] | None:
    if not _fit_intent(question):
        return None
    needles = _subject(question)
    if not needles:
        return None
    cited = set(cites)
    lines: list[str] = []
    used: list[str] = []
    seen: set[tuple[str, str]] = set()
    for fit in fits:
        part = by_id.get(str(fit.get("part_sku") or ""))
        if part is None:
            continue
        part_id = _id(part)
        for centre in _centres(fit, products, by_id):
            centre_id = _id(centre)
            if not centre_id or centre_id == part_id:
                continue
            pair = (part_id, centre_id)
            if pair in seen:
                continue
            if part_id not in cited and centre_id not in cited:
                continue
            union = _tokens_of(part) | _tokens_of(centre)
            if not _covers(needles, union):
                continue
            seen.add(pair)
            part_name = str(part.get("name") or part_id)
            centre_name = str(centre.get("name") or centre_id)
            lines.append(f"{part_name} fits {centre_name}.")
            if part_id not in used:
                used.append(part_id)
            if centre_id not in used:
                used.append(centre_id)
    if not lines:
        return None
    return " ".join(lines), used
