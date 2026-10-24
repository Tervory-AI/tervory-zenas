"""Text judge helpers for MOOT-1a (no voice). Citations pass a library / binding check."""

from __future__ import annotations

from typing import Any

from agents.zenas.core.precedent import drop_unverified_cases


def flag_authorities(
    text: str,
    *,
    library_names: list[str],
    binding_names: list[str] | None = None,
) -> dict[str, Any]:
    """Flag made-up citations and non-binding authorities presented as binding.

    ``library_names`` are verified library case names / citations.
    ``binding_names`` are the subset that bind this bench. Naming a library case
    that is only persuasive as if it binds is flagged.
    """
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
    }



def judge_text_ruling(
    *,
    problem: dict[str, Any],
    appellant_points: list[str],
    respondent_points: list[str],
    library_names: list[str],
) -> dict[str, Any]:
    """Deterministic text ruling for fixtures: verified cites only + reveal."""
    notes = problem.get("bench_notes") or {}
    binding = list(notes.get("binding_authorities") or library_names)
    order = str(notes.get("preferred_order") or "appeal_dismissed")
    reasons = list(notes.get("reason_paragraphs") or ["The fixture bench notes supply the reasons."])
    # Strip any unverified name from reasons.
    safe_reasons = [drop_unverified_cases(p, library_names) for p in reasons]
    reveal = {
        "real_holding": problem.get("real_holding") or notes.get("real_holding") or "",
        "source": problem.get("source_url") or "",
    }
    return {
        "order": order,
        "reasons": safe_reasons,
        "authorities": binding,
        "reveal": reveal,
        "appellant_points_seen": list(appellant_points),
        "respondent_points_seen": list(respondent_points),
    }
