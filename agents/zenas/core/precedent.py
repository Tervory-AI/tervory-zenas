"""South African precedent weight. The rules are data, and they are still a draft.

``precedent_rules.json`` is the file the founder edits. This module does not
add a rule of its own. US weight is out of scope: a court this file does not
name simply matches no rule.
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

_RULES_PATH = Path(__file__).with_name("precedent_rules.json")
_DRAFT_LABEL = "precedent rules: draft"

_DIVISIONS = (
    "Gauteng Division",
    "Western Cape Division",
    "Eastern Cape Division",
    "KwaZulu-Natal Division",
    "Free State Division",
    "North West Division",
    "Limpopo Division",
    "Mpumalanga Division",
    "Northern Cape Division",
)
_COURTS = (
    "Constitutional Court",
    "Supreme Court of Appeal",
    "High Court",
    "Labour Appeal Court",
    "Labour Court",
    "Competition Appeal Court",
    "Competition Tribunal",
    "Tax Court",
    "Land Claims Court",
    "Land Court",
    "Special Tribunal",
    "Regional Court",
    "Magistrates' Court",
)
_SEATS = (
    "Pretoria",
    "Johannesburg",
    "Cape Town",
    "Durban",
    "Pietermaritzburg",
    "Makhanda",
    "Grahamstown",
    "Bloemfontein",
    "Mahikeng",
    "Polokwane",
    "Mbombela",
    "Kimberley",
    "Gqeberha",
    "Mthatha",
    "East London",
    "Bhisho",
    "Thohoyandou",
    "Middelburg",
)
_BENCH = {
    "single judge": 1,
    "two-judge bench": 2,
    "two judges": 2,
    "full bench": 3,
}
_CASE_NAME = re.compile(
    r"\b([A-Z][A-Za-z0-9&'’.\-]*(?:\s+(?:\([^)]+\)|[A-Z][A-Za-z0-9&'’.\-]*)){0,8}"
    r"\s+v\.?\s+"
    r"[A-Z][A-Za-z0-9&'’.\-]*(?:\s+(?:\([^)]+\)|[A-Z][A-Za-z0-9&'’.\-]*)){0,8})"
)
_NEUTRAL = re.compile(r"\[\d{4}\]\s+ZA[A-Z]{2,8}\s+\d+", re.I)


@lru_cache(maxsize=1)
def load_rules() -> tuple[dict[str, Any], ...]:
    data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return ()
    return tuple(rule for rule in rules if isinstance(rule, dict))


def rules_are_draft() -> bool:
    rules = load_rules()
    return not rules or any(str(rule.get("status") or "") == "draft" for rule in rules)


def draft_label() -> str:
    return _DRAFT_LABEL if rules_are_draft() else ""


def parse_forum(forum: str) -> dict[str, Any]:
    """A lawyer's forum, for example 'Western Cape Division, single judge'.

    A word this list does not know is left unknown. Nothing is guessed.
    """
    text = " ".join((forum or "").split())
    if not text:
        return _blank_court()
    if text in _COURTS:
        return {**_blank_court(), "court": text}
    parts = [part.strip() for part in text.split(",") if part.strip()]
    court = ""
    division = ""
    seat = ""
    bench: int | None = None
    for part in parts:
        bench_size = _BENCH.get(part.lower())
        if bench_size is not None:
            bench = bench_size
            continue
        if part in _DIVISIONS:
            court = "High Court"
            division = part
            continue
        if part in _SEATS:
            seat = part
            continue
        if part in _COURTS:
            court = part
            continue
    return {"court": court, "division": division, "seat": seat, "bench_size": bench}


def _blank_court() -> dict[str, Any]:
    return {"court": "", "division": "", "seat": "", "bench_size": None}


def _as_court(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return parse_forum(value)
    if not isinstance(value, dict):
        return _blank_court()
    bench = value.get("bench_size", value.get("bench"))
    if bench is None or bench == "":
        bench_size = None
    else:
        try:
            bench_size = int(bench)
        except (TypeError, ValueError):
            bench_size = None
    return {
        "court": " ".join(str(value.get("court") or "").split()),
        "division": " ".join(str(value.get("division") or "").split()),
        "seat": " ".join(str(value.get("seat") or "").split()),
        "bench_size": bench_size,
    }


def _same_division(citing: dict[str, Any], cited: dict[str, Any]) -> bool:
    left = citing.get("division") or ""
    right = cited.get("division") or ""
    return bool(left) and left == right


def rule_matches(rule: dict[str, Any], citing: dict[str, Any], cited: dict[str, Any]) -> bool:
    """True when this one rule's conditions fit. A flag rule is not a weight."""
    if str(rule.get("kind") or "") == "flag":
        return False
    cited_court = str(rule.get("cited_court") or "")
    if cited_court and cited.get("court") != cited_court:
        return False
    citing_court = str(rule.get("citing_court") or "")
    if citing_court and citing.get("court") != citing_court:
        return False
    cited_in = rule.get("cited_court_in")
    if isinstance(cited_in, list) and cited.get("court") not in cited_in:
        return False
    citing_in = rule.get("citing_court_in")
    if isinstance(citing_in, list) and citing.get("court") not in citing_in:
        return False
    citing_not = rule.get("citing_court_not_in")
    if isinstance(citing_not, list) and citing.get("court") in citing_not:
        return False
    if "cited_bench" in rule and cited.get("bench_size") != rule.get("cited_bench"):
        return False
    if "citing_bench" in rule and citing.get("bench_size") != rule.get("citing_bench"):
        return False
    if "cited_bench_min" in rule:
        size = cited.get("bench_size")
        if size is None or size < int(rule["cited_bench_min"]):
            return False
    if "citing_bench_max" in rule:
        size = citing.get("bench_size")
        if size is None or size > int(rule["citing_bench_max"]):
            return False
    same = _same_division(citing, cited)
    if rule.get("same_division") and not same:
        return False
    if rule.get("different_division"):
        left = citing.get("division") or ""
        right = cited.get("division") or ""
        if not left or not right or left == right:
            return False
    if rule.get("benches_known"):
        if citing.get("bench_size") is None or cited.get("bench_size") is None:
            return False
    if rule.get("same_court"):
        left = str(citing.get("court") or "")
        right = str(cited.get("court") or "")
        if not left or left != right:
            return False
    return True


