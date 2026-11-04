"""Source-bound legal reason traces; no outcome key or preferred-winner scoring.

This checks grounding and trace completeness, not whether a legal conclusion is
correct. That needs adversarial evaluation by the actual participants. Unknown
facts/rules stay unknown, and a supported uncertainty is a valid conclusion.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .hearing import HearingError

REASONING_INSTRUCTIONS = """Reason without emotion, popularity or agreement-seeking.
Apply the governing law to legally relevant facts. Separate sourced facts from
contested facts and explicitly labelled assumptions. Cite only supplied verified
authorities; state court weight and uncertainty honestly. Compare material factual
similarities AND differences with precedent, explaining their legal significance.
Answer the strongest opposing argument and any pending judicial challenge. Similar
words are not identical facts; different wording may describe the same relevant
facts. Never invent a factual factor, authority, quotation, or certainty. If the
record is insufficient, identify exactly what is missing instead of forcing an
answer. Do not aim to reproduce the original court's holding: it is withheld until
the independent ruling is committed. Every counsel and judge turn must return a
reasoning trace with facts, rules, comparisons, opposing_argument, response,
uncertainty and conclusion. Preserve the current issue and pending question after
controller handover; continue the transcript rather than restarting the hearing.
"""


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise HearingError("incomplete_reasoning")
    return value.strip()


def validate(problem: dict[str, Any], trace: Any) -> dict[str, Any]:
    if not isinstance(trace, dict):
        raise HearingError("reasoning_required")
    records = problem.get("fact_records") or [{"id": "facts", "status": "sourced", "text": problem["facts"]}]
    known_facts = {f["id"]: f for f in records}
    authorities = {a["id"]: a for a in problem["authorities"]}
    facts = trace.get("facts")
    rules = trace.get("rules")
    comparisons = trace.get("comparisons")
    uncertainty = trace.get("uncertainty")
    if not isinstance(facts, list) or not facts or not isinstance(rules, list) or not isinstance(comparisons, list):
        raise HearingError("incomplete_reasoning")
    if not isinstance(uncertainty, list) or len(uncertainty) > 30:
        raise HearingError("incomplete_reasoning")
    result: dict[str, Any] = {"facts": [], "rules": [], "comparisons": [],
        "opposing_argument": _text(trace.get("opposing_argument")), "response": _text(trace.get("response")),
        "uncertainty": [_text(u) for u in uncertainty], "conclusion": _text(trace.get("conclusion"))}
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("id") not in known_facts or fact.get("position") not in ("sourced", "contested", "assumed"):
            raise HearingError("unsupported_fact")
        record = known_facts[fact["id"]]
        if fact["position"] == "sourced" and record.get("status") != "sourced":
            raise HearingError("assumption_presented_as_fact")
        result["facts"].append({"id": fact["id"], "position": fact["position"], "relevance": _text(fact.get("relevance"))})
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("authority_id") not in authorities:
            raise HearingError("unverified_authority")
        authority = authorities[rule["authority_id"]]
        weight = authority.get("weight", "unknown")
        if rule.get("court_weight") != weight:
            raise HearingError("unsupported_court_weight")
        result["rules"].append({"authority_id": rule["authority_id"], "court_weight": weight,
                                "application": _text(rule.get("application"))})
    for comparison in comparisons:
        if not isinstance(comparison, dict) or comparison.get("authority_id") not in authorities:
            raise HearingError("unverified_authority")
        similarities, differences = comparison.get("similarities"), comparison.get("differences")
        if not isinstance(similarities, list) or not isinstance(differences, list) or not (similarities or differences):
            raise HearingError("incomplete_comparison")
        if any(not isinstance(f, str) or f not in known_facts for f in similarities):
            raise HearingError("unsupported_fact")
        checked = []
        for difference in differences:
            if not isinstance(difference, dict) or difference.get("fact_id") not in known_facts:
                raise HearingError("unsupported_fact")
            checked.append({"fact_id": difference["fact_id"], "explanation": _text(difference.get("explanation"))})
        result["comparisons"].append({"authority_id": comparison["authority_id"], "similarities": similarities,
            "differences": checked, "legal_significance": _text(comparison.get("legal_significance"))})
    if (not rules or not comparisons) and not uncertainty:
        raise HearingError("unsupported_certainty")
    return deepcopy(result)
