"""Run one speaking order with run_one_order and project public matches with watch_payload."""

from __future__ import annotations

from typing import Any, Literal

from agents.zenas.core.moot.address import court_style
from agents.zenas.core.moot.judge import (
    Strictness,
    flag_authorities,
    judge_text_ruling,
    questions_after_submission,
)
from agents.zenas.core.moot.problem import counsel_bundle

Mode = Literal["ai_ai", "student_ai", "student_student"]

# Stub cost per counsel or judge turn used by run_one_order; not a live price.
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


def watch_payload(match: dict[str, Any], problem: dict[str, Any]) -> dict[str, Any]:
    """Public watch shape: transcript, judgment, fairness, reveal, authority links."""
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
