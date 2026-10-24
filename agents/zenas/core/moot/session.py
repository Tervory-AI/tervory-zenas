"""Text match runners: AI-AI (fairness), student-AI, student-student. Fixtures only."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal
from uuid import uuid4

from agents.zenas.core.moot.address import court_style
from agents.zenas.core.moot.judge import (
    Strictness,
    flag_authorities,
    judge_text_ruling,
    questions_after_submission,
)
from agents.zenas.core.moot.learning import points_to_gold
from agents.zenas.core.moot.problem import counsel_bundle, load_fixture_problem

Mode = Literal["ai_ai", "student_ai", "student_student"]

# Stub unit cost per turn (fixture receipts only — not a live price).
_STUB_COST_PER_TURN = 0.001


def _library(problem: dict[str, Any]) -> list[str]:
    names = [
        str(x.get("name") or x)
        for side in ("appellant", "respondent")
        for x in (problem.get("bundles") or {}).get(side, {}).get("authorities") or []
    ]
    names += list((problem.get("bench_notes") or {}).get("binding_authorities") or [])
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _stub_counsel_points(bundle: dict[str, Any], *, oppose: list[str]) -> list[str]:
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


def _winner_party(problem: dict[str, Any]) -> str:
    preferred = str((problem.get("bench_notes") or {}).get("preferred_order") or "")
    if preferred == "appeal_upheld":
        return "appellant"
    return "respondent"


def run_one_order(
    problem: dict[str, Any],
    *,
    first_side: str,
    strictness: Strictness = "standard",
    appellant_override: list[str] | None = None,
    respondent_override: list[str] | None = None,
) -> dict[str, Any]:
    if first_side not in ("appellant", "respondent"):
        raise ValueError("first_side invalid")
    second = "respondent" if first_side == "appellant" else "appellant"
    style = court_style(str(problem.get("region") or "za"))
    a = counsel_bundle(problem, "appellant")
    r = counsel_bundle(problem, "respondent")
    app_pts = list(appellant_override) if appellant_override is not None else _stub_counsel_points(a, oppose=[])
    # Respondent answers appellant's points when AI; student overrides replace stubs.
    if respondent_override is not None:
        resp_pts = list(respondent_override)
    else:
        resp_pts = _stub_counsel_points(r, oppose=app_pts)

    library = _library(problem)
    binding = list((problem.get("bench_notes") or {}).get("binding_authorities") or [])
    flags_app = flag_authorities(" ".join(app_pts), library_names=library, binding_names=binding)
    flags_resp = flag_authorities(" ".join(resp_pts), library_names=library, binding_names=binding)

    transcript: list[dict[str, Any]] = []
    speaking = [first_side, second]
    points_by_side = {"appellant": app_pts, "respondent": resp_pts}
    for side in speaking:
        pts = points_by_side[side]
        transcript.append({"role": f"counsel_{side}", "text": " ".join(pts)})
        qs = questions_after_submission(
            problem,
            side=side,
            submission=" ".join(pts),
            strictness=strictness,
            style=style,
        )
        for q in qs:
            transcript.append({"role": "judge", "text": q})

    ruling = judge_text_ruling(
        problem=problem,
        appellant_points=app_pts,
        respondent_points=resp_pts,
        library_names=library,
        appellant_flags=flags_app,
        respondent_flags=flags_resp,
        strictness=strictness,
    )
    transcript.append({"role": "judgment", "text": f"Order: {ruling['order']}. " + " ".join(ruling["reasons"])})
    transcript.append({"role": "reveal", "text": ruling["reveal"]["real_holding"]})

    winner_party = _winner_party(problem)
    turns = len([t for t in transcript if t["role"].startswith("counsel") or t["role"] == "judge"])
    return {
        "first_side": first_side,
        "second_side": second,
        "winner_party": winner_party,
        "winner_seat": "first" if winner_party == first_side else "second",
        "flags": {"appellant": flags_app, "respondent": flags_resp},
        "ruling": ruling,
        "style": style,
        "transcript": transcript,
        "strictness": strictness,
        "cost_usd": round(turns * _STUB_COST_PER_TURN, 6),
    }


def run_fairness_pair(
    problem_id: str,
    *,
    strictness: Strictness = "standard",
) -> dict[str, Any]:
    """AI v AI twice with sides swapped; fairness stored. Unpublished by default."""
    problem = load_fixture_problem(problem_id)
    run_a = run_one_order(problem, first_side="appellant", strictness=strictness)
    run_b = run_one_order(problem, first_side="respondent", strictness=strictness)
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
    match = {
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
        "cost_usd": round(run_a["cost_usd"] + run_b["cost_usd"], 6),
        "task_types": ["moot.counsel", "moot.judge"],
    }
    match["gold_candidates"] = points_to_gold(match)
    match["watch"] = watch_payload(match, problem)
    return match


def run_student_match(
    problem_id: str,
    *,
    mode: Mode,
    student_side: str,
    student_text: str,
    strictness: Strictness = "standard",
) -> dict[str, Any]:
    """Student types one side; AI or another student fills the other (fixture path)."""
    if mode not in ("student_ai", "student_student"):
        raise ValueError("mode must be student_ai or student_student")
    if student_side not in ("appellant", "respondent"):
        raise ValueError("student_side invalid")
    problem = load_fixture_problem(problem_id)
    other = "respondent" if student_side == "appellant" else "appellant"
    lines = [ln.strip() for ln in (student_text or "").splitlines() if ln.strip()]
    if not lines:
        raise ValueError("student_text required")
    overrides: dict[str, list[str] | None] = {
        "appellant": None,
        "respondent": None,
    }
    overrides[student_side] = lines
    if mode == "student_student":
        # Second student not supplied in this scaffold call — empty opposing stub from bundle only.
        overrides[other] = _stub_counsel_points(counsel_bundle(problem, other), oppose=lines)
    run = run_one_order(
        problem,
        first_side="appellant",
        strictness=strictness,
        appellant_override=overrides["appellant"],
        respondent_override=overrides["respondent"],
    )
    match_id = str(uuid4())
    match = {
        "match_id": match_id,
        "problem_id": problem_id,
        "region": problem.get("region"),
        "mode": mode,
        "published": False,
        "student_side": student_side,
        "runs": [run],
        "fairness": None,
        "problem_title": problem.get("title"),
        "cost_usd": run["cost_usd"],
        "task_types": ["moot.counsel", "moot.judge"],
        "ruling": run["ruling"],
        "transcript": run["transcript"],
        "style": run["style"],
    }
    match["gold_candidates"] = points_to_gold(match)
    match["watch"] = watch_payload(match, problem)
    return match


def publish_match(match: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(match)
    out["published"] = True
    if out.get("watch"):
        out["watch"] = {**out["watch"], "published": True}
    return out


def watch_payload(match: dict[str, Any], problem: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public watch shape: transcript, judgment, fairness, reveal, authority links."""
    problem = problem or load_fixture_problem(str(match["problem_id"]))
    run0 = (match.get("runs") or [{}])[0]
    ruling = run0.get("ruling") or match.get("ruling") or {}
    transcript = run0.get("transcript") or match.get("transcript") or []
    authorities = ruling.get("authority_links") or [
        {"name": n, "source_url": problem.get("source_url") or ""}
        for n in (ruling.get("authorities") or [])
    ]
    return {
        "matchId": match["match_id"],
        "published": bool(match.get("published")),
        "title": match.get("problem_title") or problem.get("title") or "",
        "region": match.get("region") or problem.get("region"),
        "mode": match.get("mode"),
        "transcript": transcript,
        "judgmentOrder": ruling.get("order") or "",
        "judgmentReasons": list(ruling.get("reasons") or []),
        "fairnessReading": (match.get("fairness") or {}).get("reading") or "",
        "fairness": match.get("fairness"),
        "reveal": (ruling.get("reveal") or {}).get("real_holding") or "",
        "authorities": authorities,
        "costUsd": match.get("cost_usd"),
        "style": run0.get("style") or match.get("style"),
    }
