"""Compare numeric values in prose with the evidence, never the question."""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, DecimalException
from typing import Any

_UNITS = dict(zip(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(),
    range(20),
))
_TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
_ORDINALS = dict(zip("first second third fourth fifth sixth seventh eighth ninth tenth".split(), range(1, 11)))
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000, "trillion": 1000000000000}
_FRACTIONS = {"half": Decimal("0.5"), "quarter": Decimal("0.25")}
_WORDS = _UNITS | _TENS | _ORDINALS | _SCALES | _FRACTIONS
_WORD_PATTERN = "|".join(_WORDS)
_NUMERAL = r"[+\-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:[eE][+\-]?\d+)?"
_TOKEN = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})|"
    r"(?P<fraction>" + _NUMERAL + r"/" + _NUMERAL + r")|"
    r"(?P<number>" + _NUMERAL + r")(?P<scale>\s*(?:hundred|thousand|million|billion|trillion|bn|[kmbt])\b)?|"
    r"(?P<words>\b(?:(?:minus|negative|plus|positive)[\s-]+)?(?:" + _WORD_PATTERN + r")(?:[\s-]+(?:and[\s-]+)?(?:" + _WORD_PATTERN + r"))*(?:\s+point(?:\s+(?:zero|one|two|three|four|five|six|seven|eight|nine))+)?\b)",
    re.IGNORECASE,
)


def _word_number(phrase: str) -> Decimal | str:
    if " point " in phrase.lower():
        whole, fraction = phrase.lower().split(" point ", 1)
        base = _word_number(whole)
        if not isinstance(base, Decimal) or base != base.to_integral_value():
            return "unsupported number phrase: " + phrase
        digits = "".join(str(_UNITS[word]) for word in fraction.split())
        sign = -1 if whole.startswith(("minus ", "negative ")) else 1
        return base + sign * Decimal("0." + digits)
    words = phrase.lower().replace("-", " ").split()
    sign = -1 if words[0] in {"minus", "negative"} else 1
    if words[0] in {"minus", "negative", "plus", "positive"}:
        words = words[1:]
    total = current = 0
    previous = None
    for word in words:
        if word == "and":
            continue
        value = _WORDS[word]
        if word in _SCALES:
            if not current:
                return "unsupported number phrase: " + phrase
            if value == 100:
                current *= value
            else:
                total += current * value
                current = 0
        else:
            # Reject ambiguous lists ("one two") instead of inventing their sum.
            if previous in (_UNITS | _ORDINALS) or (previous in _TENS and value >= 10):
                return "unsupported number phrase: " + phrase
            current += value
        previous = word
    return Decimal(sign * (total + current))


def text_numbers(text: str) -> set[Decimal | str]:
    text = unicodedata.normalize("NFKC", text).replace("−", "-")
    text = re.sub(r"\b(?:minus|negative)\s+(?=[\d.])", "-", text, flags=re.IGNORECASE)
    result: set[Decimal | str] = set()
    for match in _TOKEN.finditer(text):
        if stamp := match.group("date"):
            # A date is checked as a whole too; matching its parts is insufficient.
            result.add("date:" + stamp)
            result.update(Decimal(part) for part in stamp.split("-"))
        elif fraction := match.group("fraction"):
            numerator, denominator = fraction.replace(",", "").split("/")
            result.add(Decimal(numerator) / Decimal(denominator))
        elif raw := match.group("number"):
            number = Decimal(raw.replace(",", ""))
            if scale := match.group("scale"):
                number *= (_SCALES | {"k": 1000, "m": 1000000, "b": 1000000000, "bn": 1000000000, "t": 1000000000000})[scale.strip().lower()]
            result.add(number)
        else:
            result.add(_word_number(match.group("words")))
    return result


def evidence_numbers(value: Any) -> set[Decimal | str]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return {Decimal(str(value))}
    if isinstance(value, str):
        return text_numbers(value)
    if isinstance(value, dict):
        # Field names such as m3 are not evidence that a value of 3 was observed.
        values = value.values()
    elif isinstance(value, list):
        values = value
    else:
        return set()
    return set().union(*(evidence_numbers(item) for item in values))


def supported(summary: str, evidence: dict) -> bool:
    try:
        return text_numbers(summary) <= evidence_numbers(evidence)
    except (DecimalException, ValueError, OverflowError):
        return False
