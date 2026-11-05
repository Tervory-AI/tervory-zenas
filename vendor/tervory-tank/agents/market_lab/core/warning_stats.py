"""Completed, non-overlapping episodes between two series. No market vocabulary."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any

from core.predictions import classify


def day(point: dict) -> date:
    return date.fromisoformat(str(point["date"])[:10])


def measured(points: list[dict], start: date, end: date, max_gap: int) -> bool:
    """A missing tail or a long unobserved interval cannot be counted as a miss."""
    before = [day(p) for p in points if day(p) <= start]
    after = [day(p) for p in points if day(p) >= end]
    if not before or not after:
        return False
    stamps = sorted(
        set(
            [max(before), min(after)] + [day(p) for p in points if start < day(p) < end]
        )
    )
    return all(
        (right - left).days <= max_gap for left, right in zip(stamps, stamps[1:])
    )


def is_hit(value: float, baseline: float, direction: str, threshold: float) -> bool:
    if baseline <= 0:
        return False
    change = (value - baseline) / abs(baseline)
    return change <= -threshold if direction == "drop" else change >= threshold


def analyze(
    upstream: list[dict], downstream: list[dict], config: dict, as_of: date
) -> dict[str, Any]:
    up = sorted((p for p in upstream if day(p) <= as_of), key=day)
    down = sorted((p for p in downstream if day(p) <= as_of), key=day)
    baseline = ({"events": [{"date": str(p["date"]), "kind": config["direction"], "size": float(p["value"])}
                            for p in up if float(p["value"]) > 0], "shifts": []}
                if config.get("trigger") == "event" else classify(up, config))
    events, shifts = baseline["events"], baseline["shifts"]
    shift_by_date = {item["date"]: item["kind"] for item in shifts}
    by_date = {str(p["date"]): (i, p) for i, p in enumerate(up)}
    selected, last_end = [], None
    for event in events:
        if event["kind"] != config["direction"]:
            continue
        stamp = date.fromisoformat(event["date"])
        if last_end is not None and stamp <= last_end:
            continue  # One long move is one episode, not independent daily votes.
        index, point = by_date[event["date"]]
        end = stamp + timedelta(days=config["max_lag_days"])
        selected.append(
            {
                "event": event,
                "point": point,
                "previous": up[index - 1] if index else point,
                "start": stamp,
                "end": end,
                "shift": shift_by_date.get(event["date"]),
            }
        )
        last_end = end
    recent = [e for e in selected if e["start"] <= as_of < e["end"]]
    current = recent[-1] if recent else None
    cutoff = current["start"] if current else as_of
    episodes = []
    for event in selected:
        if event["end"] >= cutoff:
            continue  # No future outcomes or the current episode in the denominator.
        start, end = event["start"], event["end"]
        baseline = next((p for p in reversed(down) if day(p) <= start), None)
        covered = measured(down, start, end, config["max_gap_days"])
        if (
            baseline is None
            or float(baseline["value"]) <= 0
            or not covered
            or (
                config.get("baseline_value") is not None
                and float(baseline["value"]) != config["baseline_value"]
            )
        ):
            continue
        window = [
            p
            for p in down
            if start + timedelta(days=config["min_lag_days"]) <= day(p) <= end
        ]
        hit = next(
            (
                p
                for p in window
                if is_hit(
                    float(p["value"]),
                    float(baseline["value"]),
                    config["outcome_direction"],
                    config["outcome_pct"],
                )
            ),
            None,
        )
        final = window[-1] if window else None
        if final is None:
            continue
        episodes.append(
            {
                "upstream": event["point"],
                "previous": event["previous"],
                "baseline": baseline,
                "outcome": hit or final,
                "hit": hit is not None,
                "lag_days": (day(hit) - start).days if hit else None,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "observations": [baseline]
                + [p for p in down if start < day(p) < end]
                + [next(p for p in down if day(p) >= end)],
            }
        )
    lags = [e["lag_days"] for e in episodes if e["hit"]]
    count, hits = len(episodes), len(lags)
    return {
        "episodes": episodes,
        "episode_count": count,
        "hits": hits,
        "hit_rate": hits / count if count else None,
        "lag_min": min(lags) if lags else None,
        "lag_max": max(lags) if lags else None,
        "lag_median": median(lags) if lags else None,
        "current": None
        if current is None
        else {
            **current,
            "start": current["start"].isoformat(),
            "end": current["end"].isoformat(),
        },
        "config": config,
    }
