"""Which product attributes a shopper may see, and which stay private.

Catalogue rows carry free-form ``attributes`` JSON. Stores and importers put
private pricing inputs there too (Custom Nation's POF USA rows carry
``export_usd``, the supplier's export price). Two layers keep them private:

1. Import. ``split_private`` moves every key that matches ``private_key`` out
   of ``attributes``; the store keeps it in ``agent_rig.product_private_attributes``,
   which no shopper query reads. ``schema.sql`` moves keys already stored.
2. Boundary and search. ``shopper_attributes`` is an allowlist applied to
   shopper responses (``quotes.privacy.shopper_body``), shopper-facing model
   prompts (``Service.ask``) and every search index (``rank``, ``cn``,
   ``Service`` rows, the Postgres search text in ``store``).

``rigbox.logic`` is byte-mirrored into approved, immutable Skala packages, so
it is not changed; its callers hand it rows that are already allowlisted.

Top-level keys and the fields inside structured attributes are allowlisted:
a key a store invents tomorrow stays private until it is added here on
purpose. Structured attributes have fixed shapes (``_SHAPES``); any other
allowlisted key may hold a scalar or a list of scalars only.

Words the store chooses (option names and labels, default names, stock and
price-history labels, strings in a list, every field of a ``verified`` fact)
cannot be allowlisted, so they are screened word by word against the
private-key pattern (``private_name``, a denylist). A hit drops the item and
the numbers it labels. ``verified`` is also structurally strict: dict facts
only, a string ``name``, an http(s) ``source``. Known limit: a supplier's
own name ("RSR price", ``options: {"rsr": ...}``) is not on the cost-word
list, so it passes; the number beside it is still not a cost key.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import unquote

# Private pricing inputs and supplier provenance, matched anywhere in a key.
# export_* (e.g. Custom Nation's export_usd), wholesale, buy/purchase prices,
# markup and profit are pricing inputs just like cost, landed, duty and margin.
# schema.sql carries the same pattern for the stored-row migration
# (tests/test_rig_cost_guard.py keeps the two identical).
PRIVATE_KEY_PATTERN = (
    r"cost|duty|margin|landed|markup|profit|wholesale|(^|_)export|(^|_)(buy|purchase|dealer|trade)_?price"
    r"|(^|_)vat($|_)|(^|_)fx($|_)|exchange_rate|supplier|pricing_rule|^store$|^source_id$|ship.?from|^region$|^seen_at$"
)
_PRIVATE = re.compile(PRIVATE_KEY_PATTERN, re.IGNORECASE)

# Display copy and presentation flags the widget, /rig pages and cn.py read.
_DISPLAY = {
    "summary", "detail", "tagline", "match_note", "photo_note", "demo_note",
    "sample", "demo_item", "group", "kind", "relation", "upgrade_for",
    "features", "in_box", "included", "options", "defaults", "verified",
    "price_label", "price_history", "stock",
}
# Measurable facts that decide fit (docs/rig/catalogue-feed.md: thread, rail,
# tube size, length, bolt pattern...). Shown to shoppers by design.
_FIT_FACTS = {
    "thread", "rail", "mount", "footprint", "optic_cut", "fits_cuts", "fits_products",
    "tube_mm", "tube_size", "barrel_mm", "barrel_in", "length", "length_mm", "length_in",
    "bolt_pattern", "base", "body", "rounds", "capacity",
    "size", "weight", "weight_g", "material", "finish", "color", "colour",
}
SHOPPER_ATTRIBUTES = frozenset(_DISPLAY | _FIT_FACTS)

# Inner keys of structured attributes. ``stock`` is exactly what cn._stock
# reads; ``price_history`` is the was/now badge (archon catalogue); option
# choices are what cn reads (code, label, delta, sku) plus the shown price;
# ``verified`` is a cited fact.
STOCK_KEYS = frozenset({"status", "qty", "location", "lead", "label"})
PRICE_HISTORY_KEYS = frozenset({"was", "amount", "price", "on", "date", "label"})
OPTION_CHOICE_KEYS = frozenset({"code", "label", "delta", "sku", "price"})
VERIFIED_KEYS = frozenset({"name", "value", "source", "note"})

_SCALAR = (str, int, float, bool)


def private_key(key: Any) -> bool:
    return bool(_PRIVATE.search(str(key)))


_WORDS = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


# The pricing words in PRIVATE_KEY_PATTERN, without the provenance words (supplier, store, region...).
# A stock ``location`` labels the numbers beside it only with one of these: "Supplier warehouse" is a
# real location, "Dealer price" is not.
_PRICING = re.compile(
    r"cost|duty|margin|landed|markup|profit|wholesale|(^|_)export|(^|_)(buy|purchase|dealer|trade)_?price"
    r"|(^|_)vat($|_)|(^|_)fx($|_)|exchange_rate|pricing_rule", re.IGNORECASE)


def _forms(name: str) -> tuple[str, str, str]:
    # Drop invisible format characters (zero-width, soft hyphen), fold fullwidth forms, decode %xx,
    # and split camelCase, so "pofExportUsd", "Dea\u200dler Price" and "?exp%6Frt_usd=" are screened too.
    text = unicodedata.normalize("NFKC", "".join(ch for ch in unquote(name) if unicodedata.category(ch) != "Cf"))
    words = _WORDS.sub("_", _CAMEL.sub("_", text).lower()).strip("_")
    return name, text, words


def private_name(name: Any) -> bool:
    """A store-chosen name, label or bit of text that names a private input.

    Screened as written and word by word, after decoding %xx, dropping
    invisible format characters, folding fullwidth forms and splitting
    camelCase, so "Dealer Price", "POF export_usd", "export_usd: 1",
    "pofExportUsd", "ex\u200bport_usd" and "exp%6Frt_usd" all count. Anything that is not a string
    (a missing name, a list, a dict) counts as private: it can't be checked.
    """
    if not isinstance(name, str):
        return True
    return any(private_key(form) for form in _forms(name))


def _pricing_name(name: str) -> bool:
    return any(_PRICING.search(form) for form in _forms(name))


def _has_number(text: str) -> bool:
    """Any character with a numeric value, in any script or form, after dropping invisible characters.

    Covers "4377", "R 4,377.00", "4377 rand", "4.377e3", "٤٬٣٧٧", "४३७७", "𝟒𝟑𝟕𝟕", "⁴³⁷⁷", "④③⑦⑦",
    "❹❸❼❼" and "43\u200b77": under a private label, any of them is the labelled amount.
    """
    return any(unicodedata.numeric(ch, None) is not None for ch in text if unicodedata.category(ch) != "Cf")


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, _SCALAR)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


# "name: amount" or "name = amount" in any clause ("Weight: 2 kg; Dealer price: 4377", "1. Dealer price: 4377",
# "_export_usd: 4377", "Dealer price, approx: 4377"): a number (any script) within the first 24 characters after
# the separator. A clause starts at the text start or after ; | • · ", " or any line break (\n \r \v \f NEL LS PS,
# "Weight: 2 kg\rDealer price: 4377"). The name stops at a hard break; the amount may follow one ("Dealer price:\n4377").
_CLAUSE = re.compile(r"[;|\n\r\v\f\x85\u2028\u2029\u2022\u00b7]|,\s")
_PAIR = re.compile(
    r"[\W\d_]*?([A-Za-z][^:=;|\n\u2022\u00b7]{0,60}?)\s*[:=][\s;|\u2022\u00b7,]*([^;|\n\u2022\u00b7]{0,24})", re.DOTALL)
# The be5300de opening-pair check, kept as is so nothing it hid can show.
_OPENING_PAIR = re.compile(r"^\W*([A-Za-z][^:=\n]{0,60}?)\s*[:=]\s*(.{0,12})", re.DOTALL)


# A label that runs straight into an amount, from any word start: "Dealer price, 4377", "Dealer price 4377",
# "Weight: 2 kg,Dealer price: 4377", "Dealer price \u00b7 : 4377", "Cost - R4377", "Dealer price: 4\u00b7377".
# The label is a run of letters, spaces, "_", "'", "-" and breaks; each word end in it is tried, and the last two
# words before any money word are screened. Separators, dashes, spaces and breaks may sit between label and
# amount. The amount starts with a non-letter or a money prefix ("R4377", "approx 4377") and ends at the next
# break, unless that break splits a digit run ("4\u00b7377") or what comes before it holds no number and no word
# ("$\u00b74,377", "R\u20284377", "ap\u00b7prox 4377"). It must read as money (_money_shaped), so a copy number
# after a private-looking word ("Heavy duty, 680 g", "Heavy duty Gen 5") is kept. Only adds to the pair checks.
_BREAKS = ";|,\n\r\v\f\x85\u2028\u2029\u2022\u00b7"
_WORD_START = re.compile(r"(?<![A-Za-z])[A-Za-z]")
_LABEL_RUN = re.compile(r"[A-Za-z][A-Za-z _'\-\u00ad\u200b-\u200f\u2060-\u2064\ufeff" + _BREAKS + r"]{0,60}")
_GAP = re.compile(r"[\s:=\-" + _BREAKS + r"]*")
_SPLIT_DIGITS = re.compile(r"(?<=\d)[" + _BREAKS + r"](?=\d)")
_AMOUNT_BREAK = re.compile(r"[" + _BREAKS + r"]")
_MONEY_WORDS = r"approx\w*|about|around|only|was|r|usd|zar|eur|gbp|aud|cad|nzd"
_MONEY_PREFIX = re.compile(r"(?:" + _MONEY_WORDS + r")\.?\s*(?=[^\sA-Za-z])", re.I)
_MONEY_WORD = re.compile(_MONEY_WORDS, re.I)
_FRAGMENT = re.compile(
    r"\s*(?:[^\sA-Za-z]*|[A-Za-z]{1,2}|(?i:a|ap|app|appr|appro|approx|ab|abo|abou|ar|aro|arou|aroun))\s*")
_LABEL_WORD = re.compile(r"[A-Za-z'_]+")
_LABEL_SPLIT = re.compile(r"[\s\-" + _BREAKS + r"]+")


def _label_amount(form: str) -> bool:
    for start in _WORD_START.finditer(form):
        label = _LABEL_RUN.match(form, start.start())
        # Every word end is tried as the end of the label, so a label may run across a break ("Dealer\nprice: 4377")
        # and a money word split by a break is read as the amount ("Dealer price, a\u00b7pprox 4377").
        ends = [start.start() + m.end() for m in _LABEL_WORD.finditer(label.group())]
        if any(_label_amount_at(form, start.start(), end) for end in ends):
            return True
    return False


def _label_amount_at(form: str, begin: int, end: int) -> bool:
    label = form[begin:end]
    raw = _LABEL_SPLIT.split(label)
    words = list(raw)
    while words and _MONEY_WORD.fullmatch(words[-1]):
        words.pop()
    if not words or not private_name(" ".join(words[-2:])):
        return False
    gap = _GAP.match(form, end)
    pieces = _AMOUNT_BREAK.split(_SPLIT_DIGITS.sub("", form[gap.end():gap.end() + 48]))
    amount = pieces[0]
    for piece in pieces[1:3]:
        if _has_number(amount) or not _FRAGMENT.fullmatch(amount):
            break
        amount = amount.rstrip() + piece.lstrip()
    amount = amount.strip()
    if not amount or (amount[0].isalpha() and not _MONEY_PREFIX.match(amount)):
        return False
    # A number glued to a word is a model or code ("Heavy duty IPX7", "M13x1"), unless that word is money or the
    # label needs it to be private ("R4377", "Cost4377", "Buy price4377").
    if (gap.end() == end and not _MONEY_WORD.fullmatch(raw[-1]) and not private_name(words[-1])
            and private_name(" ".join(words[-2:-1]))):
        return False
    # Copy phrases are read with the label words before the amount ("Gen 5", "Pack of 50").
    return _money_shaped(amount) and _has_number(_COPY_NUMBER.sub(" ", f"{label} {amount}".lower()))


# Words that may sit in an amount ("85 total", "4.4 thousand", "4377 AUD"); any other word makes it copy
# ("1000 lb rating", "1913 rail", "4140 steel").
_AMOUNT_WORD = re.compile(
    r"total|retail|nett?|price|value|only|mark|grand|thousand|hundred|million|bucks?|quid|sterling|apiece|inc|incl"
    r"|excl?|vat|gst|tax|rands?|dollars?|euros?|pounds?|yen|cents?|approx\w*|about|around|was|now|each|per|unit")


def _money_shaped(amount: str) -> bool:
    """Text after a private label that reads as money, not copy: a number left once ASCII copy phrases are
    taken out ("680 g" and "17 rounds" are copy), with a currency sign or word, or with no other word than
    an amount word or a three-letter currency code ("4377", "R4377", "$4,377.00", "4377 AUD", "85 total")."""
    if any(unicodedata.category(ch) == "Cf" for ch in amount):
        return _amount_text(amount)
    if not _has_number(_COPY_NUMBER.sub(" ", amount.lower())):
        return False
    folded = unicodedata.normalize("NFKC", amount)
    if any(unicodedata.category(ch) == "Sc" for ch in amount) or _MONEY.search(folded.lower()):
        return True
    words = [token for token in _TOKEN.findall(_COPY_NUMBER.sub(" ", amount))
             if len(token) >= 3 and token.isalpha()]
    return all(_AMOUNT_WORD.fullmatch(word.lower()) or (len(word) == 3 and word.isupper()) for word in words)


def _private_pair(text: str) -> bool:
    # Raw and NFKC: NFKC folds fullwidth labels ("Ｂｕｙ Ｐｒｉｃｅ: 4377") but turns "Ⅳ" into letters.
    for form in (text, unicodedata.normalize("NFKC", text)):
        matches = [_OPENING_PAIR.match(form)]
        matches += [_PAIR.match(form, start) for start in (0, *(m.end() for m in _CLAUSE.finditer(form)))]
        if any(match and _has_number(match.group(2)) and private_name(match.group(1)) for match in matches):
            return True
        if _label_amount(form):
            return True
    return False


# Currency and unit-price words: a string with a number and one of these is an amount ("approximately R4377",
# "43 rand", "99 per unit").
_MONEY = re.compile(
    r"(?<![a-z])r\s?\d|\b(usd|zar|eur|gbp|rand|dollars?|euros?|pounds?|cents?|per|each|ea|unit|net|incl|excl"
    r"|approx\w*|about|around)\b")
_TOKEN = re.compile(r"[^\W_]+")


# Numbers that are product copy, not an amount: "2-year warranty", "2 magazines", "Holds 17 rounds", "Pack of 50", "Gen 5".
# ASCII digits only, matched on the text as written: a copy phrase in another script or with invisible characters
# ("\u2167\u2165 rounds", "\uff18\uff15 rounds", "Holds 8\u200b5 rounds") is not copy, so its number still counts.
_COPY_NUMBER = re.compile(
    r"[0-9]+(?:\s*-\s*|\s+)?(?:years?|yrs?|months?|weeks?|days?|hours?|magazines?|mags?|rounds?|packs?|pieces?|pcs?"
    r"|sets?|slots?|inch(?:es)?|mm|cm|kg|g|lbs?|oz|ml)\b"
    r"|\b(?:pack|set|box|case)\s+of\s+[0-9]+\b|\b(?:holds|gen|mk)\s*[0-9]+\b")


def _amount_text(text: str) -> bool:
    """A string that reads as an amount rather than copy.

    It holds a number and has 3 or more numeric characters in any script ("4377 rand", "USD 4,377.00",
    "\u2163\u2162\u2166\u2166"), has no word of 3 or more letters ("R 43", "4.4k", "NDM3Nw=="), carries a currency
    sign or word ("\u20ac43", "43 rand", "99 per unit"), or keeps a number once known copy phrases are taken out
    ("85 total", "85 AUD", "4.4 thousand", "was 85", "\u2167\u2165 total"). Only "2-year warranty",
    "2 magazines", "Holds 17 rounds", "Pack of 50", "Gen 5" and the like, in ASCII digits, are copy.
    """
    # Copy phrases are matched on the text as written, and never when it hides invisible characters.
    visible = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    left = text.lower() if visible != text else _COPY_NUMBER.sub(" ", text.lower())
    text = visible
    numeric = sum(unicodedata.numeric(ch, None) is not None for ch in text)
    if not numeric:
        return False
    # A word is a whole token of 3+ letters with no numeric character ("NDM3Nw" is not one).
    words = [token for token in _TOKEN.findall(text)
             if len(token) >= 3 and all(ch.isalpha() and unicodedata.numeric(ch, None) is None for ch in token)]
    folded = unicodedata.normalize("NFKC", text).lower()
    return (numeric >= 3 or not words or any(unicodedata.category(ch) == "Sc" for ch in text)
            or bool(_MONEY.search(folded)) or _has_number(left))


def _labelled_list(value: list[Any]) -> bool:
    """A flat list where a private-looking string labels an amount: it sits beside a number or an
    amount-like string, or carries a number itself ("Dealer price 4377"). A private word beside
    ordinary copy (["Heavy duty", "2-year warranty"]) labels nothing."""
    private = [item for item in _strings(value) if private_name(item)]
    if not private:
        return False
    if any(_has_number(item) for item in private):
        return True
    return any((isinstance(item, (int, float)) and not isinstance(item, bool))
               or (isinstance(item, str) and _amount_text(item)) for item in value)


def _scalars(value: Any) -> Any:
    """A scalar, or a list of scalars. Anything nested is dropped (None).

    A list where a private-looking string labels an amount is dropped whole:
    in ["export_usd", 4377] or ["Dealer price", "R 4,377"] the string labels
    the amount beside it. Text with a "name: number" pair whose name is
    private, in any clause ("export_usd: 4377", "Weight: 2 kg; Dealer price
    = R4377") is dropped. Prose is not otherwise screened word by word, since
    copy such as "heavy duty" or "supplier warranty" is ordinary there and
    labels no amount: ["Heavy duty", "2-year warranty"] is kept.
    """
    if isinstance(value, str):
        return None if _private_pair(value) else value
    if _scalar(value):
        return value
    if isinstance(value, list):
        if _labelled_list(value):
            return None
        return [item for item in value if _scalar(item) and not (isinstance(item, str) and _private_pair(item))]
    return None


def _pick(value: Any, keys: frozenset[str]) -> dict[str, Any] | None:
    """Listed fields only, each a scalar or a list of scalars.

    Text that names a private input labels the numbers beside it, so every
    number in the dict is dropped, and so is every text that holds a number
    in any script or form ({"label": "export_usd", "qty": 4377},
    {"on": "export_usd", "price": "4377 rand"} and {"status": "Dealer price",
    "qty": "٤٬٣٧٧"} keep no number). The private text itself is dropped when
    it is the ``label`` or holds a number ("export_usd: 4377").

    A stock ``location`` is display copy: it labels the numbers only with a
    pricing word, so {"location": "Supplier warehouse", "qty": 3} keeps its
    qty (``rig.js`` renders "3 in stock"); the location text stays.
    """
    if not isinstance(value, dict):
        return None
    kept: dict[str, Any] = {}
    labelled = False
    for key, item in value.items():
        if key not in keys:
            continue
        shaped = _scalars(item)
        texts = _strings(item)
        private = [text for text in texts if private_name(text)]
        if private:
            if key != "location" or any(_pricing_name(text) for text in private):
                labelled = True
            if key == "label" or any(_has_number(text) for text in private):
                continue
        if shaped is None:
            continue
        kept[key] = shaped
    if labelled:
        kept = {key: item for key, item in kept.items()
                if item is None or (isinstance(item, str) and not _has_number(item))}
    return kept


def _choice(value: Any) -> Any:
    if isinstance(value, str):
        return None if private_name(value) else value
    if _scalar(value):
        return value
    return _pick(value, OPTION_CHOICE_KEYS) or None  # a choice with nothing left is no choice


def _options(value: Any) -> dict[str, Any] | None:
    """{option name: [choice, ...]}. A choice is a scalar or {code,label,delta,sku,price}.

    Option names and labels are the store's own words, so they are screened
    (``private_name``), not allowlisted. A list of choices where a
    private-looking string sits beside a number is dropped whole.
    """
    if not isinstance(value, dict):
        return None
    kept: dict[str, Any] = {}
    for name, choices in value.items():
        if private_name(name):
            continue
        if isinstance(choices, list):
            if _labelled_list(choices):
                continue
            kept[str(name)] = [item for item in (_choice(entry) for entry in choices) if item is not None]
        elif isinstance(choices, dict):
            picked = {str(label): _choice(entry) for label, entry in choices.items() if not private_name(label)}
            kept[str(name)] = {label: entry for label, entry in picked.items() if entry is not None}
        elif _scalar(choices) and not (isinstance(choices, str) and private_name(choices)):
            kept[str(name)] = choices
    return kept


def _defaults(value: Any) -> dict[str, Any] | None:
    """{option name: chosen code}. Scalars only."""
    if not isinstance(value, dict):
        return None
    return {str(name): item for name, item in value.items()
            if not private_name(name) and _scalar(item) and not (isinstance(item, str) and private_name(item))}


def _price_history(value: Any) -> Any:
    if isinstance(value, dict):
        return _pick(value, PRICE_HISTORY_KEYS)
    if isinstance(value, list):
        return [item for item in (_pick(entry, PRICE_HISTORY_KEYS) for entry in value) if item is not None]
    return None


_URL = re.compile(r"^https?://[^\s]+$")
_NAME_MAX = 64


def _verified_entry(entry: Any) -> dict[str, Any] | None:
    """One cited fact: {"name": str, "value": scalar or list of scalars, "source": URL, "note": str}.

    Structural allowlist: a fact must be a dict with a non-empty string
    ``name`` (at most 64 characters), a ``value`` that is a scalar or a list
    of scalars, and, when present, a ``source`` that is an http(s) URL. Every
    string in it (name, value, source, note) is screened with
    ``private_name``; one hit drops the whole fact, because the name is a key
    in all but form and a label anywhere marks the value as private.
    """
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > _NAME_MAX:
        return None
    raw = entry.get("value")
    if not (_scalar(raw) or (isinstance(raw, list) and all(_scalar(item) for item in raw))):
        return None
    source = entry.get("source")
    if source is not None and not (isinstance(source, str) and _URL.match(source)):
        return None
    note = entry.get("note")
    if note is not None and not isinstance(note, str):
        return None
    texts = [name, *_strings(raw), *([source] if source else []), *([note] if note else [])]
    if any(private_name(text) for text in texts):
        return None
    return {key: entry[key] for key in ("name", "value", "source", "note") if key in entry}


def _verified(value: Any) -> Any:
    """A list of cited facts. Bare entries (strings, numbers, nested lists) carry no name, so they are dropped."""
    if not isinstance(value, list):
        return None
    return [item for item in (_verified_entry(entry) for entry in value) if item is not None]


_SHAPES = {
    "stock": lambda value: _pick(value, STOCK_KEYS),
    "price_history": _price_history,
    "options": _options,
    "defaults": _defaults,
    "verified": _verified,
}


def shopper_attributes(attributes: Any) -> dict[str, Any]:
    """Allowlisted keys, each cut to its allowed shape. Never raises."""
    if not isinstance(attributes, dict):
        return {}
    kept: dict[str, Any] = {}
    for key, value in attributes.items():
        name = str(key)
        if name not in SHOPPER_ATTRIBUTES:
            continue
        shaped = _SHAPES.get(name, _scalars)(value)
        if shaped is not None:
            kept[name] = shaped
    return kept


def shopper_row(row: dict[str, Any]) -> dict[str, Any]:
    """A copy of a catalogue row whose attributes are shopper-safe (for search and ranking)."""
    attributes = row.get("attributes")
    return {**row, "attributes": shopper_attributes(attributes if isinstance(attributes, dict) else {})}


def shopper_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [shopper_row(row) for row in rows]


def split_private(attributes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(public, private): top-level keys that look like pricing inputs leave ``attributes``."""
    public = {key: value for key, value in attributes.items() if not private_key(key)}
    private = {key: value for key, value in attributes.items() if private_key(key)}
    return public, private
