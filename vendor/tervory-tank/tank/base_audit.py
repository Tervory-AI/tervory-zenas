"""Saved human audits and recorded confidence counts; no models or external reads."""
from __future__ import annotations

from tenant_db import tenant_transaction

from collections import Counter
import csv
import hashlib
import io
import math
import random
from statistics import NormalDist
from typing import Any
import uuid

from fastapi import HTTPException

from base_metrics import export_records, obj
from base_repository import encoded

SAMPLE_SIZE = 200
BANDS = ((0, 39), (40, 59), (60, 79), (80, 100))
# Match Base's newest-result-per-licence convention, inside this campaign only.
# Both sides of every join are tenant scoped. Scores are read from their existing
# source, never inferred from a verified flag or substituted with a zero.
POPULATION = """WITH campaign_records AS (
    SELECT DISTINCT ON(coalesce(r.licence_key,r.record_id::text)) r.*, a.confidence,
        (a.id IS NOT NULL AND a.updated_at <= r.checked_at) AS evidence_current
    FROM tank.base_record_results r
    JOIN tank.base_campaign_states s ON s.tenant_id=$1 AND s.campaign_id=$2
        AND s.task_id=r.task_id AND s.batch=r.batch
    LEFT JOIN agent_base.records a ON a.tenant_id=$1 AND a.id=r.record_id
        AND a.task_id=r.task_id AND a.batch=r.batch
    WHERE r.tenant_id=$1
    ORDER BY coalesce(r.licence_key,r.record_id::text),r.checked_at DESC,r.record_id DESC
), verified AS (
    SELECT * FROM campaign_records WHERE status='done' AND (
        fields->'website'->>'verified'='true' OR fields->'phone'->>'verified'='true'
        OR fields->'email'->>'verified'='true')
) """


def measure(verdicts: dict[str, str], size: int = SAMPLE_SIZE) -> dict[str, Any]:
    """Wilson score interval for resolved human verdicts; unsure is not correct."""
    counts = Counter(verdicts.values())
    correct, wrong, unsure = (counts[name] for name in ("correct", "wrong", "unsure"))
    resolved = correct + wrong
    accuracy = correct / resolved if resolved else None
    interval = None
    if resolved:
        z = NormalDist().inv_cdf(.975)
        denominator = 1 + z * z / resolved
        center = (accuracy + z * z / (2 * resolved)) / denominator
        half = z * math.sqrt(accuracy * (1 - accuracy) / resolved + z * z / (4 * resolved**2)) / denominator
        interval = {"lower": max(0.0, center - half), "upper": min(1.0, center + half)}
    unreviewed = size - len(verdicts)
    return {"sample_size": size, "reviewed": len(verdicts), "correct": correct, "wrong": wrong,
            "unsure": unsure, "unreviewed": unreviewed, "denominator": resolved,
            "accuracy": accuracy, "confidence_level": .95, "interval_method": "wilson",
            "confidence_interval": interval, "accuracy_scope": "correct / (correct + wrong)",
            # These are bounds on the sample including unresolved verdicts, not
            # confidence limits. Keep them distinct from the Wilson interval.
            "sample_accuracy_bounds": {"lower": correct / size,
                                       "upper": (correct + unsure + unreviewed) / size}}


def unpack(row: Any) -> dict[str, Any]:
    audit = dict(row)
    audit["records"], audit["verdicts"] = obj(audit["records"]), obj(audit["verdicts"])
    audit["measurement"] = measure(audit["verdicts"])
    audit["field_verdicts"] = obj(audit.get("field_verdicts") or {})
    return audit


def audit_csv(audit: dict[str, Any]) -> str:
    """Extend the existing source/date export without changing its CSV escaping."""
    reader = csv.reader(io.StringIO(export_records(audit["records"]).lstrip("\ufeff")))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["audit_id", "campaign_id", "seed", "position", "confidence", "record_checked_at",
                     *next(reader), "verdict"])
    for position, (record, cells) in enumerate(zip(audit["records"], reader), 1):
        writer.writerow([str(audit["id"]), str(audit["campaign_id"]), audit["seed"], position,
                         record["confidence"], record["checked_at"], *cells,
                         audit["verdicts"].get(record["record_id"], "")])
    return "\ufeff" + output.getvalue()


