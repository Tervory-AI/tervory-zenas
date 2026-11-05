"""Hold a new batch until it matches the series. Rejected rows never enter the pool.

No industry words. Currency, unit, and region are data the caller passes in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

DEFAULT_JUMP_LIMIT = Decimal("10")
REGIONS = frozenset({"us-east", "za"})
PROVENANCE = ("source", "fetched_at", "region", "currency", "unit", "batch_id")
HELD = "held"
POOLED = "pooled"
REJECTED = "rejected"
REMOVED = "removed"


@dataclass(frozen=True)
class Observation:
    source: str
    fetched_at: str
    region: str
    currency: str
    unit: str
    batch_id: str
    series_id: str
    date: str
    value: Decimal


@dataclass(frozen=True)
class SeriesRule:
    unit: str
    currency: str
    region: str
    last_value: Decimal | None
    jump_limit: Decimal = DEFAULT_JUMP_LIMIT
    official: dict[str, Decimal] = field(default_factory=dict)


def parse_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value).strip().replace(",", ""))


def jump_limit_of(value: object) -> Decimal:
    if value is None or value == "":
        return DEFAULT_JUMP_LIMIT
    limit = parse_decimal(value)
    if limit <= 0:
        return DEFAULT_JUMP_LIMIT
    return limit


def beyond_limit(previous: Decimal | None, value: Decimal, limit: Decimal) -> bool:
    """True when the new number is more than `limit` times the previous one, either way."""
    if previous is None:
        return False
    if limit <= 0:
        return True
    previous_abs = abs(previous)
    value_abs = abs(value)
    if previous_abs == 0:
        return value_abs != 0
    return value_abs > previous_abs * limit or previous_abs > value_abs * limit


def _unique(reasons: list[str]) -> list[str]:
    found: list[str] = []
    for reason in reasons:
        if reason not in found:
            found.append(reason)
    return found


def assess_batch(points: list[Observation], rule: SeriesRule) -> tuple[str, str]:
    """Return pooled, or held with the reasons the batch must not enter the pool."""
    reasons: list[str] = []
    if not points:
        return HELD, "empty batch"
    batch_ids = {point.batch_id.strip() for point in points}
    if len(batch_ids) != 1 or "" in batch_ids:
        reasons.append("batch id is missing")
    units = {point.unit for point in points}
    currencies = {point.currency for point in points}
    if len(units) != 1:
        reasons.append("unit does not match the series")
    if len(currencies) != 1:
        reasons.append("currency does not match the series")
    previous = rule.last_value
    ordered = sorted(points, key=lambda point: (point.date, point.batch_id))
    for point in ordered:
        for name in PROVENANCE:
            if not str(getattr(point, name) or "").strip():
                reasons.append("provenance is incomplete")
                break
        if point.region not in REGIONS:
            reasons.append("region is not recognised")
        if rule.unit and point.unit != rule.unit:
            reasons.append("unit does not match the series")
        if rule.currency and point.currency != rule.currency:
            reasons.append("currency does not match the series")
        if rule.region and point.region != rule.region:
            reasons.append("region does not match the series")
        if beyond_limit(previous, point.value, rule.jump_limit):
            reasons.append("change is beyond the series limit")
        official = rule.official.get(point.date)
        if official is not None and official != point.value:
            reasons.append("value does not match the official value")
        previous = point.value
    reasons = _unique(reasons)
    if reasons:
        return HELD, "; ".join(reasons)
    return POOLED, ""


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _pick(point_value: object, series_value: object, fallback: str) -> str:
    point_text = _text(point_value)
    if point_text:
        return point_text
    series_text = _text(series_value)
    if series_text:
        return series_text
    return fallback


def _official_map(value: object) -> dict[str, Decimal]:
    if not isinstance(value, dict):
        return {}
    found: dict[str, Decimal] = {}
    for key, number in value.items():
        if number is None or number == "":
            continue
        found[str(key)[:10]] = parse_decimal(number)
    return found


class Pool:
    """In-memory pool. Held and rejected batches are stored and never copied in."""

    def __init__(self) -> None:
        self.series: dict[str, dict[str, Any]] = {}
        self.points: list[dict[str, Any]] = []
        self.batches: dict[str, dict[str, Any]] = {}
        self.batch_points: dict[str, list[dict[str, Any]]] = {}

    def ensure_series(
        self,
        series_id: str,
        *,
        tenant_id: str,
        unit: str,
        source: str,
        currency: str = "",
        region: str = "",
        jump_limit: object = None,
        official: object = None,
    ) -> None:
        if series_id in self.series:
            return
        self.series[series_id] = {
            "id": series_id,
            "tenant_id": str(tenant_id),
            "unit": unit,
            "source": source,
            "currency": _text(currency),
            "region": _text(region),
            "jump_limit": jump_limit_of(jump_limit),
            "official": _official_map(official),
        }

    def ingest(self, series_id: str, tenant_id: str, points: list[dict[str, Any]]) -> dict[str, Any]:
        series = self._owned(series_id, tenant_id)
        if not points:
            return {
                "id": "",
                "tenant_id": str(tenant_id),
                "series_id": series_id,
                "source": "",
                "fetched_at": "",
                "region": "",
                "currency": "",
                "unit": "",
                "status": HELD,
                "reason": "empty batch",
                "jump_limit": str(jump_limit_of(series.get("jump_limit"))),
                "added": 0,
            }
        batch_id = _text(points[0].get("batch_id") if points else "") or str(uuid.uuid4())
        observations = [self._observation(series, point, batch_id) for point in points]
        status, reason = assess_batch(observations, self._rule(series))
        record = self._record(series, observations, status, reason)
        self.batches[batch_id] = record
        self.batch_points[batch_id] = [self._stored(series, item) for item in observations]
        added = self._copy(batch_id) if status == POOLED else 0
        if status == POOLED and observations:
            self._establish(series, observations[0])
        return {**record, "added": added}

    def release(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        record = self._batch(tenant_id, batch_id)
        if record["status"] != HELD:
            raise RuntimeError("only a held batch can be released")
        record["status"] = POOLED
        record["reason"] = ""
        self._copy(batch_id)
        observations = self.batch_points.get(batch_id) or []
        series = self.series.get(record["series_id"])
        if series is not None and observations:
            self._establish(series, self._from_stored(observations[0]))
        return dict(record)

    def reject(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        record = self._batch(tenant_id, batch_id)
        if record["status"] != HELD:
            raise RuntimeError("only a held batch can be rejected")
        record["status"] = REJECTED
        return dict(record)

    def remove(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        record = self._batch(tenant_id, batch_id)
        if record["status"] == REJECTED:
            return dict(record)
        self.points = [point for point in self.points if point.get("batch_id") != batch_id]
        record["status"] = REMOVED
        return dict(record)

    def pooled(self, tenant_id: str, series_id: str | None = None) -> list[dict[str, Any]]:
        rows = [dict(point) for point in self.points if point["tenant_id"] == str(tenant_id)]
        if series_id is not None:
            rows = [point for point in rows if point["series_id"] == series_id]
        return rows

    def held(self, tenant_id: str) -> list[dict[str, Any]]:
        return [
            dict(record)
            for record in self.batches.values()
            if record["tenant_id"] == str(tenant_id) and record["status"] == HELD
        ]

    def _owned(self, series_id: str, tenant_id: str) -> dict[str, Any]:
        series = self.series.get(series_id)
        if series is None or series["tenant_id"] != str(tenant_id):
            raise RuntimeError("series is not in this tenant")
        return series

    def _batch(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        record = self.batches.get(batch_id)
        if record is None or record["tenant_id"] != str(tenant_id):
            raise RuntimeError("batch is not in this tenant")
        return record

    def _rule(self, series: dict[str, Any]) -> SeriesRule:
        return SeriesRule(
            unit=_text(series.get("unit")),
            currency=_text(series.get("currency")),
            region=_text(series.get("region")),
            last_value=self._last(series["id"]),
            jump_limit=jump_limit_of(series.get("jump_limit")),
            official=dict(series.get("official") or {}),
        )

    def _last(self, series_id: str) -> Decimal | None:
        rows = [point for point in self.points if point["series_id"] == series_id]
        if not rows:
            return None
        rows.sort(key=lambda point: (point["date"], point.get("batch_id") or ""))
        return parse_decimal(rows[-1]["value"])

    def _observation(self, series: dict[str, Any], point: dict[str, Any], batch_id: str) -> Observation:
        region = _pick(point.get("region"), series.get("region"), "us-east")
        return Observation(
            source=_pick(point.get("source"), series.get("source"), ""),
            fetched_at=_text(point.get("fetched_at")),
            region=region,
            currency=_pick(point.get("currency"), series.get("currency"), "none"),
            unit=_pick(point.get("unit"), series.get("unit"), ""),
            batch_id=batch_id,
            series_id=series["id"],
            date=_text(point.get("date"))[:10],
            value=parse_decimal(point.get("value")),
        )

    def _stored(self, series: dict[str, Any], point: Observation) -> dict[str, Any]:
        return {
            "series_id": point.series_id,
            "tenant_id": series["tenant_id"],
            "date": point.date,
            "value": point.value,
            "source": point.source,
            "fetched_at": point.fetched_at,
            "region": point.region,
            "currency": point.currency,
            "unit": point.unit,
            "batch_id": point.batch_id,
        }

    def _record(self, series: dict[str, Any], points: list[Observation], status: str, reason: str) -> dict[str, Any]:
        first = points[0]
        return {
            "id": first.batch_id,
            "tenant_id": series["tenant_id"],
            "series_id": series["id"],
            "source": first.source,
            "fetched_at": first.fetched_at,
            "region": first.region,
            "currency": first.currency,
            "unit": first.unit,
            "status": status,
            "reason": reason,
            "jump_limit": str(jump_limit_of(series.get("jump_limit"))),
        }

    def _copy(self, batch_id: str) -> int:
        added = 0
        for point in self.batch_points.get(batch_id) or []:
            key = (point["series_id"], point["date"])
            if any((item["series_id"], item["date"]) == key for item in self.points):
                continue
            stored = dict(point)
            stored["value"] = point["value"]
            self.points.append(stored)
            added += 1
        return added

    def _establish(self, series: dict[str, Any], point: Observation) -> None:
        if not _text(series.get("currency")):
            series["currency"] = point.currency
        if not _text(series.get("region")):
            series["region"] = point.region

    def _from_stored(self, point: dict[str, Any]) -> Observation:
        return Observation(
            source=point["source"],
            fetched_at=point["fetched_at"],
            region=point["region"],
            currency=point["currency"],
            unit=point["unit"],
            batch_id=point["batch_id"],
            series_id=point["series_id"],
            date=point["date"],
            value=parse_decimal(point["value"]),
        )
