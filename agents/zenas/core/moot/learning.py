"""MOOT-1a learning hooks: gold candidates from accepted/rejected points (fixture path)."""

from __future__ import annotations

from datetime import date
from typing import Any


def gold_bucket(when: date | None = None) -> str:
    """Gold candidate namespace: zenas:moot:{YYYY-MM}."""
    d = when or date.today()
    return f"zenas:moot:{d.year:04d}-{d.month:02d}"


def points_to_gold(match: dict[str, Any], *, when: date | None = None) -> list[dict[str, Any]]:
    """Turn judge-accepted / rejected points into gold candidate rows (no network)."""
    bucket = gold_bucket(when)
    rows: list[dict[str, Any]] = []
    for run in match.get("runs") or [match]:
        ruling = run.get("ruling") or match.get("ruling") or {}
        for side in ("appellant", "respondent"):
            key = f"{side}_points_seen"
            for point in ruling.get(key) or []:
                rejected = any(
                    f.get("side") == side for f in (ruling.get("citation_flags") or [])
                )
                rows.append(
                    {
                        "bucket": bucket,
                        "side": side,
                        "point": point,
                        "accepted": not rejected,
                        "match_id": match.get("match_id"),
                        "problem_id": match.get("problem_id"),
                    }
                )
    return rows
