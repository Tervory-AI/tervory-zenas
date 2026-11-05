"""Rank a shopper query against one catalogue.

The words before a relation (for, fits, pairs with, goes on, compatible with,
to go with, and the same kind of phrase) are the part the shopper wants. A
word counts as a whole token, or as the same token with a small spelling
change when it is at least four characters. A piece of a longer word does not
count. A model code that is one edit from a real code, or a short prefix of
one, can surface that row only as a closest match. It is never an exact hit.
A digit in a model name, including a single digit joined to or following
a model word, has to sit on that same row. A single letter after a catalogue
word, such as Type C, is a missing model unless one row carries both. The
token directly after a known brand is exact only when it is in that
catalogue's names, categories, or attributes, or in the bundled shopping
glossary. Any other word there, including an ordinary English word or a
short code, is a missing model.
Tokens that do not follow a brand still use the English frequency list:
they are not model codes unless they carry a digit the catalogue lacks.
A word that is already a category outranks a shared name, so that part comes
ahead of the product it attaches to. Typos use character trigrams, including
the text with spaces and dashes removed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rigbox.logic import _active, _haystack, _limit, _similarity, _sku, _tokens, public_product
from rigbox.shopper_attributes import shopper_attributes, shopper_row

_FILLER = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "to",
        "and",
        "or",
        "for",
        "with",
        "on",
        "in",
        "by",
        "at",
        "from",
        "is",
        "it",
        "be",
        "as",
        "per",
        "which",
        "what",
        "need",
        "that",
        "this",
        "these",
        "made",
        "called",
        "show",
        "find",
        "please",
        "my",
        "your",
        "use",
        "me",
        "do",
        "does",
        "can",
        "are",
    }
)
# Longer phrases first so "pairs with" wins over a shorter overlap at the same spot.
_RELATIONS = (
    "compatible with",
    "pairs with",
    "to go with",
    "goes with",
    "goes on",
    "to use on",
    "to mount on",
    "to replace on",
    "that suits",
    "fits",
    "fit",
    "for",
)
_COMPACT = re.compile(r"[^a-z0-9]+")
_WORD = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
# Frequency list and shopping glossary. Search does not download either.
_ENGLISH_PATH = Path(__file__).resolve().parents[1] / "english-20k.txt.gz"
_SHOPPING_PATH = Path(__file__).resolve().parents[1] / "shopping.txt"
# Relation words are not model codes. "go" would otherwise look like a short code.
_SKIP = frozenset(
    {
        "go",
        "goes",
        "going",
        "pair",
        "pairs",
        "paired",
        "fit",
        "fits",
        "fitting",
        "compatible",
        "suit",
        "suits",
        "mount",
        "replace",
        "use",
        "using",
    }
)
# A trigram-only match below this is a different product, not a typo.
_TYPO = 0.45
# A single token this close to a catalogue token is the same word misspelled.
# Below this, "sig" is not "sight". A different model code is handled apart from this.
_FUZZY = 0.6
_CATEGORY = 4.0


def _load_english(path: Path) -> frozenset[str]:
    import gzip
    found: set[str] = set()
    text = gzip.decompress(path.read_bytes()).decode('utf-8') if path.suffix == '.gz' else path.read_text(encoding='utf-8')
    for line in text.splitlines():
        word = line.split("#", 1)[0].strip().lower()
        if word.isalpha():
            found.add(word)
    return frozenset(found)


_ENGLISH = _load_english(_ENGLISH_PATH)
_SHOPPING = _load_english(_SHOPPING_PATH)


def _common_english(token: str) -> bool:
    """True when the bundled frequency list contains this word or its plural."""
    if token in _ENGLISH or token in _FILLER:
        return True
    return len(token) >= 5 and token.endswith("s") and token[:-1] in _ENGLISH


def _shopping_word(token: str) -> bool:
    """A curated descriptor: price, colour, material, carry, size, use, or noun."""
    if token in _SHOPPING or token in _FILLER:
        return True
    return _covered(token, _SHOPPING)


_BATTERY = re.compile(r"^[a-z]{2,3}\d{2,4}[a-z]?$")


def _battery(token: str) -> bool:
    """A cell code such as cr123, cr123a, cr2032, or cr2. Not a model code."""
    return _BATTERY.fullmatch(token) is not None


def _catalogue_tokens(rows: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for row in rows:
        found.update(_tokens(_text(row)))
    return found


def _join_spaced_codes(query: str, rows: list[dict[str, Any]]) -> str:
    """Read ``c 2`` as the catalogue code ``c2`` when that code is stocked.

    A short run of letters and the digits after it are one code only when
    the joined form is already a token on a row. ``Type 2`` stays two words,
    because ``type2`` is not a catalogue token.
    """
    vocab = _catalogue_tokens(rows)
    words = _WORD.findall(query)
    if len(words) < 2 or not vocab:
        return query
    joined: list[str] = []
    index = 0
    while index < len(words):
        letters = _tokens(words[index])
        if index + 1 < len(words) and len(letters) == 1 and letters[0].isalpha() and len(letters[0]) <= 3:
            digits = _tokens(words[index + 1])
            if len(digits) == 1 and digits[0].isdigit() and letters[0] + digits[0] in vocab:
                joined.append(words[index] + words[index + 1])
                index += 2
                continue
        joined.append(words[index])
        index += 1
    return " ".join(joined)


def search(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("q") or "").strip()
    limit = _limit(payload.get("limit"))
    tenant = payload.get("tenant_id")
    rows = list(payload.get("products") or [])
    if tenant is not None and str(tenant) != "":
        rows = [row for row in rows if str(row.get("tenant_id") or tenant) == str(tenant)]
    rows = [row for row in rows if _active(row)]
    if not query:
        return {"results": [], "ids": []}
    query = _join_spaced_codes(query, rows)
    sought, host = _intent(query)
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for row in rows:
        score = _score(sought, host, row)
        if score <= 0:
            continue
        ranked.append((score, _sku(row), row))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    chosen = [row for _, _, row in ranked[:limit]]
    results = [public_product(row) for row in chosen]
    body: dict[str, Any] = {"results": results, "ids": [item["id"] for item in results]}
    if not results:
        return body
    # Single-letter model words ("Type C") are a shop-search rule. The shared
    # catalogue leaves them to the brand-after check, which is what main does.
    missing = _unmatched_models(query, rows, letters=bool(payload.get("model_letters")))
    if missing:
        body["exact_match"] = False
        body["message"] = _no_exact(missing)
    else:
        body["exact_match"] = True
    return body


def _intent(query: str) -> tuple[str, str]:
    lowered = query.lower()
    found: tuple[tuple[int, int], re.Match[str]] | None = None
    for phrase in _RELATIONS:
        match = re.search(rf"\b{re.escape(phrase)}\b", lowered)
        if match is None:
            continue
        rank = (match.start(), -len(phrase))
        if found is None or rank < found[0]:
            found = (rank, match)
    if found is None:
        return query.strip(), ""
    match = found[1]
    left = query[: match.start()].strip()
    right = query[match.end() :].strip()
    if not left or not right:
        return query.strip(), ""
    return left, right


def _score(sought: str, host: str, row: dict[str, Any]) -> float:
    base = _match(sought, row)
    if host:
        if base <= 0:
            return 0.0
        return base * 5 + _match(host, row)
    return base


def _same_word(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) >= 4 and left + "s" == right:
        return True
    if len(right) >= 4 and right + "s" == left:
        return True
    return False


def _token_hit(needle: str, have: set[str]) -> bool:
    if any(_same_word(needle, item) for item in have):
        return True
    if any(_code_close(needle, item) for item in have):
        return True
    # Three-letter pieces stay exact, so a fragment cannot pose as a longer word.
    if len(needle) < 4:
        return False
    return any(len(item) >= 4 and _similarity(needle, item) >= _FUZZY for item in have)


def _code_close(left: str, right: str) -> bool:
    """One edit between two model codes. A different code is not the same model."""
    if left == right or len(left) < 4 or len(right) < 4:
        return False
    if not any(char.isdigit() for char in left) or not any(char.isdigit() for char in right):
        return False
    return _one_edit(left, right)


def _one_edit(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    i = 0
    j = 0
    edits = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) == len(right):
            i += 1
        j += 1
    edits += (len(right) - j) + (len(left) - i)
    return edits == 1


def _brand_tokens(rows: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for row in rows:
        found.update(_tokens(str(row.get("brand") or "")))
    return found


def _listed_tokens(rows: list[dict[str, Any]]) -> set[str]:
    """Names, categories, attributes, and the brand's own words."""
    found: set[str] = set()
    for row in rows:
        found.update(_tokens(str(row.get("name") or "")))
        found.update(_tokens(str(row.get("category") or "")))
        found.update(_tokens(str(row.get("brand") or "")))
        attributes = row.get("attributes") or {}
        if isinstance(attributes, dict):
            for key, value in attributes.items():
                found.update(_tokens(str(key)))
                found.update(_tokens(str(value)))
    return found


