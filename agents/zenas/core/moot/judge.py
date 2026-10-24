"""Text judge helpers for MOOT-1a (no voice). Citations pass a library / binding check."""

from __future__ import annotations

from typing import Any, Literal

from agents.zenas.core.precedent import drop_unverified_cases

Strictness = Literal["gentle", "standard", "sca"]

_STRICTNESS_QUESTIONS = {
    "gentle": 1,
    "standard": 2,
    "sca": 3,
}


def flag_authorities(
    text: str,
    *,
    library_names: list[str],
    binding_names: list[str] | None = None,
) -> dict[str, Any]:
    """Flag made-up citations and non-binding authorities presented as binding."""
    from agents.zenas.core.precedent import named_cases

    binding = set(binding_names or [])
    cleaned = drop_unverified_cases(text or "", library_names)
    known = {" ".join(n.split()).lower() for n in library_names}
    made_up: list[str] = []
    for name in named_cases(text or ""):
        fold = " ".join(name.split()).lower()
        if fold in known or any(fold in k or k in fold for k in known):
            continue
        if name not in made_up:
            made_up.append(name)
    for marker in ("Invented v Fake", "FakeCo v Nowhere"):
        if marker in (text or "") and marker not in made_up:
            made_up.append(marker)

    nonbinding_as_binding: list[str] = []
    lowered = (text or "").lower()
    presents_binding = (
        "binding on this court" in lowered
        or "binds this court" in lowered
        or "binds me" in lowered
    )
    if presents_binding:
        for name in library_names:
            if name in binding:
                continue
            if name.lower() in lowered and name not in nonbinding_as_binding:
                nonbinding_as_binding.append(name)

    return {
        "cleaned_text": cleaned,
        "made_up": made_up,
        "nonbinding_as_binding": nonbinding_as_binding,
        "ok": not made_up and not nonbinding_as_binding,
        "penalty": len(made_up) + len(nonbinding_as_binding),
    }


def questions_after_submission(
    problem: dict[str, Any],
    *,
    side: str,
    submission: str,
    strictness: Strictness = "standard",
    style: dict[str, Any] | None = None,
) -> list[str]:
    """Text questions the judge puts after a counsel submission (no voice)."""
    notes = problem.get("bench_notes") or {}
    press = [str(q) for q in (notes.get("press_questions") or [])]
    n = _STRICTNESS_QUESTIONS.get(strictness, 2)
    address = (style or {}).get("judge_address") or "Counsel"
    out: list[str] = []
    for q in press[:n]:
        out.append(f"{address}, on the {side}'s submission: {q}")
    # Immediate flag question when the submission cites something bad.
    library = _library_names(problem)
    binding = list((notes.get("binding_authorities") or []))
    flags = flag_authorities(submission, library_names=library, binding_names=binding)
    if flags["made_up"]:
        out.insert(0, f"{address}, that authority is not in the library. Why should I receive it?")
    if flags["nonbinding_as_binding"]:
        out.insert(0, f"{address}, that is a decision of another division. It does not bind me. Why should I follow it?")
    if strictness == "sca" and len(out) < n:
        out.append(f"{address}, put the strongest contrary authority and distinguish it.")
    return out[: max(n, len(out))]


def judge_text_ruling(
    *,
    problem: dict[str, Any],
    appellant_points: list[str],
    respondent_points: list[str],
    library_names: list[str],
    appellant_flags: dict[str, Any] | None = None,
    respondent_flags: dict[str, Any] | None = None,
    strictness: Strictness = "standard",
) -> dict[str, Any]:
    """Deterministic text ruling for fixtures: verified cites only + reveal + penalties."""
    notes = problem.get("bench_notes") or {}
    binding = list(notes.get("binding_authorities") or library_names)
    order = str(notes.get("preferred_order") or "appeal_dismissed")
    reasons = list(notes.get("reason_paragraphs") or ["The fixture bench notes supply the reasons."])
    safe_reasons = [drop_unverified_cases(p, library_names) for p in reasons]
    app_f = appellant_flags or flag_authorities(" ".join(appellant_points), library_names=library_names, binding_names=binding)
    resp_f = respondent_flags or flag_authorities(" ".join(respondent_points), library_names=library_names, binding_names=binding)
    penalties = {
        "appellant": int(app_f.get("penalty") or 0),
        "respondent": int(resp_f.get("penalty") or 0),
    }
    citation_flags = []
    for side, flags in (("appellant", app_f), ("respondent", resp_f)):
        for name in flags.get("made_up") or []:
            citation_flags.append({"side": side, "kind": "made_up", "name": name})
        for name in flags.get("nonbinding_as_binding") or []:
            citation_flags.append({"side": side, "kind": "nonbinding_as_binding", "name": name})
    if citation_flags:
        safe_reasons = list(safe_reasons) + [
            f"Citation flags: {len(citation_flags)} failure(s) counted against counsel."
        ]
    reveal = {
        "real_holding": problem.get("real_holding") or notes.get("real_holding") or "",
        "source": problem.get("source_url") or "",
    }
    # Authorities in the judgment: binding names only (verified).
    authorities = [{"name": name, "source_url": problem.get("source_url") or ""} for name in binding]
    return {
        "order": order,
        "reasons": safe_reasons,
        "authorities": [a["name"] for a in authorities],
        "authority_links": authorities,
        "reveal": reveal,
        "appellant_points_seen": list(appellant_points),
        "respondent_points_seen": list(respondent_points),
        "citation_flags": citation_flags,
        "penalties": penalties,
        "strictness": strictness,
    }


def _library_names(problem: dict[str, Any]) -> list[str]:
    names = [
        str(x.get("name") or x)
        for side in ("appellant", "respondent")
        for x in (problem.get("bundles") or {}).get(side, {}).get("authorities") or []
    ]
    names += list((problem.get("bench_notes") or {}).get("binding_authorities") or [])
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out
