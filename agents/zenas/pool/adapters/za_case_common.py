"""Shared pieces for keyless South African case adapters.

Snapshots are JSON envelopes. A live fetch turns a PDF into that envelope
before the loader sees it, because the loader does not store PDF bytes.
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import os
import re
from datetime import date
from typing import Any
from urllib.parse import unquote, urljoin

from agents.zenas.pool.adapters.za_common import pause, reject_credential_url
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

_NEUTRAL = re.compile(r"\[(\d{4})\]\s+(ZA[A-Z]{2,8})\s+(\d+)")
_LONG_DATE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
# The body scan stays narrow so a commercial judgment that cites a criminal
# case in its opening is kept. Titles and file names also match "The State vs",
# a mid-title "S v", and the DPP and NPA initials.
_CRIMINAL = re.compile(
    r"(^\s*s\s+v\s+\S|\bv\s+the\s+state\b|\bv\s+s\b|\bthe\s+state\s+vs?\.?\s+\S|"
    r"\bdirector of public prosecutions\b|\bnational prosecuting authority\b|"
    r"criminal appeal|criminal trial)",
    re.I,
)
_CRIMINAL_TITLE = re.compile(
    r"(^\s*s\s+v\s+\S|(?:^|[\s\[\(])s\s+v\s+\S|\bthe\s+state\s+vs?\.?\s+\S|"
    r"\bv\s+the\s+state\b|\bv\s+s\b|\bdpp\b|\bnpa\b|"
    r"\bdirector of public prosecutions\b|\bnational prosecuting authority\b|"
    r"criminal appeal|criminal trial)",
    re.I,
)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def max_docs() -> int:
    return env_int("ZA_CASE_MAX_DOCS", 8)


async def case_pause(env_name: str) -> None:
    """Per-source interval when set, otherwise ZA_CASE_MIN_INTERVAL (default 1s)."""
    if os.environ.get(env_name, "").strip():
        await pause(env_name, "0")
        return
    await pause("ZA_CASE_MIN_INTERVAL", "1")


def absolute_url(base: str, href: str) -> str:
    return urljoin(base, html.unescape(href.strip()))


def neutral_citation(text: str) -> str:
    match = _NEUTRAL.search(text or "")
    if not match:
        return ""
    return f"[{match.group(1)}] {match.group(2)} {match.group(3)}"


# A judgment cites other cases. Only the judgment's own neutral citation names it:
# it sits in the header (before paragraph [1] or the JUDGMENT heading) and carries
# this court's own code. A citation found further down belongs to a cited case.
_CAPTION_START = re.compile(r"in\s+the\s+(?:matter|appeal)\s+(?:between|of)\s*:?", re.I)
_PARA_ONE = (
    re.compile(r"\n\s*\[\s*1\s*\]"),
    re.compile(r"\n\s*1\.?[ \t]+[A-Z][a-z]+[ \t]+[a-z]"),
)
# Some courts print JUDGMENT above the caption (the SCA), others between the
# caption and paragraph 1. It ends the header only after the caption.
_JUDGMENT_HEADING = re.compile(r"\n\s*J\s*U\s*D\s*G\s*M\s*E\s*N\s*T\s*:?\s*\n")
_HEAD_MAX = 4000
_SLUG_NEUTRAL = re.compile(r"\b((?:19|20)\d{2})\s+(za[a-z]{2,8})\s+(\d{1,4})\b", re.I)
_CITATION_LINE = re.compile(r"neutral\s+citation", re.I)


def judgment_head(text: str) -> str:
    """The header of a judgment: everything before paragraph 1 (or a JUDGMENT heading below the caption)."""
    body = text or ""
    cut = min(len(body), _HEAD_MAX)
    for pattern in _PARA_ONE:
        found = pattern.search(body)
        if found and found.start() > 0:
            cut = min(cut, found.start())
    caption = _CAPTION_START.search(body[:cut])
    if caption:
        found = _JUDGMENT_HEADING.search(body, caption.end())
        if found:
            later = _CITATION_LINE.search(body, found.end(), cut)
            if not later:
                cut = min(cut, found.start())
    return body[:cut]


def _code_ok(code: str, codes) -> bool:
    code = code.upper()
    if callable(codes):
        return bool(codes(code))
    return code in {str(item).upper() for item in codes}


def own_citation(text: str, codes, *, url: str = "", title: str = "") -> str:
    """This judgment's own neutral citation, with this court's code, or "".

    Looked for in the listing title, then the file name, then the judgment header.
    Never in the body, where the citations belong to the cases the court cites.
    """
    for place in (title or "", unquote(url or ""), judgment_head(text)):
        for match in _NEUTRAL.finditer(place):
            if _code_ok(match.group(2), codes):
                return f"[{match.group(1)}] {match.group(2)} {match.group(3)}"
    slug = slug_title(url) if url else ""
    for match in _SLUG_NEUTRAL.finditer(slug):
        if _code_ok(match.group(2), codes):
            return f"[{match.group(1)}] {match.group(2).upper()} {match.group(3)}"
    return ""


_SLUG_CASE_NO = (
    re.compile(r"\b([a-z]{1,5}\d{1,6}[a-z]?)\s+((?:19|20)\d{2})\b", re.I),
    re.compile(r"\b((?:19|20)\d{2})\s+(\d{3,7})\b"),
)


def slug_case_number(url: str) -> str:
    """A case number from a court's file name, e.g. 'ja13 2016' -> 'JA13/2016'."""
    slug = slug_title(url) if url else ""
    found = _SLUG_CASE_NO[0].search(slug)
    if found:
        return f"{found.group(1).upper()}/{found.group(2)}"
    found = _SLUG_CASE_NO[1].search(slug)
    if found:
        return f"{found.group(2)}/{found.group(1)}"
    return ""