def _covered(token: str, have: set[str]) -> bool:
    return any(_same_word(token, item) for item in have)


def _row_has_all(parts: list[str], rows: list[dict[str, Any]]) -> bool:
    if not parts:
        return True
    for row in rows:
        have = set(_tokens(_text(row)))
        if all(_covered(part, have) for part in parts):
            return True
    return False


def _model_parts(parts: list[str]) -> list[str]:
    kept: list[str] = []
    for part in parts:
        if any(char.isdigit() for char in part):
            kept.append(part)
        elif part in _FILLER or part in _SKIP or len(part) < 2:
            continue
        else:
            kept.append(part)
    return kept


def _after_brand(flat: list[tuple[str, str]], index: int, brands: set[str]) -> bool:
    """True when this token is the next content word after a brand token."""
    if index <= 0 or flat[index][1] in _FILLER:
        return False
    previous = index - 1
    while previous >= 0 and flat[previous][1] in _FILLER:
        previous -= 1
    return previous >= 0 and _covered(flat[previous][1], brands)


def _unmatched_models(query: str, rows: list[dict[str, Any]], letters: bool = False) -> list[str]:
    """Model codes that no single row carries exactly.

    A digit that is not on the same row as the rest of its code is missing.
    The word directly after a brand is exact only when the catalogue lists
    it, or the shopping glossary does. English words and short codes there
    are missing models. A word that does not follow a brand is not a model
    code; the frequency list is what marks ordinary English in that position.
    """
    brands = _brand_tokens(rows)
    listed = _listed_tokens(rows)
    parsed = [(word, _tokens(word)) for word in _WORD.findall(query)]
    missing: list[str] = []
    seen: set[str] = set()

    def add(phrase: str) -> None:
        if phrase and phrase not in seen:
            seen.add(phrase)
            missing.append(phrase)

    for index, (word, parts) in enumerate(parsed):
        if not any(any(char.isdigit() for char in part) for part in parts):
            continue
        # "Type 3": the digit is its own word and belongs to the word before it.
        if len(parts) == 1 and parts[0].isdigit() and index > 0:
            continue
        needed = _model_parts(parts)
        if needed and not _row_has_all(needed, rows):
            if all(_battery(part) for part in needed):
                continue
            add(word)
    for index, (word, parts) in enumerate(parsed):
        if index == 0 or not (len(parts) == 1 and parts[0].isdigit()):
            continue
        prev_word, prev_parts = parsed[index - 1]
        needed = _model_parts(prev_parts) + parts
        if not _row_has_all(needed, rows):
            add(f"{prev_word} {word}")
    # "Type C": a single letter after a catalogue word. Off unless the shop
    # asked for it. "a" stays a filler. A letter sitting on a brand is left
    # to the brand-after loop so it is not named twice.
    if letters:
        for index, (word, parts) in enumerate(parsed):
            if index == 0 or not (len(parts) == 1 and len(parts[0]) == 1 and parts[0].isalpha()):
                continue
            if parts[0] in _FILLER:
                continue
            prev_word, prev_parts = parsed[index - 1]
            if not prev_parts or prev_parts[0] in _FILLER or prev_parts[0] in _SKIP:
                continue
            # "c 4" is a spaced code. The digit loop owns the letter in front of it.
            if index + 1 < len(parsed):
                next_parts = parsed[index + 1][1]
                if len(next_parts) == 1 and next_parts[0].isdigit():
                    continue
            # A typo such as "typ b" is not a new model. The word before the letter
            # has to be one this catalogue already uses, as "type" is.
            if not _covered(prev_parts[0], listed):
                continue
            if _covered(prev_parts[0], brands):
                continue
            needed = _model_parts(prev_parts) + parts
            if needed and not _row_has_all(needed, rows):
                add(f"{prev_word} {word}")
    flat: list[tuple[str, str]] = []
    for word, parts in parsed:
        for part in parts:
            flat.append((word, part))
    for index, (surface, token) in enumerate(flat):
        if any(char.isdigit() for char in token):
            continue
        if not _after_brand(flat, index, brands):
            # Away from the brand, the frequency list marks ordinary English.
            # Those words, and any other non-digit token here, are not models.
            if _common_english(token) or not any(char.isdigit() for char in token):
                continue
        if _covered(token, listed) or _shopping_word(token):
            continue
        # "c 4" is one phrase. The digit loop already owns the letter in front of it.
        if index + 1 < len(flat) and flat[index + 1][1].isdigit():
            continue
        add(surface)
    return missing


