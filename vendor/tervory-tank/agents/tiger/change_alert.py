"""Replay a price history and say which steps are real changes.

Sub-cent rounding is the same price. A flat series does not alert. Text that
tries to give orders is ignored. A missing price is not a change.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

_PROMPT = re.compile(
    r"(ignore (all |any )?(previous|above|prior)|disregard (the |all )?(instructions|above)"
    r"|you are now|system\s*:|new instructions|output the price|do not follow)",
    re.I,
)
_SKIP = {"blocked", "robots", "error", "none", "unread"}
_STOCK = {
    "instock": "InStock",
    "outofstock": "OutOfStock",
    "preorder": "PreOrder",
    "discontinued": "Discontinued",
    "limitedavailability": "LimitedAvailability",
    "soldout": "OutOfStock",
}


CANT_READ = "can't read"

_AVAIL_JSON = re.compile(r'"availability"\s*:\s*"([^"]*)"', re.I)
_AVAIL_FIELD = re.compile(
    r"""<(?:meta|link)\b[^>]*?(?:itemprop|property|name)\s*=\s*['"]"""
    r"""(?:availability|product:availability|og:availability)['"][^>]*?"""
    r"""(?:content|href)\s*=\s*['"]([^'"]*)['"]""",
    re.I,
)
_AVAIL_FIELD_REV = re.compile(
    r"""<(?:meta|link)\b[^>]*?(?:content|href)\s*=\s*['"]([^'"]*)['"][^>]*?"""
    r"""(?:itemprop|property|name)\s*=\s*['"]"""
    r"""(?:availability|product:availability|og:availability)['"]""",
    re.I,
)


# A negator within a few words before a stock phrase flips it.
# This is a class of words, not a list of sentences. A contraction ending in
# n't counts, as do not/no/never/none/nothing/without, the word zero, and 0.
# "not out of stock" is one negation of an out-of-stock phrase, so it is in stock.
_APOS = "['\u2019]"
_NEGATOR = (
    r"(?:"
    r"\b[a-z]+n" + _APOS + r"t\b"
    r"|\bwithout\b|\bnothing\b|\bnever\b|\bnone\b|\bnot\b|\bno\b"
    r"|\bzero\b|\b0\b"
    r")"
)
_GAP = r"(?:\s+[a-z0-9]+){0,4}"
_NEG_TAIL = re.compile(_NEGATOR + _GAP + r"\s*$", re.I)
# "no stock" needs the negator's own gap to be short and closed. A longer
# sentence such as "couldn't keep it in stock" is not this.
_AUX = {
    "be", "been", "being", "am", "is", "are", "was", "were",
    "have", "has", "had", "having",
    "do", "does", "did", "doing",
    "come", "comes", "coming", "came",
    "get", "gets", "got", "getting",
    "go", "goes", "going", "gone", "went",
}
_CLEAR_GAP = _AUX | {
    "it", "its", "them", "this", "that", "these", "those",
    "a", "an", "the", "any", "some",
    "longer", "left", "unit", "units",
    "currently", "now",
}
_WORD = re.compile(r"[A-Za-z0-9]+(?:['\u2019]t)?")
_WORD_NEG = re.compile(r"(?:" + _NEGATOR + r")\Z", re.I)
_ONLY_LEFT = re.compile(r"\bonly\s+(\d+)\s+left\b", re.I)
_N_AVAILABLE = re.compile(r"\b(\d+)\s+available\b", re.I)
# Selling fast, or trouble keeping up. A positive "we keep it in stock" or
# "held in stock" is a real in-stock statement, so the verb alone is not enough.
_INABILITY = re.compile(
    r"\b(?:couldn" + _APOS + r"t|can" + _APOS + r"t|cannot|could\s+not|can\s+not|unable)\b",
    re.I,
)
_MAINTAIN = re.compile(
    r"\b(?:keeps|keeping|kept|keep|holds|holding|held|hold|"
    r"stays|staying|stayed|stay|remains|remaining|remained|remain|"
    r"maintains|maintaining|maintained|maintain|"
    r"carries|carrying|carried|carry)\b",
    re.I,
)
_TROUBLE = re.compile(
    r"\b(?:trouble|difficult|challenge|struggle|struggling|barely)\b",
    re.I,
)
# "hard" only in "hard to keep" or "hard to stay". "hard cover" is not trouble.
_HARD_TO = re.compile(
    r"\bhard\s+to\s+(?:keep|hold|stay|remain|maintain|carry)\b",
    re.I,
)
_SELLING_PACE = re.compile(
    r"\bsell(?:s|ing|ed)?\b(?:\s+[a-z]+){0,4}\s+fast\b(?:\s+[a-z]+){0,3}\s+"
    r"to\s+(?:keep|hold|stay|remain|maintain|carry)\b",
    re.I,
)
# "no stock" and "without stock" name stock without "in" or "out of".
_NO_STOCK = re.compile(_NEGATOR + _GAP + r"\s+stock\b", re.I)
_PHRASE = (
    ("OutOfStock", re.compile(
        r"(?:schema\.org[/\\]+outofstock|\bout[\s\-]*of[\s\-]*stock\b|\bsold[\s\-]*out\b"
        r"|\boutofstock\b|\bunavailable\b)",
        re.I,
    )),
    ("LimitedAvailability", re.compile(
        r"(?:schema\.org[/\\]+limitedavailability|\blimited[\s\-]*availability\b|\blimitedavailability\b)",
        re.I,
    )),
    ("PreOrder", re.compile(
        r"(?:schema\.org[/\\]+preorder|\bpre[\s\-]*order\b|\bpreorder\b)",
        re.I,
    )),
    ("Discontinued", re.compile(
        r"(?:schema\.org[/\\]+discontinued|\bdiscontinued\b)",
        re.I,
    )),
    ("InStock", re.compile(
        r"(?:schema\.org[/\\]+instock|\bin[\s\-]*stock\b|\binstock\b)",
        re.I,
    )),
)