_HEAD_CASE_NO = re.compile(
    r"case\s*(?:no|number)\b\.?\s*:?\s*([A-Z]{0,6}\s?\d[\w]*(?:\s?/\s?[\w]+){1,3}(?: \d{2}\b)?)", re.I
)


# A header line that starts with a case label, even one a PDF broke ("CAS\nE NO:"),
# followed by a number with no slash ("IM238JAN19", "(019513)", "2025 – 154050").
_LINE_CASE_NO = re.compile(
    r"^[ \t]*(?:(?:ct|cc|cct|cac|lcc)[ \t]+)?c\s?a\s?s\s?e[ \t]*(?:n\s?o\s?s?|numbers?)?[ \t]*[.:]*[ \t]*\(?"
    r"((?:[A-Z]{1,6}[ \t]?)?\d[\w]*(?:[ \t]?[/\u2013-][ \t]?\w+){0,3})",
    re.I | re.M,
)


def header_case_number(text: str) -> str:
    """The case number printed in the judgment header ("Case No: LCC 16/2020"), or ""."""
    head = judgment_head(text)
    found = _HEAD_CASE_NO.search(head) or _LINE_CASE_NO.search(head)
    if not found:
        return ""
    number = " ".join(found.group(1).split())
    number = re.sub(r"\s*[/]\s*", "/", number)
    number = re.sub(r"\s*[\u2013]\s*|\s+-\s+", "-", number)
    return number.upper()[:40]


def court_citation(url: str, text: str, codes, label: str, *, title: str = "") -> str:
    """This court's own neutral citation, else "<label> <case number>", else "" (skip the file)."""
    own = own_citation(text, codes, url=url, title=title)
    if own:
        return own
    number = slug_case_number(url) or header_case_number(text)
    return f"{label} {number}" if number else ""


