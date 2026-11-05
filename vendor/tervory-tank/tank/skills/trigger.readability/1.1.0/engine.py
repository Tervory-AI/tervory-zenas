"""Pinned first-build checker snapshot; see rules.zip/trigger/ATTRIBUTION.md.

The source below is copied from agents/trigger/checkers.py at main 3f469b0.
Only imports/data location and this stdin/stdout adapter differ.
"""
from __future__ import annotations
import json
import zipfile
import sys
from pathlib import Path
# run.py owns this scratch folder and removes it after the process ends, so
# a killed engine (SIGKILL, SIGTERM, timeout) no longer leaves it behind.
DATA = Path(sys.argv[2])
if not DATA.is_dir() or any(DATA.iterdir()):
    raise SystemExit("run.py must pass an empty scratch folder")
with zipfile.ZipFile(Path(__file__).with_name("rules.zip")) as archive:
    archive.extractall(DATA)
sys.path.insert(0, str(DATA))
"""Deterministic checks only. No model clients or remote fallback."""

import re
import sys
from pathlib import Path

import httpx
from proselint.checks import __register__
from proselint.registry import CheckRegistry
from proselint.tools import LintFile

CheckRegistry().register_many(__register__)

from trigger.voice import Voice

ROOT = DATA / "trigger"
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


def local_checks(text: str, voice: Voice | None) -> dict:
    issues = []
    for found in LintFile("input", text).lint():
        item = found.into_dict()
        issues.append({"source": "proselint", "rule": item["check_path"], "message": item["message"],
                       "line": found.pos[0], "column": found.pos[1]})
    for rule in RULES:
        if rule.id == "RULE-12" and voice and voice.maxSentenceWords != 30:
            continue
        module = detectors_mech if rule.id in MECHANICAL else detectors_struct if rule.id in STRUCTURAL else None
        if module is None:
            continue
        result = module.run(rule, text, "input.md")
        issues.extend({"source": "agent-style", "rule": v.rule, "line": v.line,
                       "column": v.column, "message": v.detail} for v in result.violations)
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
        for term in voice.bannedTerms:
            for match in phrase_spans(text, term):
                issues.append({"source": "voice", "rule": "banned_term", "offset": match.start(),
                               "length": match.end()-match.start(), "message": f"Banned term: {term}"})
        for term in voice.requiredTerms:
            if not phrase_spans(text, term):
                issues.append({"source": "voice", "rule": "required_term", "message": f"Required term missing: {term}"})
        # These are mechanical subsets of the selected book, not a semantic style grade.
        if voice.stylebook:
            guide = (ROOT / "sources/stylebooks" / voice.stylebook / "SKILL.md").read_text()
            for term in BOOK_TERMS.get(voice.stylebook, []):
                for match in phrase_spans(text, term):
                    issues.append({"source": "stylebook", "rule": "avoid_candidate", "offset": match.start(),
                                   "message": f"{voice.stylebook}: review '{term}' against the guide's Avoid section."})
            if "descriptive link" in guide.lower():
                for match in re.finditer(r"\[(?:click here|here|read more)\]\([^)]*\)", text, re.IGNORECASE):
                    issues.append({"source": "stylebook", "rule": "link_text", "offset": match.start(),
                                   "message": "Use descriptive link text."})
    # Practical Prose F3: mechanical markup checks only; no scorer or semantic grade.
    fences = re.findall(r"^\s*(`{3,}|~{3,})", text, re.MULTILINE)
    if len(fences) % 2:
        issues.append({"source": "practical-prose", "rule": "F3", "message": "Unclosed fenced code block."})
    return {"issues": issues, "skipped_agent_style_rules": SKIPPED,
            "checklist": {"E1": "grammar/readability findings", "E3": "proselint and phrase findings",
                          "F3": "fence balance only"},
            "limitations": "Mechanical findings only; semantic accuracy, voice fit and rubric grades are not assessed."}


async def check(text: str, voice: Voice | None, http: httpx.AsyncClient) -> dict:
    import asyncio
    result = await asyncio.to_thread(local_checks, text, voice)
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
    result["issues"] = result["issues"][:200]
    result["truncated"] = result["issue_count"] > len(result["issues"])
    result["voice_revision"] = voice.revision if voice else None
    result["stylebook"] = voice.stylebook if voice else None
    result["model_calls"] = 0
    return result


def report_text(report: dict) -> str:
    lines = [f'{report["issue_count"]} issues found.']
    if report["truncated"]:
        lines.append("First 200 issues shown; check shorter sections for the remaining findings.")
    lines.extend(f'{i["source"]} / {i["rule"]}: {i["message"]}' for i in report["issues"])
    lines.extend(report["checker_errors"])
    lines.append(report["limitations"])
    return "\n".join(lines)


def belongs(issue, component):
    source, rule = issue["source"], issue["rule"]
    return {
        "proselint": source == "proselint",
        "agent_style": source == "agent-style" and rule != "RULE-12",
        "house_style": source == "stylebook",
        "readability": source in {"readability", "practical-prose"} or rule in {"RULE-12", "sentence_length"},
        "voice_check": source == "voice" and rule != "sentence_length",
    }[component]


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    voice = Voice.model_validate(payload["voice"]) if payload.get("voice") else None
    result = local_checks(payload["text"], voice)
    issues = [item for item in result["issues"] if belongs(item, sys.argv[1])]
    json.dump({"issues": issues, "rules": sorted({i["rule"] for i in issues}),
               "issue_count": len(issues), "model_calls": 0}, sys.stdout)
