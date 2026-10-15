"""Compare a contract with a playbook. This module knows no jurisdiction.

The caller passes the contract text and the positions. An exact approved
sentence is not a deviation. Anything else is a redline back to that sentence.
"""

from __future__ import annotations

import re
from typing import Any

_WORD = re.compile(r"[a-z0-9]+")


def _norm(text: str) -> str:
    return " ".join(_WORD.findall((text or "").lower()))


def _jaccard(left: str, right: str) -> float:
    a = set(_WORD.findall(left.lower()))
    b = set(_WORD.findall(right.lower()))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def review_positions(*, text: str, positions: list[dict[str, Any]]) -> dict[str, Any]:
    paragraphs = [part.strip() for part in (text or "").split("\n\n") if part.strip()]
    folded = _norm(text or "")
    findings: list[dict[str, Any]] = []
    for position in positions:
        kind = str(position.get("kind") or "")
        wanted = str(position.get("text") or "")
        stance = str(position.get("stance") or "must")
        if stance not in ("must", "prefer", "fallback"):
            stance = "must"
        if wanted and _norm(wanted) and _norm(wanted) in folded:
            findings.append(
                {"kind": kind, "risk": "none", "deviation": False, "delete": "", "insert": "", "partial": False}
            )
            continue
        fallback_hit = ""
        for fallback in position.get("fallbacks") or position.get("fallback_positions") or []:
            if _norm(str(fallback)) and _norm(str(fallback)) in folded:
                fallback_hit = str(fallback)
                break
        if fallback_hit:
            findings.append(
                {
                    "kind": kind,
                    "risk": "low",
                    "deviation": True,
                    "delete": fallback_hit,
                    "insert": wanted,
                    "partial": False,
                }
            )
            continue
        best = ""
        score = 0.0
        for paragraph in paragraphs:
            current = _jaccard(paragraph, wanted)
            if current > score:
                score = current
                best = paragraph
        partial = score >= 0.45 and bool(best) and _norm(best) != _norm(wanted)
        risk = "high" if stance == "must" else "medium"
        findings.append(
            {
                "kind": kind,
                "risk": risk,
                "deviation": True,
                "delete": best if partial else "",
                "insert": wanted,
                "partial": partial,
            }
        )
    deviations = [item for item in findings if item["deviation"]]
    if not deviations:
        risk = "none"
    elif len(deviations) == 1:
        risk = str(deviations[0]["risk"])
    else:
        risk = ""
    return {
        "ok": True,
        "findings": findings,
        "deviations": len(deviations),
        "risk": risk,
        "inserts": [item["insert"] for item in deviations],
        "deletes": [item["delete"] for item in deviations],
    }
