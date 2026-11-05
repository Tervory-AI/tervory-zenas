"""Platform source accuracy from explicit human field verdicts, never record proxies.

Only aggregate source kinds, field names and counts leave the tenant audit storage.
Unreviewed/unsure fields and legacy record-only verdicts contribute no score.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
from statistics import NormalDist
from typing import Any, Iterable

from base_source_scores import SOURCES, classify, prefer  # noqa: F401 (public helpers)
from vendor_meter import PLATFORM

def wilson_lower(correct: int, total: int, confidence: float = .95) -> float | None:
    if total <= 0:
        return None
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    p = correct / total
    denominator = 1 + z * z / total
    center = p + z * z / (2 * total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (center - half) / denominator)


def _obj(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def scores(audits: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scores from reviewed audits: one row per (source, field) with at least one resolved record."""
    tally: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"correct": 0, "wrong": 0})
    audit_counts: dict[tuple[str, str], int] = defaultdict(int)
    for audit in audits:
        verdicts = _obj(audit.get("field_verdicts")) or {}
        records = _obj(audit.get("records")) or []
        if not verdicts:
            continue
        contributed = set()
        for record in records:
            checks = verdicts.get(str(record.get("record_id"))) or {}
            fields = _obj(record.get("fields")) or {}
            website = (fields.get("website") or {}).get("value") or (fields.get("website") or {}).get("source_url")
            for name, field in fields.items():
                if not isinstance(field, dict):
                    continue
                verdict = checks.get(name)
                if not field.get("verified") or verdict not in {"correct", "wrong"}:
                    continue
                kind = classify(field.get("source_url"), website, field.get("source_kind") or field.get("source"))
                if kind:
                    tally[(kind, name)][verdict] += 1
                    contributed.add((kind, name))
        for key in contributed:
            audit_counts[key] += 1
    rows = []
    for (source, field), count in sorted(tally.items()):
        checked = count["correct"] + count["wrong"]
        rows.append({"source": source, "field": field, "correct": count["correct"], "wrong": count["wrong"],
                     "checked": checked, "accuracy": count["correct"] / checked,
                     "wilson_lower": wilson_lower(count["correct"], checked), "audits": audit_counts[(source, field)]})
    return rows


async def recompute(conn) -> list[dict[str, Any]]:
    """Recompute every score from all reviewed audits, in one transaction. Counts only."""
    async with conn.transaction():
        # Serialize before reading audit inputs: a slower earlier recompute must
        # never overwrite the counts from a newly committed audit.
        await conn.execute("SELECT pg_advisory_xact_lock(4810512)")
        audits = await conn.fetch("SELECT records, field_verdicts FROM tank.base_audits WHERE reviewed_at IS NOT NULL")
        rows = scores(dict(row) for row in audits)
        await conn.execute("DELETE FROM tank.base_source_reliability WHERE owner_id=$1", PLATFORM)
        for row in rows:
            await conn.execute(
                """INSERT INTO tank.base_source_reliability
                (owner_id, source, field, correct, wrong, checked, accuracy, wilson_lower, audits, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,now())""",
                PLATFORM, row["source"], row["field"], row["correct"], row["wrong"], row["checked"],
                row["accuracy"], row["wilson_lower"], row["audits"])
    return rows


async def read(conn) -> list[dict[str, Any]]:
    rows = await conn.fetch("""SELECT source, field, correct, wrong, checked, accuracy, wilson_lower, audits, updated_at
        FROM tank.base_source_reliability WHERE owner_id=$1
        ORDER BY field, wilson_lower DESC NULLS LAST, source""", PLATFORM)
    return [dict(row) for row in rows]


async def table(conn) -> dict[tuple[str, str], float]:
    """(source, field) -> Wilson lower bound, for `prefer`."""
    return {(row["source"], row["field"]): float(row["wilson_lower"] or 0) for row in await read(conn)}