def _no_exact(words: list[str]) -> str:
    if len(words) == 1:
        return f"No exact match for {words[0]}."
    return "No exact match for " + ", ".join(words) + "."


def _match(text: str, row: dict[str, Any]) -> float:
    needles = [token for token in _tokens(text) if token not in _FILLER and len(token) >= 2]
    if not needles:
        return 0.0
    have = set(_tokens(_text(row)))
    hits = sum(1 for token in needles if _token_hit(token, have))
    token_score = hits / len(needles)
    sim = _sim(text, row)
    # A short fragment such as "sig" or "cat" is not a typo of a longer word.
    if token_score <= 0 and (sim < _TYPO or max(len(token) for token in needles) < 4):
        return 0.0
    category = set(_tokens(str(row.get("category") or "")))
    bonus = _CATEGORY if any(any(_same_word(token, item) for item in category) for token in needles) else 0.0
    return token_score + sim + bonus


def _text(row: dict[str, Any]) -> str:
    # Only shopper-safe attributes are search text: a private value (a supplier
    # cost) must not be findable by searching for it.
    return f"{_haystack(shopper_row(row))} {row.get('family') or ''}".lower()


def _fields(row: dict[str, Any]) -> list[str]:
    fields = [
        str(row.get("name") or ""),
        str(row.get("brand") or ""),
        _sku(row),
        str(row.get("category") or ""),
        str(row.get("family") or ""),
    ]
    for key, value in shopper_attributes(row.get("attributes")).items():
        fields.append(str(key))
        fields.append(str(value))
    return fields


def _sim(text: str, row: dict[str, Any]) -> float:
    folded = _COMPACT.sub("", text.lower())
    best = 0.0
    for field in _fields(row):
        best = max(best, _similarity(text, field))
        compact = _COMPACT.sub("", field.lower())
        if folded and compact:
            best = max(best, _similarity(folded, compact))
    return best