_CAPTION_END = re.compile(
    r"^\s*(?:coram|heard|delivered|decided|judgment|j\s*u\s*d\s*g|summary|neutral\s+citation|order\b|"
    r"in\s+re\b|arising|this\s+judgment|date|case\s+no|ct\s+case|cac\s+case|reasons|_{3,}|\[\s*1\s*\])",
    re.I,
)
_CAPTION_SPLIT = re.compile(r"^\s*(?:and|versus|vs?\.?)\s*$", re.I)
_ROLE = re.compile(
    r"\s*\b(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\s+)?"
    r"(?:applicants?|appellants?|respondents?|plaintiffs?|defendants?|excipients?|"
    r"intervening\s+part(?:y|ies)|intervenors?|amicus\s+curiae|complainants?|"
    r"(?:primary\s+)?(?:acquiring|target)\s+firms?)\b.*$",
    re.I,
)
_JOINS_NEXT = re.compile(r"(?:\b(?:of|and|the|for|south|in)|&|,|\(pty\)|\(proprietary\)|-)\s*$", re.I)
_CASE_LINE = re.compile(r"^\s*(?:(?:cac\s+|ct\s+|cct\s+)?case\b|cct\b|cac\b|tribunal\s+case|reportable|of\s+interest|revised)", re.I)
_CONTINUES = re.compile(r"^\s*(?:\(|(?i:ltd|limited|proprietary|t/a)\b|[a-z])")
_KEEP_UPPER = frozenset({"CC", "NPC", "SOC", "SA", "RF", "NO", "CCMA", "SARS", "NERSA", "FAWU", "HCI", "CEO", "MEC"})
_SMALL = frozenset({"of", "and", "the", "for", "in", "on", "v", "a", "an", "to", "t/a"})


def _tidy_party_case(name: str) -> str:
    letters = [ch for ch in name if ch.isalpha()]
    if not letters or sum(ch.isupper() for ch in letters) < 0.8 * len(letters):
        return name
    words = []
    for index, word in enumerate(name.split()):
        bare = word.strip("(),.&'’")
        if bare.upper() in _KEEP_UPPER:
            words.append(word.upper())
        elif index and word.lower() in _SMALL:
            words.append(word.lower())
        else:
            words.append(re.sub(r"[A-Za-z][A-Za-z'’]*", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), word))
    return " ".join(words)


def _first_party(lines: list[str]) -> tuple[str, bool]:
    """The first party in a caption side, and whether more parties follow it."""
    parts: list[str] = []
    rest_at = len(lines)
    for index, raw in enumerate(lines):
        line = " ".join(raw.split())
        if not line or _CASE_LINE.match(line):
            continue
        stripped = _ROLE.sub("", line).strip()
        if parts and not (_JOINS_NEXT.search(parts[-1]) or _CONTINUES.match(line)):
            rest_at = index
            break
        if stripped:
            parts.append(stripped)
        if stripped != line and not _JOINS_NEXT.search(stripped):
            rest_at = index + 1
            break
    rest = any(
        _ROLE.sub("", " ".join(raw.split())).strip() and not _CONTINUES.match(raw.strip())
        for raw in lines[rest_at:]
    )
    name = " ".join(parts).strip(" ,;:")
    return name, rest


def caption_title(text: str) -> str:
    """'Party v Party' from the judgment's own caption ("In the matter between ... and ..."), or ""."""
    head = judgment_head(text)
    start = _CAPTION_START.search(head)
    if not start:
        return ""
    lines = head[start.end():].split("\n")[:60]
    block: list[str] = []
    for line in lines:
        if block and _CAPTION_END.match(line):
            break
        if _CAPTION_START.match(line.strip()):
            continue
        block.append(line)
    split = next((i for i, line in enumerate(block) if _CAPTION_SPLIT.match(line)), None)
    if split is None:
        return ""
    left, left_more = _first_party(block[:split])
    right, right_more = _first_party(block[split + 1 :])
    if not left or not right:
        return ""
    left = _tidy_party_case(left) + (" and Others" if left_more and "other" not in left.lower() else "")
    right = _tidy_party_case(right) + (" and Others" if right_more and "other" not in right.lower() else "")
    return f"{left} v {right}"[:200]


