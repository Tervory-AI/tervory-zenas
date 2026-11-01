"""Explicit public projection; no bench notes, seat identities or private state."""
from __future__ import annotations

import json
from typing import Any

from .hearing import HearingError, measured_cost
from .published import _OFFLINE


def public_rows(state: dict[str, Any], fairness: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if state["phase"] != "closed" or not state["published"] or state["ruling"] is None:
        raise HearingError("closed_ruling_required")
    problem = state["problem"]
    ruling = state["ruling"]
    by_id = {a["id"]: a for a in problem["authorities"]}
    authorities = [{"name": by_id[a]["name"], "source_url": by_id[a]["source_url"]} for a in ruling["authority_ids"]]
    roles = {"applicant": "counsel_appellant", "respondent": "counsel_respondent", "judge": "judge"}
    transcript = [{"role": roles[t["seat"]], "text": t["text"]} for t in state["transcript"]]
    human_sides = sum(state["seats"][s]["kind"] == "human" for s in ("applicant", "respondent"))
    mode = ("ai_ai", "student_ai", "student_student")[human_sides]
    # Ordinary hearings do not pretend to be a measured fairness pair.
    match = {"mode": mode, "fairness": ({"reading": "Fairness check not measured."} if human_sides == 0 else {}), "runs": [{"transcript": transcript, "ruling": {
        "order": ruling["order"], "reasons": ruling["reasons"], "authority_links": authorities,
        "reveal": {"real_holding": problem["real_holding"]}}, "cost_usd": measured_cost(state)}]}
    if fairness is not None:
        from .fairness import public_evidence
        match["fairness"] = public_evidence(fairness)
    public_problem = {"id": "hearing-" + state["id"], "title": problem["title"],
        "body": {"title": problem["title"], "region": state["region"], "real_holding": problem["real_holding"]}}
    if _OFFLINE.search(json.dumps([public_problem, match])):
        raise HearingError("offline_content_not_publishable")
    return public_problem, match
