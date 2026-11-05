"""Public gold from this box's own series, points, and predictions.

A surge or shift label is what the stored values did in the 28 days after a
point. It is not a second run of the live detector. A point with no stored
value at least 28 days later is not labelled. series_fetch stays a match
against the stored point. Only the platform tenant's public feeds are read.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from core.pure import series_fetch

WINDOW_DAYS = 28
# The live detector's percent threshold. The forward label uses this number
# on the stored later points. It does not call surge_detect or shift_detect.
PCT_THRESHOLD = 0.25
MAX_CANDIDATES = 200
PLATFORM_TENANT = "00000000-0000-0000-0000-000000000001"
# Uploads and link-only feeds are not public copies. FRED must not be stored.
PRIVATE_SOURCES = frozenset({"upload", "fred", "client", "private"})
_FEED = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_REGIONS = {"us-east", "za"}

SERIES_SQL = """
SELECT id, tenant_id, source, ref, unit, engine
FROM agent_market_lab.series
WHERE tenant_id = $1
ORDER BY id
"""

POINTS_SQL = """
SELECT p.series_id, p.date, p.value, p.fetched_at, p.source, p.region, p.currency, p.unit, p.batch_id
FROM agent_market_lab.points p
JOIN agent_market_lab.series s ON s.id = p.series_id
WHERE s.tenant_id = $1
ORDER BY p.series_id, p.date
"""

PREDICTIONS_SQL = """
SELECT id, tenant_id, made_at, metric_series_id, comparator, target, resolve_by
FROM agent_market_lab.predictions
WHERE tenant_id = $1
ORDER BY made_at, id
"""


def _month(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = value.isoformat() if hasattr(value, "isoformat") else str(value).strip()
    if len(text) < 7 or text[4] != "-" or not (text[:4].isdigit() and text[5:7].isdigit()):
        return None
    if not 1 <= int(text[5:7]) <= 12:
        return None
    return text[:7]


def _family(name: str, synthetic: bool) -> str:
    return name.replace(":", "-synthetic:", 1) if synthetic else name


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) < 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return float(int(number)) if number.is_integer() else number


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _public_series(row: dict[str, Any]) -> tuple[str, str, str, bool] | None:
    if _text(row.get("tenant_id")) != PLATFORM_TENANT:
        return None
    source = _text(row.get("source")).strip()
    if source in PRIVATE_SOURCES or _FEED.fullmatch(source) is None:
        return None
    series_id = _text(row.get("id")).strip()
    ref = _text(row.get("ref")).strip() or source
    if not series_id:
        return None
    return series_id, source, ref, _text(row.get("engine")) == "synthetic"


def _grouped_points(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[tuple[date, dict[str, Any]]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _text(row.get("batch_status")).strip() in {"held", "rejected", "removed"}:
            continue
        series_id = _text(row.get("series_id")).strip()
        stamp = _day(row.get("date"))
        number = _number(row.get("value"))
        if not series_id or stamp is None or number is None:
            continue
        item = {"date": stamp.isoformat(), "value": number}
        fetched_at = _text(row.get("fetched_at")).strip()
        if fetched_at:
            item["fetched_at"] = fetched_at
        for key in ("batch_id", "currency", "unit", "source"):
            text = _text(row.get(key)).strip()
            if text:
                item[key] = text
        grouped.setdefault(series_id, []).append((stamp, item))
    ordered: dict[str, list[dict[str, Any]]] = {}
    for series_id, pairs in grouped.items():
        pairs.sort(key=lambda item: item[0])
        ordered[series_id] = [item for _, item in pairs]
    return ordered


def _later_move(base: float, later: list[dict[str, Any]]) -> str:
    """Up, down, or flat, using the live percent threshold on later points."""
    if base == 0:
        return "flat"
    direction = "flat"
    for point in later:
        change = (float(point["value"]) - base) / abs(base)
        if change >= PCT_THRESHOLD:
            return "up"
        if change <= -PCT_THRESHOLD:
            direction = "down"
    return direction


def _in_window(points: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    start = date.fromisoformat(points[index]["date"])
    end = start + timedelta(days=WINDOW_DAYS)
    found: list[dict[str, Any]] = []
    for point in points[index + 1 :]:
        stamp = date.fromisoformat(point["date"])
        if stamp > end:
            break
        found.append(point)
    return found


def _mature(points: list[dict[str, Any]], index: int) -> bool:
    horizon = date.fromisoformat(points[index]["date"]) + timedelta(days=WINDOW_DAYS)
    return any(date.fromisoformat(point["date"]) >= horizon for point in points[index + 1 :])


def _compare(actual: float, comparator: str, target: float) -> bool | None:
    if comparator in {"gt", ">"}:
        return actual > target
    if comparator in {"gte", ">="}:
        return actual >= target
    if comparator in {"lt", "<"}:
        return actual < target
    if comparator in {"lte", "<="}:
        return actual <= target
    if comparator in {"eq", "="}:
        return actual == target
    return None


def _prediction_for(
    predictions: list[dict[str, Any]],
    series_id: str,
    ref: str,
    point: dict[str, Any],
    later: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compare a stored claim with the later value. The stored outcome is ignored."""
    stamp = date.fromisoformat(point["date"])
    chosen: tuple[date, dict[str, Any]] | None = None
    for row in predictions:
        metric = _text(row.get("metric_series_id")).strip()
        if metric not in {series_id, ref}:
            continue
        made = _day(row.get("made_at"))
        resolve_by = _day(row.get("resolve_by"))
        if made is None or resolve_by is None or made > stamp or resolve_by < stamp:
            continue
        if chosen is None or made >= chosen[0]:
            chosen = (made, row)
    if chosen is None:
        return None
    row = chosen[1]
    target = _number(row.get("target"))
    sample = later[-1]["value"] if later else point["value"]
    agrees = None if target is None else _compare(float(sample), _text(row.get("comparator")).strip(), target)
    if agrees is None:
        return None
    return {"id": _text(row.get("id")), "agrees": agrees}


