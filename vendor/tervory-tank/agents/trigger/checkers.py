"""Deterministic checks only. No model clients or remote fallback."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
from proselint.checks import __register__
from proselint.registry import CheckRegistry
from proselint.tools import LintFile

CheckRegistry().register_many(__register__)

from .voice import Voice

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "vendor"))
from agent_style.review import detectors_mech, detectors_struct
from agent_style.review.loader import parse_rules

RULES = parse_rules((ROOT / "vendor/agent_style/RULES.md").read_text())
MECHANICAL = {"RULE-05", "RULE-06", "RULE-12", "RULE-B", "RULE-D"}
STRUCTURAL = {"RULE-A", "RULE-C", "RULE-E"}
SKIPPED = [r.id for r in RULES if r.id not in MECHANICAL | STRUCTURAL]
# Literal candidates from each book's Avoid section. These flag review candidates;
# a code checker cannot decide whether the surrounding context justifies a phrase.
BOOK_TERMS = {
    "18f-content": ["easy", "simple", "obvious"],
    "apple-interface-writing": ["click here", "OK"],
    "github-docs": ["this", "that", "it"],
    "gitlab-docs": ["allow", "enable", "easy", "easily", "simple", "obvious"],
    "google-developer-docs": ["simply", "easily", "obviously", "allows you to", "please"],
    "govuk": ["please", "simply", "easy", "obvious"],
    "kubernetes-docs": ["new", "currently", "soon", "just", "simply", "easy"],
    "microsoft-writing-style": ["please"],
}


def phrase_spans(text: str, phrase: str):
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(p) for p in phrase.split()) + r"(?!\w)"
    return list(re.finditer(pattern, text, re.IGNORECASE))


def local_checks(text: str, voice: Voice | None, excluded: frozenset[str] = frozenset()) -> dict:
    issues = []
    for found in ([] if "proselint" in excluded else LintFile("input", text).lint()):
        item = found.into_dict()
        issues.append({"source": "proselint", "rule": item["check_path"], "message": item["message"],
                       "line": found.pos[0], "column": found.pos[1]})
    for rule in RULES:
        if ("readability" if rule.id == "RULE-12" else "agent_style") in excluded:
            continue
        if rule.id == "RULE-12" and voice and voice.maxSentenceWords != 30:
            continue
        module = detectors_mech if rule.id in MECHANICAL else detectors_struct if rule.id in STRUCTURAL else None
        if module is None:
            continue
        result = module.run(rule, text, "input.md")
        issues.extend({"source": "agent-style", "rule": v.rule, "line": v.line,
                       "column": v.column, "message": v.detail} for v in result.violations)
    if "readability" not in excluded:
        # Passive-voice candidates, not a semantic verdict about whether passive is appropriate.
        for match in re.finditer(r"\b(?:am|is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(?:\w+ed|written|given|made|seen|known|taken|built|sent|shown|found|done)\b", text, re.IGNORECASE):
            issues.append({"source": "readability", "rule": "passive_voice", "offset": match.start(),
                           "length": match.end()-match.start(), "message": "Possible passive voice; review the actor and verb."})
        # Tenant-specific length threshold; the upstream detector supplies the default 30-word check.
        if voice and voice.maxSentenceWords != 30:
            for sentence in re.finditer(r"[^.!?]+[.!?]?", text):
                count = len(re.findall(r"\b[\w'-]+\b", sentence.group()))
                if count > voice.maxSentenceWords:
                    issues.append({"source": "voice", "rule": "sentence_length", "offset": sentence.start(),
                                   "message": f"Sentence has {count} words; voice limit is {voice.maxSentenceWords}."})
    if voice:
        for term in ([] if "voice_check" in excluded else voice.bannedTerms):
            for match in phrase_spans(text, term):
                issues.append({"source": "voice", "rule": "banned_term", "offset": match.start(),
                               "length": match.end()-match.start(), "message": f"Banned term: {term}"})
        for term in ([] if "voice_check" in excluded else voice.requiredTerms):
            if not phrase_spans(text, term):
                issues.append({"source": "voice", "rule": "required_term", "message": f"Required term missing: {term}"})
        # These are mechanical subsets of the selected book, not a semantic style grade.
        if voice.stylebook and "house_style" not in excluded:
            guide = (ROOT / "sources/stylebooks" / voice.stylebook / "SKILL.md").read_text()
            for term in BOOK_TERMS.get(voice.stylebook, []):
                for match in phrase_spans(text, term):
                    issues.append({"source": "stylebook", "rule": "avoid_candidate", "offset": match.start(),
                                   "message": f"{voice.stylebook}: review '{term}' against the guide's Avoid section."})
            if "descriptive link" in guide.lower():
                for match in re.finditer(r"\[(?:click here|here|read more)\]\([^)]*\)", text, re.IGNORECASE):
                    issues.append({"source": "stylebook", "rule": "link_text", "offset": match.start(),
                                   "message": "Use descriptive link text."})
    if voice is None:
        from .house_style import findings
        issues.extend(findings(text, issues, phrase_spans))
    # Practical Prose F3: mechanical markup checks only; no scorer or semantic grade.
    fences = re.findall(r"^\s*(`{3,}|~{3,})", text, re.MULTILINE)
    if "readability" not in excluded and len(fences) % 2:
        issues.append({"source": "practical-prose", "rule": "F3", "message": "Unclosed fenced code block."})
    return {"issues": issues, "skipped_agent_style_rules": SKIPPED,
            "checklist": {"E1": "grammar/readability findings", "E3": "proselint and phrase findings",
                          "F3": "fence balance only"},
            "limitations": "Mechanical findings only; semantic accuracy, voice fit and rubric grades are not assessed."}


async def _check_piece(text: str, voice: Voice | None, http: httpx.AsyncClient, *, tank=None, tenant_id=None, bounded=True) -> dict:
    import asyncio
    approved = {}
    if tank is not None:
        if tenant_id is None:
            raise ValueError("Checker tenant is required")
        for component in ("proselint", "agent_style", "readability", "voice_check", "house_style", "languagetool"):
            payload = {"text": text}
            if component != "languagetool" and voice is not None:
                payload["voice"] = voice.model_dump()
            output = await tank.approved_skill(str(tenant_id), "trigger." + component, payload)
            if output is not None:
                approved[component] = output
    result = await asyncio.to_thread(local_checks, text, voice, frozenset(approved))
    for output in approved.values():
        result["issues"].extend(output["issues"])
    if approved:
        result["skills"] = [{"skill": o["_skill"], "ledger_id": o.get("_ledger_id")}
                           for o in approved.values()]
    if "languagetool" in approved:
        result["complete"] = True
        result["checker_errors"] = []
    else:
        try:
            # Fixed internal destination, no configurable public service or cloud fallback.
            response = await http.post("http://trigger-languagetool:8081/v2/check",
                                       data={"text": text, "language": "en-US"}, timeout=30)
            response.raise_for_status()
            payload = response.json()
            matches = payload["matches"]
            if not isinstance(matches, list):
                raise TypeError("Invalid matches")
            for match in matches:
                result["issues"].append({"source": "languagetool", "rule": match["rule"]["id"],
                                         "message": match["message"], "offset": match["offset"], "length": match["length"]})
            warnings = payload.get("warnings", {})
            if not isinstance(warnings, dict):
                raise TypeError("Invalid warnings")
            result["complete"] = not warnings.get("incompleteResults", False)
            result["checker_errors"] = [] if result["complete"] else ["LanguageTool returned incomplete results."]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            result["complete"] = False
            result["checker_errors"] = ["LanguageTool is unavailable; its checks have not completed."]
    result["issue_count"] = len(result["issues"])
    result["issues"] = result["issues"][:200] if bounded else result["issues"]
    result["truncated"] = result["issue_count"] > len(result["issues"])
    result["style_source"] = "tenant" if voice else "house"
    result["voice_revision"] = voice.revision if voice else None
    result["stylebook"] = voice.stylebook if voice else None
    result["model_calls"] = 0
    return result


# Preserve the existing Command action envelope; each checker still sees <20k.
DOCUMENT_LIMIT = 32000
PIECE_LIMIT = 19999


def pieces(text: str):
    """Lossless source slices: headings, then paragraphs, then word boundaries."""
    start = 0
    while start < len(text):
        end = min(start + PIECE_LIMIT, len(text))
        if end < len(text):
            window = text[start:end]
            headings = [m.start() for m in re.finditer(r"(?m)^#{1,6}[ \t]+\S", window) if m.start()]
            paragraphs = [m.end() for m in re.finditer(r"\n[ \t]*\n", window)]
            boundaries = headings or paragraphs or [m.end() for m in re.finditer(r"\s+", window)]
            if boundaries:
                end = start + boundaries[-1]
        yield start, text[start:end]
        start = end


def _whole_position(issue: dict, part: str, start: int, text: str) -> dict:
    item = dict(issue)
    if isinstance(item.get("offset"), int):
        offset = item["offset"]
        # Java LanguageTool reports UTF-16 positions; Python checkers use code points.
        if item.get("source") == "languagetool":
            raw = part.encode("utf-16-le")
            local = len(raw[:offset * 2].decode("utf-16-le", errors="ignore"))
            if isinstance(item.get("length"), int):
                stop = len(raw[:(offset + item["length"]) * 2].decode("utf-16-le", errors="ignore"))
                item["length"] = stop - local
            offset = local
        item["offset"] = start + offset
    elif isinstance(item.get("line"), int):
        rows = part.splitlines(keepends=True)
        local = sum(len(row) for row in rows[:max(0, item["line"] - 1)])
        local += max(0, (item.get("column") or 1) - 1)
        item["offset"] = start + local
    if isinstance(item.get("line"), int):
        local_line = item["line"]
        item["line"] += text.count("\n", 0, start)
        if local_line == 1 and isinstance(item.get("column"), int):
            item["column"] += start - (text.rfind("\n", 0, start) + 1)
    return item


async def check(text: str, voice: Voice | None, http: httpx.AsyncClient, *, tank=None, tenant_id=None) -> dict:
    if not isinstance(text, str) or not 1 <= len(text) <= DOCUMENT_LIMIT:
        raise ValueError("Text exceeds the checker limit")
    if len(text) <= PIECE_LIMIT:
        return await _check_piece(text, voice, http, tank=tank, tenant_id=tenant_id)
    reports = []
    findings = []
    global_seen = set()
    required = {f"Required term missing: {term}": term for term in voice.requiredTerms} if voice else {}
    unclosed = len(re.findall(r"^\s*(`{3,}|~{3,})", text, re.MULTILINE)) % 2
    for start, part in pieces(text):
        report = await _check_piece(part, voice, http, tank=tank, tenant_id=tenant_id, bounded=False)
        reports.append(report)
        for issue in report["issues"]:
            rule = issue.get("rule")
            if rule == "required_term":
                term = required.get(issue.get("message"))
                if term and phrase_spans(text, term):
                    continue
            if rule == "F3" and not unclosed:
                continue
            if rule in {"required_term", "F3"}:
                key = (issue.get("source"), rule, issue.get("message"))
                if key in global_seen:
                    continue
                global_seen.add(key)
            findings.append(_whole_position(issue, part, start, text))
    result = dict(reports[0])
    result.update(issues=findings[:200], issue_count=len(findings), truncated=len(findings) > 200,
                  complete=all(r["complete"] for r in reports),
                  checker_errors=list(dict.fromkeys(e for r in reports for e in r["checker_errors"])))
    if any("skills" in r for r in reports):
        result["skills"] = [skill for r in reports for skill in r.get("skills", [])]
    return result


def report_text(report: dict) -> str:
    lines = [f'{report["issue_count"]} issues found.']
    if report["truncated"]:
        lines.append("First 200 issues shown; check shorter sections for the remaining findings.")
    lines.extend(f'{i["source"]} / {i["rule"]}: {i["message"]}' for i in report["issues"])
    lines.extend(report["checker_errors"])
    lines.append(report["limitations"])
    return "\n".join(lines)