# Neutral-citation codes. Seat is the one the code names. The judgment's own
# words win when they name the same place; a disagreement leaves the field unknown.
_CODE_COURT: dict[str, tuple[str, str, str]] = {
    "ZACC": ("Constitutional Court", "", ""),
    "ZASCA": ("Supreme Court of Appeal", "", ""),
    "ZAGPPHC": ("High Court", "Gauteng Division", "Pretoria"),
    "ZAGPJHC": ("High Court", "Gauteng Division", "Johannesburg"),
    "ZAWCHC": ("High Court", "Western Cape Division", "Cape Town"),
    "ZAKZDHC": ("High Court", "KwaZulu-Natal Division", "Durban"),
    "ZAKZPHC": ("High Court", "KwaZulu-Natal Division", "Pietermaritzburg"),
    "ZAECGHC": ("High Court", "Eastern Cape Division", "Makhanda"),
    "ZAECPEHC": ("High Court", "Eastern Cape Division", "Gqeberha"),
    "ZAECMHC": ("High Court", "Eastern Cape Division", "Mthatha"),
    "ZAECELLC": ("High Court", "Eastern Cape Local Division", "East London"),
    "ZAECBHC": ("High Court", "Eastern Cape Division", "Bhisho"),
    "ZAFSHC": ("High Court", "Free State Division", "Bloemfontein"),
    "ZANWHC": ("High Court", "North West Division", "Mahikeng"),
    "ZALMPPHC": ("High Court", "Limpopo Division", "Polokwane"),
    "ZALMPTHC": ("High Court", "Limpopo Division", "Thohoyandou"),
    "ZAMPMBHC": ("High Court", "Mpumalanga Division", "Mbombela"),
    "ZAMPMHC": ("High Court", "Mpumalanga Division", "Middelburg"),
    "ZANCHC": ("High Court", "Northern Cape Division", "Kimberley"),
    "ZALAC": ("Labour Appeal Court", "", ""),
    "ZALCJHB": ("Labour Court", "Johannesburg", "Johannesburg"),
    "ZALCCT": ("Labour Court", "Cape Town", "Cape Town"),
    "ZALCD": ("Labour Court", "Durban", "Durban"),
    "ZALCPE": ("Labour Court", "Gqeberha", "Gqeberha"),
    "ZACAC": ("Competition Appeal Court", "", ""),
    "ZACT": ("Competition Tribunal", "", ""),
    "ZALCC": ("Land Claims Court", "", ""),
    "ZAST": ("Special Tribunal", "", ""),
    "ZATC": ("Tax Court", "", ""),
}
_SEAT_ALIAS = {
    "grahamstown": "makhanda",
    "port elizabeth": "gqeberha",
    "pietermaritzburg": "pietermaritzburg",
}
_REPORT = re.compile(
    r"\b\d{4}\s+\(\d+\)\s+(?:SA|BCLR|All SA)\s+\d+\s+\([A-Za-z]{2,8}\)"
)
_DIVISION_LINE = re.compile(
    r"(?i)\b("
    r"eastern cape local|eastern cape|gauteng|western cape|kwazulu-natal|"
    r"free state|north west|limpopo|mpumalanga|northern cape"
    r")\s+division(?:\s*,\s*|\s+)([A-Za-z][A-Za-z .'\-]{1,40})?"
)
_LISTING_DIVISION = re.compile(
    r"(?i)^(eastern cape|gauteng|western cape|kwazulu-natal|free state|"
    r"north west|limpopo|mpumalanga|northern cape)\s+division of the high court$"
)
_SUFFIX = r"(?:AJJA|AJA|ADJP|DJP|DCJ|JJA|JA|AJ|JJ|JP|CJ|J)"
_JUDGE_LINE = re.compile(
    rf"^(?P<name>[A-Z][A-Za-z .'\-]{{1,80}}?)(?:,\s*|\s+)(?P<suf>{_SUFFIX})\s*:?\s*$"
)
_PARA_NO = re.compile(r"\[(\d+)\](?!\s*ZA[A-Z])")


def _tidy(value: str) -> str:
    return " ".join((value or "").split())


def _place_key(value: str) -> str:
    token = _tidy(value).lower().replace("-", " ")
    return _SEAT_ALIAS.get(token, token)


def _same_place(left: str, right: str) -> bool:
    if not left or not right:
        return True
    return _place_key(left) == _place_key(right)


def _pick(sources: list[str]) -> str:
    """The first stated value, unless a later source names a different place."""
    chosen = ""
    for raw in sources:
        text = _tidy(raw)
        if not text:
            continue
        if not chosen:
            chosen = text
            continue
        if not _same_place(chosen, text):
            return ""
    return chosen


