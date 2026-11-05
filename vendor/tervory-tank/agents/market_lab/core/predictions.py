"""Prediction metadata and tenant calibration, using the existing code baseline.

Unknown confidence stays unknown; missing observations are never scored as misses.
No network, model, UI copy, or new prediction store lives here.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from core.pure import shift_detect, surge_detect


def classify(points: list[dict], config: dict) -> dict:
    """The existing rung-3 skills supply both movement and shift classifications."""
    options = {k: config[k] for k in ("window", "z_threshold", "pct_threshold")}
    return {
        "rung": 3,
        "events": surge_detect({"points": points, **options})["events"],
        "shifts": shift_detect(
            {"areas": [{"area": "series", "points": points}], **options}
        )["events"],
    }


def clock(value: Any) -> datetime:
    result = (
        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    )
    return (
        result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    )


def metadata(row: dict) -> dict:
    """Freeze the actual issue-to-deadline horizon and validate probabilities."""
    result = dict(row)
    horizon = (clock(row["resolve_by"]) - clock(row["made_at"])).total_seconds()
    if horizon < 0 or not horizon.is_integer():
        raise ValueError("prediction horizon must be nonnegative whole seconds")
    if (
        row.get("horizon_seconds") is not None
        and int(row["horizon_seconds"]) != horizon
    ):
        raise ValueError("prediction horizon differs from its deadline")
    result["horizon_seconds"] = int(horizon)
    confidence = row.get("confidence")
    if confidence is not None:
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be between zero and one")
    result["confidence"] = confidence
    return result


def resolved_originals(rows: list[dict], tenant_id: str):
    """The shared calibration/gate qualification and original-ID deduplication."""
    owned = [r for r in rows if str(r["tenant_id"]) == str(tenant_id)]
    originals = {str(r["id"]): r for r in owned if r.get("outcome") is None}
    resolved: dict[str, dict] = {}
    for row in sorted(
        owned, key=lambda r: (str(r.get("resolved_at") or ""), str(r["id"]))
    ):
        if row.get("outcome") not in {"hit", "miss"}:
            continue
        for source in row.get("sources") or []:
            if isinstance(source, dict) and str(source.get("resolves")) in originals:
                resolved.setdefault(str(source["resolves"]), row)
    for ident, result in resolved.items():
        original = originals[ident]
        if any(
            original.get(k) is None
            for k in ("confidence", "horizon_seconds", "kind", "feed")
        ):
            continue
        probability = float(original["confidence"])
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            continue
        # A resolution before the frozen horizon cannot contribute evidence.
        if not result.get("resolved_at") or clock(result["resolved_at"]) < clock(
            original["resolve_by"]
        ):
            continue
        yield original, result


def calibration(rows: list[dict], tenant_id: str) -> dict:
    """Resolved originals with a frozen probability, grouped by kind/feed/horizon."""
    groups: dict[tuple, list] = defaultdict(list)
    for original, result in resolved_originals(rows, tenant_id):
        groups[(original["kind"], original["feed"], original["horizon_seconds"])].append(
            (float(original["confidence"]), int(result["outcome"] == "hit"))
        )
    curves = []
    for (kind, feed, horizon), values in sorted(groups.items()):
        buckets: dict[int, list] = defaultdict(list)
        for probability, hit in values:
            buckets[min(int(probability * 10), 9)].append((probability, hit))
        curves.append(
            {
                "kind": kind,
                "feed": feed,
                "horizon_seconds": horizon,
                "count": len(values),
                "mean_confidence": sum(p for p, _ in values) / len(values),
                "realised_rate": sum(y for _, y in values) / len(values),
                "hits": sum(y for _, y in values),
                "misses": sum(1 - y for _, y in values),
                "brier_score": sum((p - y) ** 2 for p, y in values) / len(values),
                "bins": [
                    {
                        "lower": b / 10,
                        "upper": (b + 1) / 10,
                        "count": len(v),
                        "mean_confidence": sum(p for p, _ in v) / len(v),
                        "realised_rate": sum(y for _, y in v) / len(v),
                    }
                    for b, v in sorted(buckets.items())
                ],
            }
        )
    return {"tenant_id": str(tenant_id), "curves": curves}


def gate(rows: list[dict], tenant_id: str, *, now: datetime | None = None) -> dict:
    """A cohort opens 90 days after its earliest qualifying resolved original.

    Unresolved, unmeasurable, premature and foreign resolutions cannot age it.
    The clock is injectable for tests, never a tenant override setting.
    """
    at = clock(now or datetime.now(UTC))
    groups = {}
    for row in rows:
        if str(row["tenant_id"]) != str(tenant_id) or row.get("outcome") is not None:
            continue
        if any(row.get(key) is None for key in ("kind", "feed", "horizon_seconds")):
            continue
        groups.setdefault((row["kind"], row["feed"], row["horizon_seconds"]), [])
    for original, result in resolved_originals(rows, tenant_id):
        if clock(result["resolved_at"]) > at or clock(original["made_at"]) > at:
            continue
        groups[(original["kind"], original["feed"], original["horizon_seconds"])].append(original)
    cohorts = []
    for (kind, feed, horizon), originals in sorted(groups.items()):
        first = min((clock(row["made_at"]) for row in originals), default=None)
        opens_at = first + timedelta(days=90) if first is not None else None
        days = max(0, (at - first).days) if first is not None else 0
        opened = bool(originals) and days >= 90
        cohorts.append({"kind": kind, "feed": feed, "horizon_seconds": horizon,
                        "open": opened, "opens_at": opens_at.isoformat() if opens_at else None,
                        "days_covered": days, "resolved_count": len(originals),
                        "reason": None if opened else "insufficient_history" if originals else "no_resolved_originals"})
    return {"tenant_id": str(tenant_id), "cohorts": cohorts}