def checked_verdicts(text: str, audit: dict[str, Any]) -> dict[str, str]:
    """Only verdict cells may change; accept BOM, row/column order and CRLF/LF."""
    error = "The checked CSV must contain every sampled record once, with only correct, wrong or unsure verdicts changed."
    try:
        expected_reader = csv.DictReader(io.StringIO(audit_csv(audit).lstrip("\ufeff")))
        expected = {row["record_id"]: row for row in expected_reader}
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)) or set(columns) != set(expected_reader.fieldnames):
            raise ValueError
        verdicts = {}
        for row in reader:
            record_id, verdict = row.get("record_id"), row.get("verdict")
            if (None in row or record_id not in expected or record_id in verdicts
                    or verdict not in {"correct", "wrong", "unsure"}
                    or any(row[key] != value for key, value in expected[record_id].items() if key != "verdict")):
                raise ValueError
            verdicts[record_id] = verdict
        if len(verdicts) != SAMPLE_SIZE:
            raise ValueError
        return verdicts
    except (csv.Error, ValueError):
        raise HTTPException(422, error) from None


class AuditStore:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    async def create(self, tenant: uuid.UUID, campaign: uuid.UUID, seed: int, actor: str) -> dict[str, Any]:
        async with self.rt.pool().acquire() as conn, conn.transaction():
            # Serialize creates for the campaign, including identical-seed retries.
            found = await conn.fetchval("SELECT id FROM tank.base_campaigns WHERE tenant_id=$1 AND id=$2 FOR UPDATE", tenant, campaign)
            if not found:
                raise HTTPException(404, "Campaign not found.")
            old = await conn.fetchrow("SELECT * FROM tank.base_audits WHERE tenant_id=$1 AND campaign_id=$2 AND seed=$3", tenant, campaign, seed)
            if old:
                return unpack(old)
            states = await conn.fetch("SELECT status,finished_at FROM tank.base_campaign_states WHERE tenant_id=$1 AND campaign_id=$2 FOR UPDATE", tenant, campaign)
            if not states or any(row["status"] not in {"done", "failed"} or not row["finished_at"] for row in states):
                raise HTTPException(409, "The campaign must be finished before drawing an audit.")
            # Collection can lag a finished worker. Never draw from a partial
            # population or from evidence older than the scored source record.
            # One statement gives the population and collection check the same
            # PostgreSQL snapshot, even while the collector is committing rows.
            rows = await conn.fetch(POPULATION + """SELECT *, EXISTS(
                SELECT 1 FROM agent_base.records a
                JOIN tank.base_campaign_states s ON s.tenant_id=$1 AND s.campaign_id=$2
                    AND s.task_id=a.task_id AND s.batch=a.batch
                LEFT JOIN tank.base_record_results r ON r.tenant_id=$1 AND r.record_id=a.id
                WHERE a.tenant_id=$1 AND (a.status NOT IN ('done','failed')
                    OR r.record_id IS NULL OR r.checked_at<a.updated_at)) AS collection_pending
                FROM verified ORDER BY record_id""", tenant, campaign)
            if any(row["collection_pending"] for row in rows):
                raise HTTPException(409, "The campaign results are still being collected.")
            if any(not row["evidence_current"] or row["confidence"] is None or not 0 <= row["confidence"] <= 100 for row in rows):
                raise HTTPException(409, "The campaign evidence or confidence is incomplete.")
            if len(rows) < SAMPLE_SIZE:
                raise HTTPException(409, "An audit requires at least 200 verified records in this campaign.")
            records = [{**{key: value for key, value in row.items() if key not in {"collection_pending", "evidence_current"}},
                        "fields": obj(row["fields"])} for row in random.Random(seed).sample(rows, SAMPLE_SIZE)]
            digest = hashlib.sha256(encoded([(str(row["record_id"]), row["source_revision"], row["confidence"]) for row in rows]).encode()).hexdigest()
            saved = await conn.fetchrow("""INSERT INTO tank.base_audits
                (tenant_id,campaign_id,seed,created_by,population_size,population_sha256,records)
                VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) RETURNING *""",
                tenant, campaign, seed, actor, len(rows), digest, encoded(records))
        return unpack(saved)

    async def get(self, tenant: uuid.UUID, campaign: uuid.UUID, audit: uuid.UUID) -> dict[str, Any]:
        async with self.rt.pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tank.base_audits WHERE tenant_id=$1 AND campaign_id=$2 AND id=$3", tenant, campaign, audit)
        if not row:
            raise HTTPException(404, "Audit not found.")
        return unpack(row)

    async def review(self, tenant: uuid.UUID, campaign: uuid.UUID, audit: uuid.UUID, text: str, actor: str,
                     field_verdicts: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
        async with self.rt.pool().acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT * FROM tank.base_audits WHERE tenant_id=$1 AND campaign_id=$2 AND id=$3 FOR UPDATE", tenant, campaign, audit)
            if not row:
                raise HTTPException(404, "Audit not found.")
            saved = unpack(row)
            verdicts = checked_verdicts(text, saved)
            fields = field_verdicts or {}
            sampled = {record["record_id"]: record["fields"] for record in saved["records"]}
            for record_id, checks in fields.items():
                if record_id not in sampled or not isinstance(checks, dict) or any(
                    name not in sampled[record_id] or not sampled[record_id][name].get("verified")
                    or verdict not in {"correct", "wrong", "unsure"} for name, verdict in checks.items()
                ):
                    raise HTTPException(422, "Field verdicts must check verified fields in this saved audit.")
            if saved["reviewed_at"]:
                if verdicts != saved["verdicts"] or field_verdicts is not None and fields != saved["field_verdicts"]:
                    raise HTTPException(409, "This audit already has saved verdicts.")
                return saved
            row = await conn.fetchrow("""UPDATE tank.base_audits SET verdicts=$4::jsonb,reviewed_at=now(),reviewed_by=$5,field_verdicts=$6::jsonb
                WHERE tenant_id=$1 AND campaign_id=$2 AND id=$3 RETURNING *""", tenant, campaign, audit, encoded(verdicts), actor, encoded(fields))
            # Review and aggregates commit together. A failed recompute cannot
            # leave a successfully acknowledged batch with stale scores.
            await __import__("base_reliability").recompute(conn)
        return unpack(row)

    async def histogram(self, tenant: uuid.UUID, campaign: uuid.UUID) -> dict[str, Any]:
        async with tenant_transaction(self.rt.pool(), isolation="repeatable_read", readonly=True) as conn:
            found = await conn.fetchval("SELECT id FROM tank.base_campaigns WHERE tenant_id=$1 AND id=$2", tenant, campaign)
            if not found:
                raise HTTPException(404, "Campaign not found.")
            states = await conn.fetch("SELECT state FROM tank.base_campaign_states WHERE tenant_id=$1 AND campaign_id=$2 ORDER BY state", tenant, campaign)
            rows = await conn.fetch(POPULATION + """SELECT state,
                CASE WHEN NOT evidence_current OR confidence IS NULL OR confidence NOT BETWEEN 0 AND 100 THEN NULL
                     WHEN confidence<40 THEN 0 WHEN confidence<60 THEN 40 WHEN confidence<80 THEN 60 ELSE 80 END AS band,
                count(*) AS count FROM verified GROUP BY state,band""", tenant, campaign)
        grouped = {(row["state"], row["band"]): row["count"] for row in rows}

        def counts(state: str | None) -> dict[str, Any]:
            def count(band):
                return sum(n for (s, b), n in grouped.items() if b == band and (state is None or state == s))
            bands = [{"min": low, "max": high, "count": count(low)} for low, high in BANDS]
            missing = count(None)
            return {"verified_records": sum(band["count"] for band in bands) + missing,
                    "bands": bands, "missing_confidence": missing}

        return {"tenant_id": tenant, "campaign_id": campaign, "score_source": "agent_base.records.confidence",
                **counts(None), "states": [{"state": row["state"], **counts(row["state"])} for row in states]}