def _header_court(text: str) -> tuple[str, str, str]:
    """Court, division and seat from the judgment heading. Empty when unstated."""
    head = text[:6000]
    flat = _tidy(head)
    upper = flat.upper()
    court = ""
    division = ""
    seat = ""
    if "CONSTITUTIONAL COURT" in upper:
        court = "Constitutional Court"
    elif "SUPREME COURT OF APPEAL" in upper:
        court = "Supreme Court of Appeal"
    elif "LABOUR APPEAL COURT" in upper:
        court = "Labour Appeal Court"
        found = re.search(r"(?i)labour appeal court of south africa,\s*([A-Za-z][A-Za-z ]{2,40})", flat)
        if found:
            seat = re.split(r"(?i)\b(not|case|coram|in)\b", found.group(1))[0]
            seat = _tidy(seat).title()
    elif "COMPETITION APPEAL COURT" in upper:
        court = "Competition Appeal Court"
    elif "COMPETITION TRIBUNAL" in upper:
        court = "Competition Tribunal"
    elif "LAND CLAIMS COURT" in upper:
        court = "Land Claims Court"
    elif re.search(r"\bTAX COURT\b", upper):
        court = "Tax Court"
    elif "SPECIAL TRIBUNAL" in upper:
        court = "Special Tribunal"
    elif "LABOUR COURT" in upper:
        court = "Labour Court"
    elif "HIGH COURT" in upper:
        court = "High Court"
    found = _DIVISION_LINE.search(flat)
    if found and court in {"High Court", ""}:
        division = f"{found.group(1).title()} Division"
        if found.group(1).lower().startswith("eastern cape local"):
            division = "Eastern Cape Local Division"
        tail = _tidy(found.group(2) or "")
        tail = re.split(r"(?i)\b(case|reportable|not|in the matter|held)\b", tail)[0]
        tail = tail.strip(" ,.;:-")
        if tail and tail.lower() not in {"of the high court", "of south africa"}:
            seat = tail.title() if tail.isupper() or tail[:1].isupper() else tail
            if seat.isupper():
                seat = seat.title()
    return court, division, _tidy(seat)


def _listing_court(listing: str) -> tuple[str, str, str]:
    """Court and division from an official listing label. A generic label is ignored."""
    text = _tidy(listing)
    if not text or text.lower() in {"south african court", "government of south africa"}:
        return "", "", ""
    found = _LISTING_DIVISION.match(text)
    if found:
        return "High Court", f"{found.group(1).title()} Division", ""
    known = {
        "constitutional court": ("Constitutional Court", "", ""),
        "supreme court of appeal": ("Supreme Court of Appeal", "", ""),
        "competition appeal court": ("Competition Appeal Court", "", ""),
        "competition tribunal": ("Competition Tribunal", "", ""),
        "labour appeal court": ("Labour Appeal Court", "", ""),
        "labour court": ("Labour Court", "", ""),
        "land claims court": ("Land Claims Court", "", ""),
        "land court": ("Land Court", "", ""),
        "tax court": ("Tax Court", "", ""),
        "special tribunal": ("Special Tribunal", "", ""),
        "high court": ("High Court", "", ""),
    }
    return known.get(text.lower(), ("", "", ""))


def _caption(text: str) -> str:
    """The heading, before the reasons. A citation in the reasons is another case."""
    labelled = re.search(r"(?is)neutral citation\s*:.{0,500}", (text or "")[:8000])
    if labelled:
        return labelled.group(0)
    cut = re.search(r"(?i)\bjudgment\b", (text or "")[:8000])
    if cut:
        return text[: cut.start()]
    return (text or "")[:2500]


def _code_court(text: str) -> tuple[str, str, str, str]:
    match = _NEUTRAL.search(_caption(text or ""))
    if not match:
        return "", "", "", ""
    code = match.group(2).upper()
    citation = f"[{match.group(1)}] {code} {match.group(3)}"
    court, division, seat = _CODE_COURT.get(code, ("", "", ""))
    return court, division, seat, citation


def _split_judges(coram: str) -> list[str]:
    text = re.sub(r"(?i)^coram:\s*", "", _tidy(coram))
    text = re.sub(r"(?i)\bet\b", "and", text)
    parts = [part.strip(" ,") for part in re.split(r"\s+and\s+|,\s*", text) if part.strip(" ,")]
    names = []
    for part in parts:
        if re.fullmatch(r"(?i)coram:?", part):
            continue
        if not re.search(r"[A-Za-z]", part):
            continue
        names.append(_tidy(part))
    return names