def _row(
    *,
    skill: str,
    payload: dict[str, Any],
    expected: dict[str, Any],
    source: str,
    family: str,
    region: str,
) -> dict[str, Any]:
    return {
        "skill_id": skill,
        "input": payload,
        "expected": expected,
        "label_source": source[:500],
        "label_family": family,
        "region": region,
        "public": True,
    }


def _stamp_labels(row: dict[str, Any], point: dict[str, Any], series_row: dict[str, Any]) -> dict[str, Any]:
    """A pooled batch labels the example with the series currency and unit."""
    batch_id = _text(point.get("batch_id")).strip()
    if not batch_id:
        return row
    currency = _text(point.get("currency")).strip() or _text(series_row.get("currency")).strip()
    unit = _text(point.get("unit")).strip() or _text(series_row.get("unit")).strip()
    labeled = dict(row)
    labeled["batch_id"] = batch_id
    labeled["quarantine"] = "passed"
    if currency:
        labeled["currency"] = currency
    if unit:
        labeled["unit"] = unit
    payload = dict(row["input"])
    if currency:
        payload["currency"] = currency
    if unit:
        payload["unit"] = unit
    labeled["input"] = payload
    if row["skill_id"] == "market.series_fetch":
        labeled["expected"] = series_fetch(payload)
    return labeled


def _history(points: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    return [{"date": point["date"], "value": point["value"]} for point in points[: index + 1]]


def candidates(
    series: Iterable[dict[str, Any]],
    points: Iterable[dict[str, Any]],
    predictions: Iterable[dict[str, Any]] | None = None,
    *,
    region: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if region not in _REGIONS or limit <= 0:
        return []
    cap = min(int(limit), MAX_CANDIDATES)
    by_series = _grouped_points(points)
    claims = [row for row in (predictions or []) if isinstance(row, dict)]
    output: list[dict[str, Any]] = []

    def add(row: dict[str, Any], point: dict[str, Any]) -> bool:
        output.append(_stamp_labels(row, point, raw))
        return len(output) >= cap

    ordered = sorted(
        (row for row in series if isinstance(row, dict)),
        key=lambda row: _text(row.get("id")),
    )
    for raw in ordered:
        parsed = _public_series(raw)
        if parsed is None:
            continue
        series_id, source, ref, synthetic = parsed
        stored = by_series.get(series_id) or []
        for index, point in enumerate(stored):
            if not _mature(stored, index):
                continue
            month = point["date"][:7]
            family = _family(f"later-value:{source}:{month}", synthetic)
            later = _in_window(stored, index)
            move = _later_move(float(point["value"]), later)
            claim = _prediction_for(claims, series_id, ref, point, later)
            surge_expected: dict[str, Any] = {"outcome": "surge" if move == "up" else "no surge"}
            if move == "up":
                shift_outcome = "bottleneck"
            elif move == "down":
                shift_outcome = "saturating"
            else:
                shift_outcome = "no shift"
            shift_expected: dict[str, Any] = {"outcome": shift_outcome}
            if claim is not None:
                surge_expected["prediction_agrees"] = claim["agrees"]
                shift_expected["prediction_agrees"] = claim["agrees"]
            history = _history(stored, index)
            if add(_row(
                skill="market.surge_detect",
                payload={"points": history},
                expected=surge_expected,
                source=f"later-value:{source}:{point['date']}",
                family=family,
                region=region,
            ), point):
                return output
            if add(_row(
                skill="market.shift_detect",
                payload={"areas": [{"area": ref, "segment": "all", "gap": history}]},
                expected=shift_expected,
                source=f"later-value:{source}:{ref}:{point['date']}",
                family=family,
                region=region,
            ), point):
                return output
        for point in stored:
            month = _month(point["date"])
            if month is None:
                continue
            fetched_at = point.get("fetched_at") or f"{point['date']}T00:00:00+00:00"
            payload = {
                "adapter": "csv",
                "series_id": ref,
                "source": source,
                "fetched_at": fetched_at,
                "text": f"date,value\n{point['date']},{point['value']}\n",
            }
            if add(_row(
                skill="market.series_fetch",
                payload=payload,
                expected=series_fetch(payload),
                source=f"official-match:{source}:{ref}:{point['date']}",
                family=_family(f"official-match:{source}:{month}", synthetic),
                region=region,
            ), point):
                return output
    return output


async def _connect() -> Any:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise OSError("database unavailable")
    import asyncpg

    return await asyncpg.connect(url)


async def read_candidates(*, limit: int, region: str) -> list[dict[str, Any]]:
    """Platform-tenant rows in this box's database. A missing database is an error."""
    if region not in _REGIONS:
        return []
    conn = await _connect()
    try:
        if getattr(conn, "execute", None) is not None:
            from core.ledger import PostgresLedger

            await PostgresLedger(conn).apply_schema()
        tenant = uuid.UUID(PLATFORM_TENANT)
        series = await conn.fetch(SERIES_SQL, tenant)
        points = await conn.fetch(POINTS_SQL, tenant)
        predictions = await conn.fetch(PREDICTIONS_SQL, tenant)
        return candidates(
            [dict(row) for row in series],
            [dict(row) for row in points],
            [dict(row) for row in predictions],
            region=region,
            limit=limit,
        )
    finally:
        await conn.close()
