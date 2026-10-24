"""AI-against-AI match runner with fairness swap (text edition)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from agents.zenas.core.moot.address import court_style
from agents.zenas.core.moot.judge import flag_authorities, judge_text_ruling
from agents.zenas.core.moot.problem import counsel_bundle, load_fixture_problem


def _stub_counsel_points(bundle: dict[str, Any], *, oppose: list[str]) -> list[str]:
    """Deterministic AI counsel points from the bundle (no model call in scaffold)."""
    headings = [str(h) for h in (bundle.get("bundle") or {}).get("grounds") or []]
    authorities = [str(a.get("name") or a) for a in (bundle.get("bundle") or {}).get("authorities") or []]
    points = []
    for g in headings[:3]:
        points.append(g)
    if authorities:
        points.append(f"Authority: {authorities[0]}")
    for other in oppose[:1]:
        points.append(f"In answer to the other side: {other}")
    return points


def run_one_order(
    problem: dict[str, Any],
    *,
    first_side: str,
) -> dict[str, Any]:
    """Run one seating order. ``first_side`` speaks first (appellant|respondent)."""
    if first_side not in ("appellant", "respondent"):
        raise ValueError("first_side invalid")
    second = "respondent" if first_side == "appellant" else "appellant"
    a = counsel_bundle(problem, "appellant")
    r = counsel_bundle(problem, "respondent")
    first_bundle = a if first_side == "appellant" else r
    second_bundle = r if first_side == "appellant" else a
    first_points = _stub_counsel_points(first_bundle, oppose=[])
    second_points = _stub_counsel_points(second_bundle, oppose=first_points)
    library = [
        str(x.get("name") or x)
        for side in ("appellant", "respondent")
        for x in (problem.get("bundles") or {}).get(side, {}).get("authorities") or []
    ]
    library += list((problem.get("bench_notes") or {}).get("binding_authorities") or [])
    # Judge sees both submissions in speaking order.
    if first_side == "appellant":
        app_pts, resp_pts = first_points, second_points
    else:
        app_pts, resp_pts = second_points, first_points
    flags_app = flag_authorities(" ".join(app_pts), library_names=library)
    flags_resp = flag_authorities(" ".join(resp_pts), library_names=library)
    ruling = judge_text_ruling(
        problem=problem,
        appellant_points=app_pts,
        respondent_points=resp_pts,
        library_names=library,
    )
    # Fixture winner: preferred_order maps to which party "wins" this seating.
    preferred = str((problem.get("bench_notes") or {}).get("preferred_order") or "")
    if preferred == "appeal_dismissed":
        winner_party = "respondent"
    elif preferred == "appeal_upheld":
        winner_party = "appellant"
    else:
        winner_party = "respondent"
    return {
        "first_side": first_side,
        "second_side": second,
        "winner_party": winner_party,
        "winner_seat": "first" if winner_party == first_side else "second",
        "flags": {"appellant": flags_app, "respondent": flags_resp},
        "ruling": ruling,
        "style": court_style(str(problem.get("region") or "za")),
    }


def run_fairness_pair(problem_id: str) -> dict[str, Any]:
    """Run AI v AI twice with sides swapped; store both results + fairness summary."""
    problem = load_fixture_problem(problem_id)
    run_a = run_one_order(problem, first_side="appellant")
    run_b = run_one_order(problem, first_side="respondent")
    same_party = run_a["winner_party"] == run_b["winner_party"]
    same_seat = run_a["winner_seat"] == run_b["winner_seat"]
    if same_party and not same_seat:
        reading = "problem_may_be_lopsided"
    elif same_seat and not same_party:
        reading = "seat_strength"
    elif same_party and same_seat:
        reading = "both_same_party_and_seat"
    else:
        reading = "split"
    match_id = str(uuid4())
    return {
        "match_id": match_id,
        "problem_id": problem_id,
        "region": problem.get("region"),
        "mode": "ai_ai",
        "published": False,
        "runs": [run_a, run_b],
        "fairness": {
            "same_party_wins_both": same_party,
            "same_seat_wins_both": same_seat,
            "reading": reading,
        },
        "problem_title": problem.get("title"),
    }


def publish_match(match: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(match)
    out["published"] = True
    return out
