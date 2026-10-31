"""Find personal identifiers and replace them with placeholders.

South African ID numbers are checked with the official checksum and may be
split into their digit groups by spaces, dashes or a line break. Payment cards
are checked with Luhn. US Social Security, ITIN and EIN numbers, phone numbers
and passport numbers use their published shapes. The raw
value stays on the match so the box can encrypt it. The skill's public
result drops that value before anything is returned to Tank.
"""

from __future__ import annotations

import re
from typing import Any

# Separators found in real text between digit groups: spaces, no-break spaces,
# hyphens and Unicode dashes, and a PDF line break. Matching them in the
# pattern (not by rewriting the text first) keeps every span on the original text.
_DASH = "\\-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_SEP = rf"(?:[ \t\u00a0]*(?:[{_DASH}][ \t\u00a0]*)?(?:\n[ \t\u00a0]*)?)"
_SEP1 = rf"(?:[ \t\u00a0]*[{_DASH}][ \t\u00a0]*(?:\n[ \t\u00a0]*)?|[ \t\u00a0]?\n[ \t\u00a0]*|[ \t\u00a0])"
_NOT_BEFORE = rf"(?<![\d{_DASH}/])"
_NOT_AFTER = rf"(?![{_DASH}/]?\d)"

# SA ID: 13 digits, written whole or split at the YYMMDD / SSSS / C / A / Z group
# boundaries (6 4 3, 6 4 2 1, 2 2 2 4 3, 6 7). The checksum decides.
_SA_ID = re.compile(rf"{_NOT_BEFORE}(\d{{2}}{_SEP}\d{{2}}{_SEP}\d{{2}}{_SEP}\d{{4}}{_SEP}\d{{2}}{_SEP}\d){_NOT_AFTER}")
# SSN: dashed, Unicode-dashed or spaced. Nine digits in a row only after an SSN label.
def _ssn_body(sep: str) -> str:
    return rf"(?!000|666|9\d{{2}})\d{{3}}{sep}(?!00)\d{{2}}{sep}(?!0000)\d{{4}}"