def _negated(prefix: str) -> bool:
    return _NEG_TAIL.search(prefix) is not None


def _clause_words(prefix: str) -> list[str]:
    """Words before a stock phrase, in the same sentence."""
    clause = re.split(r"[.!;?]+", prefix)[-1]
    return [word.lower().replace("\u2019", "'") for word in _WORD.findall(clause)]


def _negator_word(word: str) -> bool:
    return _WORD_NEG.search(word) is not None


def _negated_status(words: list[str]) -> bool:
    """A negator, then only the closed gap. Used for "no stock", not for phrases."""
    index = None
    for i, word in enumerate(words):
        if _negator_word(word):
            index = i
    if index is None:
        return False
    return all(word in _CLEAR_GAP or word.isdigit() for word in words[index + 1:])


def _selling_fast(prefix: str) -> bool:
    """Demand or trouble keeping the item stocked. Not a positive keep or hold."""
    clause = re.split(r"[.!;?]+", prefix)[-1]
    if _SELLING_PACE.search(clause) or _HARD_TO.search(clause):
        return True
    if _MAINTAIN.search(clause) is None:
        return False
    return _INABILITY.search(clause) is not None or _TROUBLE.search(clause) is not None


def _count_token(number: str) -> str:
    return "OutOfStock" if int(number) == 0 else "InStock"


def _count_phrases(text: str) -> list[str]:
    """'only N left' and 'N available'. Zero is out of stock."""
    found: list[str] = []
    for pattern in (_ONLY_LEFT, _N_AVAILABLE):
        for match in pattern.finditer(text):
            token = _count_token(match.group(1))
            if _negated_status(_clause_words(text[:match.start()])):
                flipped = _flip_stock(token)
                token = flipped or token
            if token not in found:
                found.append(token)
    return found


def _clear_bare_stock(text: str) -> bool:
    """'no stock' and 'without stock'. Not 'couldn't keep it in stock'."""
    match = _NO_STOCK.search(text)
    if not match:
        return False
    words = _clause_words(match.group(0))
    if words and words[-1] == "stock":
        words = words[:-1]
    return _negated_status(words)


def _flip_stock(token: str) -> str | None:
    """Negation swaps in stock and out of stock. Other phrases then cannot be told."""
    if token == "InStock":
        return "OutOfStock"
    if token == "OutOfStock":
        return "InStock"
    return None


def phrases_in(value: Any) -> list[str]:
    """Known stock phrases in this wording. A negator before the phrase wins.

    "Couldn't keep it in stock" and the same kind of selling-fast line are
    not a stock phrase. "Held in stock" and "we keep it in stock" are.
    """
    if value is None:
        return []
    text = str(value).strip()
    if not text or text == CANT_READ:
        return []
    found: list[str] = []
    for token, pattern in _PHRASE:
        for match in pattern.finditer(text):
            prefix = text[:match.start()]
            prose_in_stock = token == "InStock" and not re.search(r"schema\.org", match.group(0), re.I)
            if prose_in_stock and _selling_fast(prefix):
                continue
            if _negated(prefix):
                flipped = _flip_stock(token)
                if flipped and flipped not in found:
                    found.append(flipped)
                continue
            if token not in found:
                found.append(token)
    for token in _count_phrases(text):
        if token not in found:
            found.append(token)
    if found:
        return found
    if _clear_bare_stock(text):
        return ["OutOfStock"]
    tail = text.rstrip("/").split("/")[-1]
    key = re.sub(r"[\s_\-]+", "", tail).lower()
    token = _STOCK.get(key)
    return [token] if token else []


def stock_token(value: Any) -> str | None:
    """A known stock phrase, including the same phrase with extra words."""
    found = phrases_in(value)
    if len(found) == 1:
        return found[0]
    return None


