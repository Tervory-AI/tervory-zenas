"""Find personal identifiers and replace them with placeholders.

South African ID numbers are checked with the official checksum. US Social
Security numbers and passport numbers use their published shapes. The raw
value stays on the match so the box can encrypt it. The skill's public
result drops that value before anything is returned to Tank.
"""

from __future__ import annotations

import re
from typing import Any

_SA_ID = re.compile(r"(?<!\d)(\d{13})(?!\d)")
_SSN = re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_PASSPORT = re.compile(
    r"\bpassport(?:\s+(?:number|no\.?))?\s*[:#]?\s*([A-Z]\d{8}|\d{9})\b",
    re.IGNORECASE,
)
_PHONE = re.compile(
    r"(?:\+27[\s.-]?\d{2}[\s.-]?\d{3}[\s.-]?\d{4}"
    r"|\b0\d{2}[\s.-]\d{3}[\s.-]\d{4}"
    r"|\+1[\s.-]?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}"
    r"|\(\d{3}\)\s*\d{3}[\s.-]\d{4})"
)
_ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Za-z]+\s+){0,3}"
    r"(?:Street|Road|Avenue|Drive|Lane|Close|Crescent|Boulevard|St|Rd|Ave|Dr|Ln)\b",
    re.IGNORECASE,
)
_BANK = re.compile(
    r"\b(?:account|acct|iban)(?:\s+(?:number|no\.?))?\s*[:#]?\s*"
    r"([A-Z]{2}\d{2}[A-Z0-9]{11,30}|\d{8,18})\b",
    re.IGNORECASE,
)
_DOB = re.compile(
    r"\b(?:date of birth|dob|born on)\s*[:\s]\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\b",
    re.IGNORECASE,
)

_LABELS = {
    "sa_id": "SA_ID",
    "ssn": "SSN",
    "us_passport": "US_PASSPORT",
    "phone": "PHONE",
    "address": "ADDRESS",
    "bank": "BANK",
    "dob": "DOB",
}


def sa_id_ok(number: str) -> bool:
    """South African ID checksum. Odd positions are summed; even positions are
    joined, doubled, and reduced to a digit sum. The check digit makes 10."""
    if not re.fullmatch(r"\d{13}", number):
        return False
    month = int(number[2:4])
    day = int(number[4:6])
    if month < 1 or month > 12 or day < 1 or day > 31:
        return False
    if number[10] not in "01":
        return False
    digits = [int(char) for char in number]
    odd_sum = sum(digits[index] for index in range(0, 12, 2))
    even = int("".join(str(digits[index]) for index in range(1, 12, 2))) * 2
    even_sum = sum(int(char) for char in str(even))
    check = (10 - ((odd_sum + even_sum) % 10)) % 10
    return check == digits[12]


def _spans(text: str) -> list[tuple[int, int, str, str]]:
    found: list[tuple[int, int, str, str]] = []
    for match in _SA_ID.finditer(text):
        value = match.group(1)
        if sa_id_ok(value):
            found.append((match.start(1), match.end(1), "sa_id", value))
    for match in _SSN.finditer(text):
        found.append((match.start(), match.end(), "ssn", match.group(0)))
    for match in _PASSPORT.finditer(text):
        found.append((match.start(1), match.end(1), "us_passport", match.group(1)))
    for match in _BANK.finditer(text):
        found.append((match.start(1), match.end(1), "bank", match.group(1)))
    for match in _DOB.finditer(text):
        found.append((match.start(1), match.end(1), "dob", match.group(1)))
    for match in _PHONE.finditer(text):
        found.append((match.start(), match.end(), "phone", match.group(0)))
    for match in _ADDRESS.finditer(text):
        found.append((match.start(), match.end(), "address", match.group(0)))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, str, str]] = []
    cursor = -1
    for start, end, kind, value in found:
        if start < cursor:
            continue
        kept.append((start, end, kind, value))
        cursor = end
    return kept


def detect(text: str) -> dict[str, Any]:
    body = text or ""
    spans = _spans(body)
    counts: dict[str, int] = {}
    matches: list[dict[str, str]] = []
    pieces: list[str] = []
    cursor = 0
    for start, end, kind, value in spans:
        counts[kind] = counts.get(kind, 0) + 1
        placeholder = f"[{_LABELS[kind]}_{counts[kind]}]"
        pieces.append(body[cursor:start])
        pieces.append(placeholder)
        cursor = end
        matches.append({"kind": kind, "placeholder": placeholder, "value": value})
    pieces.append(body[cursor:])
    return {"text": "".join(pieces), "matches": matches}


def public_view(result: dict[str, Any]) -> dict[str, Any]:
    """Drop raw values. Tank must not receive the identifier."""
    matches = []
    for item in result.get("matches") or []:
        matches.append({"kind": item["kind"], "placeholder": item["placeholder"]})
    return {"text": result.get("text") or "", "matches": matches}