_SSN = re.compile(rf"{_NOT_BEFORE}{_ssn_body(_SEP1)}{_NOT_AFTER}")
_SSN_LABELLED = re.compile(
    rf"\b(?:SSN|SS\s?No\.?|social\s+security(?:\s+(?:number|no\.?))?)\s*[:#]?\s*"
    rf"({_ssn_body(_SEP)}){_NOT_AFTER}",
    re.IGNORECASE,
)
# ITIN: 9XX, then a middle group of 50-65, 70-88, 90-92 or 94-99.
_ITIN = re.compile(
    rf"{_NOT_BEFORE}9\d{{2}}{_SEP1}(?:5\d|6[0-5]|7\d|8[0-8]|9[0-24-9]){_SEP1}\d{{4}}{_NOT_AFTER}"
)
_EIN = re.compile(
    rf"\b(?:EIN|FEIN|employer\s+identification\s+(?:number|no\.?)|federal\s+tax\s+id(?:entification)?(?:\s+(?:number|no\.?))?)"
    rf"\s*[:#]?\s*(\d{{2}}{_SEP1}\d{{7}}|\d{{9}}){_NOT_AFTER}",
    re.IGNORECASE,
)
_PASSPORT = re.compile(
    r"\bpassport(?:\s+(?:number|no\.?))?\s*[:#]?\s*([A-Z]\d{8}|\d{9})\b",
    re.IGNORECASE,
)
_LICENSE = re.compile(
    r"\b(?:driver'?s?|driving|drivers')\s+licen[cs]e(?:\s+(?:number|no\.?))?\s*[:#]?\s*"
    r"((?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{4,15})\b"
    r"|\bDL\s*(?:No\.?|#)\s*[:#]?\s*((?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{4,15})\b",
    re.IGNORECASE,
)
# A phone number may be followed by "/23" (an extension) or "/" and a second number.
_PHONE_START = rf"(?<![\d{_DASH}])"
_PHONE_END = rf"(?![{_DASH}]?\d)"
_PHONE = re.compile(
    # South Africa: +27 with or without (0), or a 0 trunk prefix, split or whole.
    rf"\+27{_SEP}(?:\(0\){_SEP})?[1-8]\d{_SEP}\d{{3}}{_SEP}\d{{4}}{_PHONE_END}"
    rf"|{_PHONE_START}\(0[1-8]\d\){_SEP}\d{{3}}{_SEP}\d{{4}}(?:\d(?=/))?{_PHONE_END}"
    rf"|{_PHONE_START}0[1-8]\d(?:{_SEP1}\d{{3}}{_SEP}\d{{4}}|\d{{3}}{_SEP1}\d{{4}}|\d{{7}}){_PHONE_END}"
    # North America: area code and exchange start 2-9. Bare digit groups need the
    # same dot or dash twice; a space-only form needs +1 or brackets.
    rf"|\+1{_SEP}\(?[2-9]\d{{2}}\)?{_SEP}[2-9]\d{{2}}{_SEP}\d{{4}}{_PHONE_END}"
    rf"|(?<![\d+])(?:1{_SEP1})?\([2-9]\d{{2}}\){_SEP}[2-9]\d{{2}}{_SEP1}\d{{4}}{_PHONE_END}"
    rf"|{_PHONE_START}(?:1[.{_DASH}])?[2-9]\d{{2}}(?P<nanp>[.{_DASH}])[2-9]\d{{2}}(?P=nanp)\d{{4}}{_PHONE_END}"
)
_CARD = re.compile(
    rf"{_NOT_BEFORE}(?:[3-6]\d{{3}}(?:{_SEP1}\d{{4}}){{3}}|3[47]\d{{2}}{_SEP1}\d{{6}}{_SEP1}\d{{5}}|[3-6]\d{{14,15}}){_NOT_AFTER}"
)
_STREET = (
    r"(?:Street|Road|Avenue|Drive|Lane|Close|Crescent|Boulevard|St|Rd|Ave|Dr|Ln)\b"
)
_ADDRESS = re.compile(
    rf"\b\d{{1,5}}\s+(?:[A-Za-z]+\s+){{0,3}}{_STREET}"
    r"(?:\.?\s+(?-i:N|S|E|W|NE|NW|SE|SW)\b)?",
    re.IGNORECASE,
)
# Suffixes that are also ordinary words are matched only when capitalised.
_ADDRESS_CAPITAL = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}(?:Terrace|Way|Place|Highway|Parkway|Circle|Square|Blvd|Hwy|Pkwy|Pl|Sq)\b"
)
_PO_BOX = re.compile(
    r"\b(?:P\.?\s?O\.?\s+Box|Post\s+Office\s+Box|Postnet\s+Suite)\s+\d{1,6}\b"
    r"|\bPrivate\s+Bag\s+X?\s?\d{1,6}\b"
    r"|\b(?:Erf|ERF)\s+\d{1,6}\b",
    re.IGNORECASE,
)
_ZIP4 = re.compile(r"(?<![\d-])\d{5}-\d{4}(?![\d-])")
_BANK = re.compile(
    r"\b(?:account|acct|iban)(?:\s+(?:number|no\.?))?\s*[:#]?\s*"
    r"([A-Z]{2}\d{2}[A-Z0-9]{11,30}|\d{2,6}(?:[ \u00a0-]\d{2,6}){1,4}(?![\d])|\d{8,18})\b",
    re.IGNORECASE,
)
_DOB = re.compile(
    r"\b(?:date of birth|dob|d\.o\.b\.?|born on|born)\s*[:\s]\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})(?![\d/])",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# "R" is a currency only when no letter sits right before it ("JR 1234/16" is a case number).
_AMOUNT = re.compile(
    r"(?:[$]|(?<![A-Za-z])R|\bUSD|\bZAR)\s?\d{1,3}(?:[, \u00a0]\d{3})*(?:\.\d{2})?"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b"
)
_DEAL_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|(?<![\d/])\d{1,2}/\d{1,2}/\d{4}(?![\d/])"
    r"|\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b"
    r"|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)
# A labelled case number may start with a court roll prefix and a space ("CA 12345/2019").
_CASE_NO = re.compile(
    r"\b(?:case|docket)\s+(?:no\.?|number)\s*[:#]?\s*(?:(?-i:[A-Z]{1,5})\s?(?=\d))?[A-Z0-9][A-Z0-9./-]{2,}\b"
    r"|(?<![\d/])\d{1,6}/\d{4}(?![\d/])"
    # A SAPS docket (CAS) number: serial / month / year, possibly broken over a line.
    r"|\bCAS\s*(?:no\.?\s*)?\d{1,6}/\d{1,2}/\d{4}\b"
    r"|(?<![\d/])\d{3,6}/\d{1,2}/\d{4}(?![\d/])"
    r"|(?<=\()\d{1,6}\s+of\s+\d{4}(?=\))",
    re.IGNORECASE,
)
_REGISTRATION = re.compile(
    r"\b(?:registration|reg\.?|company)\s+(?:number|no\.?)\s*[:#]?\s*[A-Z0-9][A-Z0-9/-]{2,}\b"
    # CIPC company registration: YYYY/NNNNNN/NN, with or without a label.
    r"|(?<![\d/])(?:18|19|20)\d{2}/\d{6}/\d{2}(?![\d/])",
    re.IGNORECASE,
)
# A name is up to six capitalised words, joined by spaces or "&", then a company suffix.
# Suffixes ending in a full stop take no word boundary, so "Corp." before a space still matches.
# Words of one name sit on one line, so a name never starts on the line above.
_NAME_WORDS = r"[A-Z][A-Za-z0-9&'.-]*(?:[ \t\u00a0]+(?:&[ \t\u00a0]+)?[A-Z][A-Za-z0-9&'.-]*){0,5}"
_COMPANY = re.compile(
    rf"\b{_NAME_WORDS}\s+(?:\(RF\)\s+)?\((?:Pty|PTY|Proprietary|PROPRIETARY)\)\s+(?:Ltd|LTD|Limited|LIMITED)\b"
    rf"|\b{_NAME_WORDS}\s+(?:\(RF\)\s+)?(?:(?:Inc|Ltd|Corp|Co|CO)\.|(?:LLC|Ltd|LTD|Limited|LIMITED|Corporation|CORPORATION|Company|COMPANY|Inc|INC|CC|NPC)\b)"
)
# A titled person. Initials may sit between the title and the surname ("Mr JA Surname",
# "Ms M. Surname"); upper-case transcripts write "MR SURNAME". A title used as a form of
# address ("Mr Speaker", "Madam Chair") is not a name.
_TITLE = r"(?:Mr|Mrs|Ms|Miss|Dr|Prof|Adv|Advocate|MR|MRS|MS|MISS|DR|PROF|ADV)"
_SURNAME = r"(?:(?:[Vv]an\s+(?:[Dd]er\s+|[Dd]en\s+)?|[Dd]e\s+|[Dd]u\s+|[Ll]e\s+))?[A-Z](?:[a-z]+(?:[’'][a-z]{2,})?|[’'][A-Z][a-z]+)(?:-[A-Z][a-z]+)?"
_PERSON = re.compile(
    rf"\b{_TITLE}\.?\s+(?!Speaker\b|President\b|Chair)"
    rf"(?:"
    rf"(?:[A-Z]{{1,3}}\.?\s+|(?:[A-Z]\.\s?){{1,3}}\s*)?{_SURNAME}(?:\s+{_SURNAME}){{0,2}}"
    rf"|(?:[A-Z]{{1,3}}\.?\s+)?[A-Z]{{2,}}(?:[’'][A-Z]+)?(?:-[A-Z]{{2,}})?"
    # A possessive ("Ms Surname’s") still ends the name.
    rf")(?=[’']s\b|[’'](?![A-Za-z])|(?![\w’']))"
)
# A litigant named on the line before its role in a judgment heading
# ("NAME SURNAME   First Applicant"). Public bodies are not personal and stay.
_ROLE = (
    r"(?:(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth|"
    r"FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|1st|2nd|3rd|\d{1,2}th)\s+)?"
    r"(?:Applicant|Respondent|Appellant|Plaintiff|Defendant|Excipient|Intervening\s+Party|"
    r"APPLICANT|RESPONDENT|APPELLANT|PLAINTIFF|DEFENDANT|EXCIPIENT)S?s?\b(?![’'])"
)
_ORDINAL_OR_ROLE = (
    r"(?i:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|applicants?|respondents?|"
    r"appellants?|plaintiffs?|defendants?|excipients?|intervening)\b"
)
_NAME_TOKEN = rf"(?!{_ORDINAL_OR_ROLE})(?:[A-Z][A-Za-z’'-]*[a-zA-Z]|[A-Z](?:\.[A-Z])*\.?|van|der|den|de|du|le|VAN|DER|DEN|DE|DU|LE)"
# Between the name and its role a heading may carry the person's ID number or a
# bracketed note, on the same line or on up to two lines of their own.
_NOTE = r"\([^()\n]{0,80}\)"
_ID_LINE = r"(?i:ID|identity)\b[^\n]{0,60}"
_LITIGANT = re.compile(
    rf"(?m)^[ \t]*({_NAME_TOKEN}(?:[ \t]+{_NAME_TOKEN}){{1,6}})[ \t,]*(?:{_NOTE}[ \t]*)?"
    rf"(?:\n[ \t]*(?:(?:{_NOTE}|{_ID_LINE})[ \t]*\n[ \t]*){{0,2}}(?:{_NOTE}[ \t]*)?)?{_ROLE}"
)
_NOT_A_PERSON = re.compile(
    r"\b(?:minister|ministry|municipality|municipal|metropolitan|commission|commissioner|department|"
    r"state|republic|government|council|authority|province|provincial|mec|executive|director|"
    r"tribunal|court|magistrate|registrar|sheriff|master|parliament|president|premier|police|"
    r"prosecutions?|public|national|fund|board|agency|regulator|university|speaker|ombud|office|"
    r"city|town|district|local|bank|trust|trustees|estate|union|association|society|"
    r"revenue|service|reserve|competition|ombudsman|legal\s+aid|road\s+accident|"
    r"counsel|attorneys?|instructed|heard|delivered|judgment|order|case|matter|between|"
    r"for|the|and|of|in|on|by|to|all|others|coram|before|reportable|interest|revised|"
    r"pty|ltd|limited|inc|cc|npc|soc|rf|proprietary|company|corporation|holdings|group)\b",
    re.IGNORECASE,
)

_LABELS = {
    "sa_id": "SA_ID",
    "ssn": "SSN",
    "us_passport": "US_PASSPORT",
    "itin": "ITIN",
    "ein": "EIN",
    "license": "LICENSE",
    "card": "CARD",
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
    # Citizenship digit: 0 citizen, 1 permanent resident, 2 refugee.
    if number[10] not in "012":
        return False
    digits = [int(char) for char in number]
    odd_sum = sum(digits[index] for index in range(0, 12, 2))
    even = int("".join(str(digits[index]) for index in range(1, 12, 2))) * 2
    even_sum = sum(int(char) for char in str(even))
    check = (10 - ((odd_sum + even_sum) % 10)) % 10
    return check == digits[12]


def luhn_ok(number: str) -> bool:
    """Payment card check digit (Luhn)."""
    digits = [int(char) for char in number if char.isdigit()]
    if len(digits) < 13:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


_PLACE_WORDS = {"south", "africa", "african", "united", "states", "america", "of"}


def litigant_names(text: str) -> list[tuple[int, int, str]]:
    """People named on the line before their role in a judgment heading."""
    found: list[tuple[int, int, str]] = []
    for match in _LITIGANT.finditer(text or ""):
        name = match.group(1).strip()
        if _NOT_A_PERSON.search(name):
            continue
        words = [word for word in name.split() if len(word.strip(".")) > 1]
        if len(words) < 2 or all(word.casefold() in _PLACE_WORDS for word in words):
            continue
        found.append((match.start(1), match.start(1) + len(name), name))
    return found


def _spans(text: str, *, parties: bool = True) -> list[tuple[int, int, str, str]]:
    found: list[tuple[int, int, str, str]] = []

    def add(match: re.Match[str], kind: str, group: int = 0) -> None:
        found.append((match.start(group), match.end(group), kind, match.group(group)))

    for match in _SA_ID.finditer(text):
        if sa_id_ok(_digits(match.group(1))):
            add(match, "sa_id", 1)
    for match in _CARD.finditer(text):
        if luhn_ok(match.group(0)):
            add(match, "card")
    for match in _SSN_LABELLED.finditer(text):
        add(match, "ssn", 1)
    for match in _SSN.finditer(text):
        add(match, "ssn")
    for match in _ITIN.finditer(text):
        add(match, "itin")
    for match in _EIN.finditer(text):
        add(match, "ein", 1)
    for match in _PASSPORT.finditer(text):
        add(match, "us_passport", 1)
    for match in _LICENSE.finditer(text):
        add(match, "license", 1 if match.group(1) else 2)
    for match in _BANK.finditer(text):
        add(match, "bank", 1)
    for match in _DOB.finditer(text):
        add(match, "dob", 1)
    for match in _PHONE.finditer(text):
        add(match, "phone")
    for pattern in (_ADDRESS, _ADDRESS_CAPITAL, _PO_BOX, _ZIP4):
        for match in pattern.finditer(text):
            add(match, "address")
    for match in _EMAIL.finditer(text):
        add(match, "email")
    for match in _AMOUNT.finditer(text):
        add(match, "amount")
    for match in _DEAL_DATE.finditer(text):
        add(match, "deal_date")
    for match in _CASE_NO.finditer(text):
        add(match, "case_no")
    for match in _REGISTRATION.finditer(text):
        add(match, "registration")
    if parties:
        for match in _COMPANY.finditer(text):
            add(match, "party")
        for match in _PERSON.finditer(text):
            add(match, "party")
        names: set[str] = set()
        for start, end, name in litigant_names(text):
            found.append((start, end, "party", name))
            names.add(name)
        # A litigant named in the heading is masked wherever the same name recurs.
        for name in sorted(names, key=len, reverse=True):
            for match in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
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
    letters: dict[str, str] = {}
    for start, end, kind, value in spans:
        if kind == "party" and " ".join(value.casefold().split()) in letters:
            placeholder = letters[" ".join(value.casefold().split())]
        else:
            counts[kind] = counts.get(kind, 0) + 1
            placeholder = _placeholder(kind, counts[kind])
            if kind == "party":
                letters[" ".join(value.casefold().split())] = placeholder
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