def _coram(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"(?i)\s*coram\s*:", line):
            continue
        block = [line]
        for follow in lines[index + 1 : index + 8]:
            stripped = follow.strip()
            if not stripped:
                continue
            if re.match(r"(?i)^(heard|delivered|summary|judgment|order|on appeal|neutral)\b", stripped):
                break
            if len(stripped) > 180:
                break
            more = bool(
                re.match(r"(?i)^(and|et)\b", stripped)
                or re.search(rf"(?i)\b{_SUFFIX}\b", stripped)
                or "," in stripped
            )
            if not more:
                break
            block.append(stripped)
        return " ".join(block)
    return ""


def _writing_judge(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines[:120]):
        if _tidy(line).upper() != "JUDGMENT":
            continue
        for follow in lines[index + 1 : index + 15]:
            stripped = follow.strip()
            if not stripped:
                continue
            if re.fullmatch(r"[\d_]+", stripped) or set(stripped) <= {"_", " "}:
                continue
            found = _JUDGE_LINE.match(stripped)
            if found:
                return _tidy(stripped).rstrip(":")
            return ""
    return ""


def _appeal(text: str) -> tuple[bool | None, str]:
    head = text[:8000]
    found = re.search(r"(?is)on appeal from\s+(.+?):", head)
    appeal_from = ""
    is_appeal: bool | None = None
    if found:
        appeal_from = _tidy(found.group(1))
        is_appeal = True
    if re.search(r"(?i)\bthis is an appeal\b|\bAPPELLANT\b", head[:4000]):
        is_appeal = True
    return is_appeal, appeal_from


def court_identity(text: str, listing: str = "") -> dict[str, Any]:
    """Court, division, seat, bench and citations. Unknown stays empty or null.

    Values come from the judgment and, when given, its official listing.
    A field the two sources name differently is left unknown.
    """
    code_court, code_division, code_seat, citation = _code_court(text or "")
    head_court, head_division, head_seat = _header_court(text or "")
    list_court, list_division, list_seat = _listing_court(listing)
    judges = _split_judges(_coram(text or ""))
    if not judges:
        one = _writing_judge(text or "")
        judges = [one] if one else []
    report = ""
    report_found = _REPORT.search(_caption(text or ""))
    if report_found:
        report = _tidy(report_found.group(0))
    is_appeal, appeal_from = _appeal(text or "")
    return {
        "court": _pick([head_court, code_court, list_court]),
        "division": _pick([head_division, code_division, list_division]),
        "seat": _pick([head_seat, code_seat, list_seat]),
        "bench_size": len(judges) or None,
        "judges": ", ".join(judges),
        "neutral_citation": citation,
        "report_citation": report,
        "appeal": is_appeal,
        "appeal_from": appeal_from,
    }


def parse_date(text: str) -> date | None:
    iso = _ISO_DATE.search(text or "")
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    match = _LONG_DATE.search(text or "")
    if not match:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def is_criminal_caption(title: str, text: str = "") -> bool:
    """Drop criminal matters. The check reads the caption, not the whole reasons."""
    head = title or ""
    opening = (text or "")[:600]
    return bool(_CRIMINAL_TITLE.search(head) or _CRIMINAL.search(opening))


def slug_title(url: str) -> str:
    raw = unquote(url.split("#", 1)[0])
    if "download=" in raw:
        raw = raw.split("download=", 1)[1]
        raw = raw.split(":", 1)[-1]
    else:
        raw = raw.split("?", 1)[0].rstrip("/").split("/")[-1]
    raw = re.sub(r"\.(pdf|txt)$", "", raw, flags=re.I)
    raw = raw.replace("-", " ").replace("_", " ").replace("%20", " ")
    return " ".join(raw.split())[:180]


def pdf_text(blob: bytes, *, max_pages: int | None = None) -> str:
    """The whole PDF. A file that is not a PDF yields nothing. Pages are not cut.

    ``max_pages`` is accepted so older callers still run. It is not applied.
    """
    del max_pages
    if not blob.startswith(b"%PDF"):
        return ""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(blob))
        parts = [(page.extract_text() or "") for page in reader.pages]
    except (PdfReadError, OSError, ValueError, KeyError, TypeError):
        return ""
    return "\n".join(part for part in parts if part).strip()