def matching_rules(citing_court: Any, cited_case: Any) -> list[dict[str, Any]]:
    citing = _as_court(citing_court)
    cited = _as_court(cited_case)
    return [rule for rule in load_rules() if rule_matches(rule, citing, cited)]


def weight(citing_court: Any, cited_case: Any) -> dict[str, Any]:
    """binding, persuasive, or not_precedent, plus the rule's own reason.

    The first matching rule in the data file wins. When none matches, the
    answer is not_precedent and the case is not offered. That is unknown,
    not a guess that the case binds.
    """
    found = matching_rules(citing_court, cited_case)
    if not found:
        return {
            "weight": "not_precedent",
            "reason": "No seeded rule matches these courts.",
            "offer": False,
            "rule_id": "",
            "draft": rules_are_draft(),
        }
    rule = found[0]
    return {
        "weight": str(rule.get("weight") or "not_precedent"),
        "reason": str(rule.get("reason") or ""),
        "offer": bool(rule.get("offer", True)),
        "rule_id": str(rule.get("id") or ""),
        "draft": rules_are_draft(),
    }


def _flag_text() -> str:
    for rule in load_rules():
        if str(rule.get("kind") or "") == "flag" and rule.get("flag"):
            return str(rule["flag"])
    return "conflicting authority"