def stock_reading(value: Any) -> str | None:
    """A known stock phrase, nothing when stock was not stated, or "can't read".

    Clear out-of-stock wording is out of stock. A positive keep or hold is
    in stock. Selling-fast wording, and any other wording with no known
    phrase, is "can't read".
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text == CANT_READ:
        return CANT_READ
    return stock_token(text) or CANT_READ


# Stock lines only. A wrapper div whose class says "instock" is not a stock phrase.
_STOCK_LINE = re.compile(
    r"""<(?:p|span|a|li|dd|td|h2|h3)\b[^>]*"""
    r"""(?:itemprop\s*=\s*['"]availability['"]|class\s*=\s*['"][^'"]*"""
    r"""(?:availability|\bstock\b|in-stock|out-of-stock)[^'"]*['"])[^>]*>"""
    r"""(.*?)</(?:p|span|a|li|dd|td|h2|h3)>""",
    re.I | re.S,
)
_TAG = re.compile(r"<[^>]+>")


def stock_statements(html: str) -> list[str]:
    """Stock wording on the page, before it is renamed to a token."""
    if not html:
        return []
    found: list[str] = []
    for pattern in (_AVAIL_JSON, _AVAIL_FIELD, _AVAIL_FIELD_REV):
        for match in pattern.finditer(html):
            text = match.group(1).strip()
            if text:
                found.append(text)
    for match in _STOCK_LINE.finditer(html):
        text = " ".join(_TAG.sub(" ", match.group(1)).split())
        if text and len(text) <= 80:
            found.append(text)
    return found


def record_stock(statements: list[str]) -> str | None:
    """Record the stock the page states. Out of stock wins over in stock.

    "Not in stock" is out of stock, not in stock. One selling-fast line does
    not veto a known phrase. "Can't read" is when the only wording is not a
    known phrase, or two different phrases still disagree and neither is out
    of stock.
    """
    if not statements:
        return None
    tokens: list[str] = []
    other = False
    for text in statements:
        found = phrases_in(text)
        if not found:
            if str(text).strip():
                other = True
            continue
        for token in found:
            if token not in tokens:
                tokens.append(token)
    if "OutOfStock" in tokens:
        return "OutOfStock"
    if len(tokens) == 1:
        return tokens[0]
    if len(tokens) > 1:
        return CANT_READ
    if other:
        return CANT_READ
    return None


def stated_availability(html: str) -> str | None:
    """Availability words written in a stock field, before they are renamed."""
    found = stock_statements(html)
    return found[0] if found else None


def normalize_price(value: Any) -> str | None:
    """A stated amount as cents. Rounding below half a cent collapses."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    if _PROMPT.search(text):
        return None
    text = text.replace("\xa0", "").replace(" ", "")
    text = re.sub(r"^[A-Za-z]{1,3}\.?", "", text)
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text or text in {"-", ".", ",", "-."}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", text):
            text = text.replace(",", "")
        elif re.fullmatch(r"-?\d+,\d{1,2}", text):
            text = text.replace(",", ".")
        else:
            return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number < 0:
        return None
    return f"{number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


def cents(value: Any) -> Decimal | None:
    text = normalize_price(value)
    if text is None:
        return None
    return Decimal(text)


def price_moved(before: Any, after: Any, threshold: Any) -> bool:
    """True when the cent price moved by more than the watch's fraction."""
    old = cents(before)
    new = cents(after)
    if old is None or new is None or old == new:
        return False
    if old == 0:
        return True
    try:
        limit = Decimal(str(threshold))
    except InvalidOperation:
        return False
    return abs(new - old) / abs(old) > limit


def replay(history: list[dict[str, Any]] | None, threshold: Any = "0") -> list[dict[str, Any]]:
    """Older helper. A watch check does not call this."""
    if not history:
        return []
    previous: dict[str, Any] | None = None
    results: list[dict[str, Any]] = []
    for raw in history:
        row = _clean(raw if isinstance(raw, dict) else {})
        alerts: list[dict[str, str]] = []
        if previous is not None and row is not None:
            alerts = _alerts(previous, row, threshold)
        results.append({"alerts": alerts})
        if row is not None and row.get("price") is not None:
            previous = row
    return results


def _clean(point: dict[str, Any]) -> dict[str, Any] | None:
    method = str(point.get("method") or "")
    if method in _SKIP:
        return None
    price_raw = point.get("price")
    if isinstance(price_raw, str) and _PROMPT.search(price_raw):
        return None
    price = normalize_price(price_raw)
    if price is None:
        return None
    availability = point.get("availability")
    if isinstance(availability, str) and _PROMPT.search(availability):
        availability = None
    return {
        "price": price,
        "availability": stock_token(availability),
        "method": method or "read",
    }


def _alerts(previous: dict[str, Any], current: dict[str, Any], threshold: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if price_moved(previous.get("price"), current.get("price"), threshold):
        found.append({
            "kind": "price",
            "before": str(previous.get("price")),
            "after": str(current.get("price")),
        })
    before_stock = previous.get("availability")
    after_stock = current.get("availability")
    if before_stock and after_stock and before_stock != after_stock:
        found.append({"kind": "stock", "before": str(before_stock), "after": str(after_stock)})
    return found