def case_bytes(
    *,
    citation: str,
    title: str,
    body: str,
    url: str,
    text: str,
    doc_date: date | None,
) -> bytes:
    citation = " ".join((citation or "").split())
    title = " ".join((title or "").split()) or citation
    text = (text or "").strip()
    if not citation or not text or is_criminal_caption(title, text):
        return b""
    payload = {
        "citation": citation,
        "title": title[:300],
        "body": body,
        "url": url,
        "text": text,
        "date": doc_date.isoformat() if doc_date else "",
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def parse_case_blob(blob: bytes, source: SourceSpec) -> list[LegalDocument]:
    raw = blob.strip()
    if not raw or raw.startswith(b"%PDF") or raw.startswith(b"<"):
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    citation = " ".join(str(data.get("citation") or "").split())
    text = str(data.get("text") or "").strip()
    title = " ".join(str(data.get("title") or "").split()) or citation
    if not citation or not text or is_criminal_caption(title, text):
        return []
    when = parse_date(str(data.get("date") or ""))
    paragraphs = [piece.strip() for piece in re.split(r"\n\s*\n", text) if piece.strip()]
    if not paragraphs:
        paragraphs = [text]
    listing = " ".join(str(data.get("body") or "").split())
    identity = court_identity(text, listing=listing)
    court = listing or "South African court"
    page = " ".join(str(data.get("url") or "").split()) or source.canonical_url
    sections = [Section("court", "Court", json.dumps(identity, ensure_ascii=False), [])]
    sections.extend(Section("", title, piece, []) for piece in paragraphs)
    return [
        build_document(
            source,
            citation=citation,
            title=title,
            level="national",
            kind="case",
            body=court,
            sections=sections,
            url=page,
            status="published",
            doc_date=when,
            effective_from=when,
            text=text,
        )
    ]


def after_cursor(urls: list[str], resume: str) -> list[str]:
    ordered = list(dict.fromkeys(urls))
    if not resume:
        return ordered
    if resume in ordered:
        return ordered[ordered.index(resume) + 1 :]
    return [url for url in ordered if url > resume]


def phoca_downloads(page: bytes, page_url: str) -> list[str]:
    """Phoca Download file links. The query is `download=<id>:<slug>` and the response is a PDF."""
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"""href=["']([^"']*?download=\d+:[^"']+)["']""", text, re.I):
        url = absolute_url(page_url, match.group(1)).split("#", 1)[0]
        if "court-rolls" in url.lower() or "courtr-olls" in url.lower():
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


async def pdfs_to_cases(
    http_get,
    urls: list[str],
    *,
    body: str,
    interval_env: str,
    citation_for,
    title_for=None,
) -> list[bytes]:
    """Download PDFs and return JSON envelopes. One bad file does not stop the wave."""
    blobs: list[bytes] = []
    for url in urls:
        reject_credential_url(url)
        try:
            raw = await http_get(url, _pdf_headers())
        except OSError:
            await case_pause(interval_env)
            continue
        await case_pause(interval_env)
        # pypdf is CPU-bound: parse off the event loop so the room keeps answering.
        text = await asyncio.to_thread(pdf_text, raw)
        if not text:
            continue
        citation = citation_for(url, text)
        if not citation:
            continue
        title = caption_title(text) or slug_title(url)
        if title_for is not None:
            title = title_for(url, text) or title
        when = parse_date(text[:800]) or parse_date(title)
        payload = case_bytes(
            citation=citation,
            title=title or citation,
            body=body,
            url=url.split("?")[0] if "download=" not in url else url,
            text=text,
            doc_date=when,
        )
        if payload:
            blobs.append(payload)
    return blobs


def _pdf_headers() -> dict[str, str]:
    from agents.zenas.pool.adapters.za_common import pdf_headers

    return pdf_headers()


def citation_or_neutral(url: str, text: str, fallback) -> str:
    found = neutral_citation(text) or neutral_citation(unquote(url))
    if found:
        return found
    return fallback(url, text)
