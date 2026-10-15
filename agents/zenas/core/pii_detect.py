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
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_AMOUNT = re.compile(
    r"(?:[$]|R|USD|ZAR)\s?\d{1,3}(?:[, \u00a0]\d{3})*(?:\.\d{2})?"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b"
)
_DEAL_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b"
    r"|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)
_CASE_NO = re.compile(
    r"\b(?:case|docket)\s+(?:no\.?|number)\s*[:#]?\s*[A-Z0-9][A-Z0-9./-]{2,}\b"
    r"|\b\d{1,6}/\d{4}\b",
    re.IGNORECASE,
)
_REGISTRATION = re.compile(
    r"\b(?:registration|reg\.?|company)\s+(?:number|no\.?)\s*[:#]?\s*[A-Z0-9][A-Z0-9/-]{2,}\b",
    re.IGNORECASE,
)
_COMPANY = re.compile(
    r"\b[A-Z][A-Za-z0-9&'.-]*(?:\s+[A-Z][A-Za-z0-9&'.-]*){0,5}\s+\((?:Pty|Proprietary)\)\s+Ltd\b"
    r"|\b[A-Z][A-Za-z0-9&'.-]*(?:\s+[A-Z][A-Za-z0-9&'.-]*){0,5}\s+(?:Inc\.|LLC|Ltd\.|Limited|Corp\.|Corporation)\b"
)
_PERSON = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b")

_LABELS = {
    "sa_id": "SA_ID",
    "ssn": "SSN",
    "us_passport": "US_PASSPORT",
    "phone": "PHONE",
    "address": "ADDRESS",
    "bank": "BANK",
    "dob": "DOB",
    "email": "Email",
    "amount": "Amount",
    "deal_date": "Date",
    "case_no": "Case",
    "registration": "Registration",
    "party": "Party",
}

_STABLE = {
    "amount": "[Amount]",
    "email": "[Email]",
    "deal_date": "[Date]",
    "case_no": "[Case]",
    "registration": "[Registration]",
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


def _spans(text: str, *, parties: bool = True) -> list[tuple[int, int, str, str]]:
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
    for match in _EMAIL.finditer(text):
        found.append((match.start(), match.end(), "email", match.group(0)))
    for match in _AMOUNT.finditer(text):
        found.append((match.start(), match.end(), "amount", match.group(0)))
    for match in _DEAL_DATE.finditer(text):
        found.append((match.start(), match.end(), "deal_date", match.group(0)))
    for match in _CASE_NO.finditer(text):
        found.append((match.start(), match.end(), "case_no", match.group(0)))
    for match in _REGISTRATION.finditer(text):
        found.append((match.start(), match.end(), "registration", match.group(0)))
    if parties:
        for match in _COMPANY.finditer(text):
            found.append((match.start(), match.end(), "party", match.group(0)))
        for match in _PERSON.finditer(text):
            found.append((match.start(), match.end(), "party", match.group(0)))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, str, str]] = []
    cursor = -1
    for start, end, kind, value in found:
        if start < cursor:
            continue
        kept.append((start, end, kind, value))
        cursor = end
    return kept


def _placeholder(kind: str, count: int) -> str:
    if kind == "party":
        letter = chr(ord("A") + count - 1) if count <= 26 else str(count)
        return f"[Party {letter}]"
    stable = _STABLE.get(kind)
    if stable:
        return stable
    return f"[{_LABELS[kind]}_{count}]"


def detect(text: str, *, parties: bool = True) -> dict[str, Any]:
    body = text or ""
    spans = _spans(body, parties=parties)
    counts: dict[str, int] = {}
    matches: list[dict[str, str]] = []
    pieces: list[str] = []
    cursor = 0
    for start, end, kind, value in spans:
        counts[kind] = counts.get(kind, 0) + 1
        placeholder = _placeholder(kind, counts[kind])
        pieces.append(body[cursor:start])
        pieces.append(placeholder)
        cursor = end
        matches.append({"kind": kind, "placeholder": placeholder, "value": value})
    pieces.append(body[cursor:])
    return {"text": "".join(pieces), "matches": matches}


def still_personal(text: str) -> bool:
    """True when a personal pattern is still present. Placeholders do not count."""
    return bool(_spans(text or ""))


def party_names(parties: Any) -> list[str]:
    names: list[str] = []
    for item in parties or []:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            for key in ("name", "legal_name", "company"):
                value = str(item.get(key) or "").strip()
                if value:
                    names.append(value)
    return names


def regex_party_names(text: str) -> list[str]:
    """Company suffixes and titled people. Plain names are not guessed."""
    found: list[str] = []
    for pattern in (_COMPANY, _PERSON):
        found.extend(match.group(0) for match in pattern.finditer(text or ""))
    return found


def _replace_names(text: str, forms: list[str], placeholders: dict[str, str]) -> str:
    spans: list[tuple[int, int, str]] = []
    for name in forms:
        label = placeholders.get(name.casefold())
        if not label or len(name) < 3:
            continue
        for match in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
            spans.append((match.start(), match.end(), label))
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    pieces: list[str] = []
    cursor = 0
    covered = -1
    for start, end, label in spans:
        if start < covered:
            continue
        pieces.append(text[cursor:start])
        pieces.append(label)
        cursor = end
        covered = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def mask_client_text(
    text: str,
    parties: Any = None,
    *,
    forms: list[str] | None = None,
    placeholders: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Replace personal details. A value that is still present is a failure.

    When placeholders is set, each name keeps the letter assigned for the whole release.
    """
    body = text or ""
    names = list(forms) if forms is not None else party_names(parties)
    assigned = placeholders
    if assigned is None:
        assigned = {}
        for name in names:
            key = name.casefold()
            if len(name) < 3 or key in assigned:
                continue
            number = len(assigned) + 1
            letter = chr(ord("A") + number - 1) if number <= 26 else str(number)
            assigned[key] = f"[Party {letter}]"
    try:
        replaced = _replace_names(body, names, assigned)
        found = detect(replaced, parties=placeholders is None)
        masked = str(found.get("text") or "")
        for match in found.get("matches") or []:
            value = str(match.get("value") or "")
            if value and value in masked:
                return {"ok": False, "text": "", "reason": "masking failed"}
        masked = re.sub(r"\[ADDRESS_\d+\]", "[Address]", masked)
        masked = re.sub(r"\[PHONE_\d+\]", "[Phone]", masked)
        if still_personal(masked):
            return {"ok": False, "text": "", "reason": "masking failed"}
        for name in names:
            if len(name) >= 3 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", masked, re.IGNORECASE):
                return {"ok": False, "text": "", "reason": "masking failed"}
        return {"ok": True, "text": masked, "reason": ""}
    except Exception:
        return {"ok": False, "text": "", "reason": "masking failed"}


def public_view(result: dict[str, Any]) -> dict[str, Any]:
    """Drop raw values. Tank must not receive the identifier."""
    matches = []
    for item in result.get("matches") or []:
        matches.append({"kind": item["kind"], "placeholder": item["placeholder"]})
    return {"text": result.get("text") or "", "matches": matches}
