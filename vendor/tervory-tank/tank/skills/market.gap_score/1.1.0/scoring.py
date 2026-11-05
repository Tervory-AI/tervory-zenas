"""Frozen gap arithmetic with comparable snapshots and area/segment identities."""
from __future__ import annotations

import math
from datetime import date
from typing import Any


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(label + " must be a finite number")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(label + " must be a finite number") from None
    if not math.isfinite(number):
        raise ValueError(label + " must be a finite number")
    if number < 0:
        raise ValueError("negative values are not allowed")
    return number


def _validated(payload: dict) -> list[dict]:
    areas = payload.get("areas", [])
    if not isinstance(areas, list):
        raise ValueError("areas must be a list")
    result, keys, latest, population = [], set(), set(), set()
    for raw in areas:
        if not isinstance(raw, dict) or not isinstance(raw.get("area"), str) or not raw["area"].strip():
            raise ValueError("each area needs a name")
        row = dict(raw)
        row["area"] = row["area"].strip()
        segment = row.get("segment", "all")
        if not isinstance(segment, str) or not segment.strip():
            raise ValueError("each segment needs a name")
        row["segment"] = segment.strip()
        key = (row["area"], row["segment"])
        if key in keys:
            raise ValueError("duplicate area+segment pairs")
        keys.add(key)
        has_population = row.get("population") is not None
        population.add(has_population)
        if has_population:
            try:
                row["population"] = _number(row["population"], "population")
            except ValueError:
                raise ValueError("population must be finite and greater than zero") from None
            if row["population"] <= 0:
                raise ValueError("population must be finite and greater than zero")
        for role in ("demand", "supply"):
            points = row.get(role)
            if not isinstance(points, list) or not points:
                raise ValueError("each area must have demand and supply")
            parsed = {}
            for point in points:
                if not isinstance(point, dict):
                    raise ValueError("points must contain a date and value")
                try:
                    stamp = date.fromisoformat(point["date"]).isoformat()
                except (KeyError, ValueError, TypeError):
                    raise ValueError("points must have valid ISO dates") from None
                if stamp in parsed:
                    raise ValueError("duplicate series dates")
                parsed[stamp] = _number(point.get("value"), role)
            row[role] = parsed
            latest.add(max(parsed))
        for name in ("competitors", "reachability", "targets", "reachable", "size", "fit"):
            if row.get(name) is not None:
                row[name] = _number(row[name], name)
        result.append(row)
    if len(population) > 1:
        raise ValueError("population must be present on all areas or none")
    if len(latest) > 1:
        raise ValueError("areas must share the latest demand and supply date")
    return result


def gap_score(payload: dict) -> dict:
    try:
        areas = _validated(payload)
    except ValueError as exc:
        return {"status": "rejected", "reason": str(exc), "ranked": [], "by_area_segment": {}}
    dates = set.intersection(*(set(a[role]) for a in areas for role in ("demand", "supply"))) if areas else set()
    histories = {(a["area"], a["segment"]): [] for a in areas}
    latest = {}
    # Only compare dates where the same full cohort has both measurements.
    for stamp in sorted(dates):
        snapshot = []
        for area in areas:
            demand, supply = area["demand"][stamp], area["supply"][stamp]
            per_capita = area.get("population") is not None
            supply_level = supply / area["population"] if per_capita else supply
            if not math.isfinite(supply_level):
                return {"status": "rejected", "reason": "supply per capita must be finite", "ranked": [], "by_area_segment": {}}
            snapshot.append(dict(area=area["area"], segment=area["segment"], demand=demand,
                                 supply_level=supply_level,
                                 per_capita=per_capita, competitors=area.get("competitors") or 0,
                                 reachability=_reach(area),
                                 size=area["size"] if area.get("size") is not None else demand,
                                 fit=area.get("fit") or 0))
        for row in _score_snapshot(snapshot):
            key = (row["area"], row["segment"])
            histories[key].append({"date": stamp, "value": row["gap"]})
            latest[key] = row
    ranked = sorted(latest.values(), key=lambda row: (-row["gap"], row["area"], row["segment"]))
    indexed = {}
    for index, row in enumerate(ranked, 1):
        row["trend"] = _trend(histories[(row["area"], row["segment"])])
        row["rank"] = index
        row["recommendation"] = recommend(row["gap"], row["saturation"], row["trend"]["m3"])
        indexed.setdefault(row["area"], {})[row["segment"]] = row
    top = ranked[0] if ranked else None
    hot = max(ranked, key=lambda row: (row["supply_idx"], row["area"], row["segment"])) if ranked else None
    return dict(ranked=ranked, by_area_segment=indexed,
                top_area=top["area"] if top else None, top_segment=top["segment"] if top else None,
                top_rank=top["rank"] if top else None, top_recommendation=top["recommendation"] if top else None,
                hot_area=hot["area"] if hot else None, hot_segment=hot["segment"] if hot else None,
                hot_recommendation=hot["recommendation"] if hot else None)


def recommend(gap: float, saturation: float, trend_m3: float | None) -> str:
    if gap <= 0 or saturation >= 0.7:
        return "reduce"
    if gap > 0 and saturation < 0.45 and (trend_m3 is None or trend_m3 >= 0):
        return "increase"
    return "hold"

def _score_snapshot(snapshot: list[dict[str, Any]]) -> list[dict[str, Any]]:
    demand_max = max(item["demand"] for item in snapshot)
    supply_max = max(item["supply_level"] for item in snapshot)
    rival_max = max(item["competitors"] for item in snapshot)
    scored: list[dict[str, Any]] = []
    for item in snapshot:
        demand_idx = 0.0 if demand_max == 0 else 100.0 * (item["demand"] / demand_max)
        supply_idx = 0.0 if supply_max == 0 else 100.0 * (item["supply_level"] / supply_max)
        density_idx = 0.0 if supply_max == 0 else item["supply_level"] / supply_max
        rival_idx = 0.0 if rival_max == 0 else item["competitors"] / rival_max
        saturation = 0.5 * density_idx + 0.5 * rival_idx
        scored.append(
            {
                "area": item["area"],
                "segment": item["segment"],
                "demand_idx": round(demand_idx, 4),
                "supply_idx": round(supply_idx, 4),
                "gap": round(demand_idx - supply_idx, 4),
                "saturation": round(saturation, 4),
                "reachability": round(item["reachability"], 4),
                "per_capita": item["per_capita"],
                "size": item["size"],
                "fit": item["fit"],
            }
        )
    return scored

def _reach(area: dict[str, Any]) -> float:
    if area.get("reachability") is not None:
        return min(1.0, max(0.0, float(area["reachability"])))
    targets = float(area.get("targets") or 0)
    if targets <= 0:
        return 0.0
    return min(1.0, max(0.0, float(area.get("reachable") or 0) / targets))

def _trend(series: list[dict[str, Any]]) -> dict[str, float | None]:
    if not series:
        return {"m3": None, "m6": None, "m12": None}
    ordered = sorted(series, key=lambda item: item["date"])
    latest = ordered[-1]
    end = date.fromisoformat(latest["date"])
    current = float(latest["value"])

    def change(months: int) -> float | None:
        target = _shift_months(end, -months)
        prior = [item for item in ordered if date.fromisoformat(item["date"]) <= target]
        if not prior:
            return None
        return round(current - float(prior[-1]["value"]), 4)

    return {"m3": change(3), "m6": change(6), "m12": change(12)}

def _shift_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, last))