def conflicting_authority(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Two same-division single judges who disagree. Both are returned."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        court = _as_court(case)
        if court.get("court") != "High Court" or court.get("bench_size") != 1:
            continue
        division = str(court.get("division") or "")
        if not division:
            continue
        groups.setdefault(division, []).append(case)
    conflicts = []
    for division, group in groups.items():
        outcomes = {" ".join(str(item.get("outcome") or "").split()) for item in group}
        outcomes.discard("")
        if len(group) >= 2 and len(outcomes) > 1:
            conflicts.append({"division": division, "cases": group})
    flag = _flag_text() if conflicts else ""
    return {"flag": flag, "groups": conflicts}


def still_good_law(
    cited_case: Any,
    later: list[dict[str, Any]],
    *,
    as_at: str | None = None,
) -> str:
    """Warnings from later library judgments, and what was checked.

    The check is only the later judgments handed in. It is not a complete search.
    """
    cited = _as_court(cited_case)
    warnings: list[str] = []
    for item in later:
        treatment = " ".join(str(item.get("treatment") or "").split()).lower()
        name = " ".join(str(item.get("case") or item.get("citation") or "").split()) or "a later case"
        paragraph = " ".join(str(item.get("paragraph") or "").split())
        relation = weight(cited, item)
        if treatment == "overruled" and relation["weight"] == "binding":
            para = f" para {paragraph}" if paragraph else ""
            warnings.append(f"Overruled by {name}{para}")
        elif treatment == "not followed" and relation["weight"] == "persuasive":
            warnings.append(f"Not followed by {name}")
    when = as_at or date.today().isoformat()
    checked = f"checked against {len(later)} later judgments in the library, as at {when}"
    return " ".join([*warnings, checked])


def _tidy_case_name(name: str) -> str:
    """Drop a leading 'In ' so 'In Smith v Jones' is the case, not a sentence start."""
    return re.sub(r"^In\s+", "", " ".join((name or "").split()))


def named_cases(text: str) -> list[str]:
    """Neutral citations and 'Name v Name' cases written in this text."""
    found: list[str] = []
    for pattern in (_NEUTRAL, _CASE_NAME):
        for match in pattern.finditer(text or ""):
            name = _tidy_case_name(match.group(0))
            if name and name not in found:
                found.append(name)
    return found


_PARTY_SKIP = frozenset(
    {
        "the",
        "another",
        "others",
        "and",
        "ltd",
        "limited",
        "pty",
        "inc",
        "plc",
        "proprietary",
        "company",
        "cc",
        "npc",
    }
)


def _party_heads(name: str) -> tuple[str, str] | None:
    """First meaningful word on each side of 'v'. 'Grancy v Gihwala' matches the long form."""
    cleaned = _NEUTRAL.sub(" ", name or "")
    cleaned = _tidy_case_name(cleaned)
    parts = re.split(r"\s+v\.?\s+", cleaned, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return None

    def head(side: str) -> str:
        for word in re.findall(r"[A-Za-z][A-Za-z0-9'’\-]*", side):
            token = word.lower().rstrip(".")
            if token not in _PARTY_SKIP:
                return token
        return ""

    left, right = head(parts[0]), head(parts[1])
    if not left or not right:
        return None
    return left, right


def _norm_cite(citation: str) -> str:
    return " ".join((citation or "").split()).lower()


def drop_unverified_cases(answer: str, known: list[str]) -> str:
    """Remove a case name or neutral citation that is not in this library.

    A name beside a library citation stays. A short form matches a library case
    name party by party. The client's own matter stays when it is passed in
    among the known names. A true miss keeps the sentence the lawyer sees.
    """
    text = answer or ""
    library = [" ".join(item.split()) for item in known if " ".join(str(item).split())]
    known_cites = {
        _norm_cite(match.group(0))
        for item in library
        for match in _NEUTRAL.finditer(item)
    }
    known_heads = {heads for item in library if (heads := _party_heads(item))}
    known_exact = {_tidy_case_name(item).lower() for item in library}
    # One library case name, not every known string joined together.
    known_case_names = [name for name in known_exact if len(name) <= 240 and _party_heads(name)]

    def citation_known(name: str) -> bool:
        return name.startswith("[") and _norm_cite(name) in known_cites

    def name_known(name: str) -> bool:
        if name.lower() in known_exact:
            return True
        heads = _party_heads(name)
        if heads and heads in known_heads:
            return True
        folded = name.lower()
        if any(folded in blob for blob in known_case_names):
            return True
        for match in re.finditer(re.escape(name), text):
            tail = text[match.end() : match.end() + 48].lstrip()
            cite = _NEUTRAL.match(tail)
            if cite and _norm_cite(cite.group(0)) in known_cites:
                return True
        return False

    missing = []
    for name in named_cases(text):
        if name.startswith("["):
            if not citation_known(name):
                missing.append(name)
        elif not name_known(name):
            missing.append(name)
    if not missing:
        return text
    cleaned = text
    for name in sorted(dict.fromkeys(missing), key=len, reverse=True):
        cleaned = cleaned.replace(name, "")
    cleaned = " ".join(cleaned.split())
    notes = [
        f"I could not verify {name}, so I have left it out. I cannot find it."
        for name in dict.fromkeys(missing)
    ]
    return " ".join([cleaned, *notes]).strip()
