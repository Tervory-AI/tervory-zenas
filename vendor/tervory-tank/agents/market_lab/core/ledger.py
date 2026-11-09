"""Tenant-scoped rows for one Market Lab box.

FRED points are refused. Predictions are insert-only: a resolution or a
correction is a new row, and nothing here updates an existing prediction.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from core.batch_guard import Pool
from core.predictions import metadata, calibration

SCHEMA = "agent_market_lab"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class MemoryLedger:
    """In-memory stand-in used by tests. Same rules as the database ledger."""

    def __init__(self) -> None:
        self.series: list[dict[str, Any]] = []
        self.points: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.opportunities: list[dict[str, Any]] = []
        self.predictions: list[dict[str, Any]] = []
        self.reports: list[dict[str, Any]] = []
        self.brief_deliveries: dict[tuple, dict] = {}
        self.brief_events: list[dict] = []
        self.refused: list[str] = []
        self.pool = Pool()

    def save_series(self, row: dict[str, Any]) -> None:
        self._require_tenant(row["tenant_id"])
        if any(item["id"] == row["id"] for item in self.series):
            return
        self.series.append(dict(row))
        self.pool.ensure_series(
            row["id"],
            tenant_id=str(row["tenant_id"]),
            unit=row["unit"],
            source=row["source"],
            currency=str(row.get("currency") or ""),
            region=str(row.get("region") or ""),
            jump_limit=row.get("jump_limit"),
            official=row.get("official"),
        )

    def save_points(self, series_id: str, tenant_id: str, points: list[dict[str, Any]], *, stored: bool, source: str) -> int:
        self._require_tenant(tenant_id)
        if not stored or source == "fred":
            self.refused.append(series_id)
            return 0
        stamped = [{**point, "source": point.get("source") or source} for point in points]
        result = self.pool.ingest(series_id, str(tenant_id), stamped)
        self._sync_points()
        return int(result["added"])

    def release_batch(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        self._require_tenant(tenant_id)
        record = self.pool.release(str(tenant_id), batch_id)
        self._sync_points()
        return record

    def reject_batch(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        self._require_tenant(tenant_id)
        return self.pool.reject(str(tenant_id), batch_id)

    def remove_batch(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        self._require_tenant(tenant_id)
        record = self.pool.remove(str(tenant_id), batch_id)
        self._sync_points()
        return record

    def held_batches(self, tenant_id: str) -> list[dict[str, Any]]:
        self._require_tenant(tenant_id)
        return self.pool.held(str(tenant_id))

    def save_event(self, series_id: str, tenant_id: str, event: dict[str, Any]) -> bool:
        self._require_tenant(tenant_id)
        key = (series_id, event["date"], event["kind"])
        if any((item["series_id"], item["date"], item["kind"]) == key for item in self.events):
            return False
        self.events.append(
            {
                "series_id": series_id,
                "tenant_id": str(tenant_id),
                "date": event["date"],
                "kind": event["kind"],
                "size": event["size"],
            }
        )
        return True

    def save_opportunity(self, row: dict[str, Any]) -> None:
        self._require_tenant(row["tenant_id"])
        self.opportunities.append(dict(row))

    def save_prediction(self, row: dict[str, Any]) -> None:
        self._require_tenant(row["tenant_id"])
        if any(item["id"] == row["id"] for item in self.predictions):
            raise RuntimeError("predictions are append-only")
        self.predictions.append(metadata(row))

    def save_report(self, row: dict[str, Any]) -> None:
        self._require_tenant(row["tenant_id"])
        self.reports.append(dict(row))

    def save_brief(self, report: dict, status: str, reason: str | None) -> None:
        self.save_report(report)
        for channel in ("email", "webhook"):
            key = (str(report["tenant_id"]), str(report["id"]), channel)
            if key not in self.brief_deliveries:
                self.brief_deliveries[key] = {"channel": channel, "status": status, "reason": reason}
                self.brief_events.append({"tenant_id": key[0], "report_id": key[1],
                    "channel": channel, "status": status, "reason": reason, "happened_at": datetime.now(UTC)})

    def briefs_for(self, tenant: str, report_id: str | None = None) -> list[dict]:
        rows = []
        for report in self.reports:
            if not report.get('answer', '').strip():
                continue  # Keep held audit records; never show an empty monthly brief.
            if str(report["tenant_id"]) != str(tenant) or (report_id is not None and str(report["id"]) != str(report_id)):
                continue
            states = [dict(row) for key, row in self.brief_deliveries.items()
                      if key[:2] == (str(tenant), str(report["id"]))]
            if states:
                rows.append({**report, "deliveries": states})
        return sorted(rows, key=lambda row: (row["made_at"], str(row["id"])), reverse=True)[:12]

    def brief_history(self, tenant: str, ident: str) -> list[dict]:
        return [dict(row) for row in self.brief_events
                if row["tenant_id"] == str(tenant) and row["report_id"] == str(ident)]

    def pending_briefs(self, tenant: str, channel: str) -> list[dict]:
        return [dict(report) for report in self.reports if str(report["tenant_id"]) == str(tenant)
                and self.brief_deliveries.get((str(tenant), str(report["id"]), channel), {}).get("status") == "pending"][:3]

    def update_brief(self, tenant: str, ident: str, channel: str, status: str,
                     reason: str | None = None, *, expected: str = "sending") -> bool:
        row = self.brief_deliveries.get((str(tenant), str(ident), channel))
        if row is None or row["status"] != expected:
            return False
        row.update(status=status, reason=reason)
        self.brief_events.append({"tenant_id": str(tenant), "report_id": str(ident),
            "channel": channel, "status": status, "reason": reason, "happened_at": datetime.now(UTC)})
        return True

    def series_for(self, tenant_id: str) -> list[dict[str, Any]]:
        self._require_tenant(tenant_id)
        return [dict(item) for item in self.series if item["tenant_id"] == str(tenant_id)]

    def points_for(self, tenant_id: str, series_id: str | None = None) -> list[dict[str, Any]]:
        self._require_tenant(tenant_id)
        rows = [item for item in self.points if item["tenant_id"] == str(tenant_id)]
        if series_id is not None:
            rows = [item for item in rows if item["series_id"] == series_id]
        return [dict(item) for item in rows]

    def predictions_for(self, tenant_id: str) -> list[dict[str, Any]]:
        self._require_tenant(tenant_id)
        return [dict(item) for item in self.predictions if item["tenant_id"] == str(tenant_id)]

    def calibration_for(self, tenant_id: str) -> dict:
        return calibration(self.predictions_for(tenant_id), tenant_id)

    def _sync_points(self) -> None:
        self.points = [_plain_point(point) for point in self.pool.points]

    def _require_tenant(self, tenant_id: str) -> None:
        text = str(tenant_id)
        if not text:
            raise RuntimeError("tenant_id is required")


class PostgresLedger:
    """asyncpg ledger. Every statement filters by tenant_id."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    async def apply_schema(self) -> None:
        for statement in _statements(schema_sql()):
            try:
                await self.conn.execute(statement)
            except Exception:
                if statement.upper().startswith("CREATE SCHEMA"):
                    present = await self.conn.fetchval(
                        "SELECT 1 FROM pg_namespace WHERE nspname = $1",
                        SCHEMA,
                    )
                    if present == 1:
                        continue
                raise

    async def save_series(self, row: dict[str, Any]) -> None:
        await self.conn.execute(
            f"""
            INSERT INTO {SCHEMA}.series (id, tenant_id, source, ref, unit, engine)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO NOTHING
            """,
            row["id"],
            uuid.UUID(str(row["tenant_id"])),
            row["source"],
            row["ref"],
            row["unit"],
            row["engine"],
        )

    async def save_points(
        self,
        series_id: str,
        tenant_id: str,
        points: list[dict[str, Any]],
        *,
        stored: bool,
        source: str,
    ) -> int:
        if not stored or source == "fred":
            return 0
        if not points:
            return 0
        owner = uuid.UUID(str(tenant_id))
        series = await self.conn.fetchrow(
            f"""
            SELECT id, source, unit, region, currency, jump_limit
            FROM {SCHEMA}.series WHERE id = $1 AND tenant_id = $2
            """,
            series_id,
            owner,
        )
        if series is None:
            raise RuntimeError("series is not in this tenant")
        book = Pool()
        official_rows = await self.conn.fetch(
            f"SELECT date, value FROM {SCHEMA}.official_values WHERE tenant_id = $1 AND series_id = $2",
            owner,
            series_id,
        )
        official = {str(row["date"])[:10]: row["value"] for row in official_rows}
        book.ensure_series(
            series_id,
            tenant_id=str(tenant_id),
            unit=series["unit"],
            source=series["source"],
            currency=series["currency"] or "",
            region=series["region"] or "",
            jump_limit=series["jump_limit"],
            official=official,
        )
        last = await self.conn.fetchrow(
            f"SELECT p.value FROM {SCHEMA}.points p JOIN {SCHEMA}.series s ON s.id = p.series_id WHERE p.series_id = $1 AND s.tenant_id = $2 ORDER BY p.date DESC LIMIT 1",
            series_id,
            owner,
        )
        if last is not None:
            book.points.append(
                {
                    "series_id": series_id,
                    "tenant_id": str(tenant_id),
                    "date": "0000-01-01",
                    "value": last["value"],
                    "batch_id": "seed",
                }
            )
        stamped = [{**point, "source": point.get("source") or source} for point in points]
        result = book.ingest(series_id, str(tenant_id), stamped)
        batch_id = result["id"]
        await self.conn.execute(
            f"""
            INSERT INTO {SCHEMA}.batches (
                id, tenant_id, series_id, source, fetched_at, region, currency, unit, status, reason, jump_limit
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (id) DO NOTHING
            """,
            batch_id,
            owner,
            series_id,
            result["source"],
            result["fetched_at"],
            result["region"],
            result["currency"],
            result["unit"],
            result["status"],
            result["reason"],
            result["jump_limit"],
        )
        for point in book.batch_points.get(batch_id) or []:
            await self.conn.execute(
                f"""
                INSERT INTO {SCHEMA}.batch_points (
                    batch_id, tenant_id, series_id, date, value, source, fetched_at, region, currency, unit
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (batch_id, series_id, date) DO NOTHING
                """,
                batch_id,
                owner,
                series_id,
                point["date"],
                point["value"],
                point["source"],
                point["fetched_at"],
                point["region"],
                point["currency"],
                point["unit"],
            )
        if result["status"] != "pooled":
            return 0
        added = 0
        for point in book.batch_points.get(batch_id) or []:
            written = await self.conn.execute(
                f"""
                INSERT INTO {SCHEMA}.points (
                    series_id, date, value, fetched_at, source, region, currency, unit, batch_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (series_id, date) DO NOTHING
                """,
                series_id,
                point["date"],
                point["value"],
                point["fetched_at"],
                point["source"],
                point["region"],
                point["currency"],
                point["unit"],
                batch_id,
            )
            if str(written).endswith(" 1"):
                added += 1
        if not (series["currency"] or "").strip() or not (series["region"] or "").strip():
            await self.conn.execute(
                f"""
                UPDATE {SCHEMA}.series
                SET currency = CASE WHEN currency = '' THEN $3 ELSE currency END,
                    region = CASE WHEN region = '' THEN $4 ELSE region END
                WHERE id = $1 AND tenant_id = $2
                """,
                series_id,
                owner,
                result["currency"],
                result["region"],
            )
        return added

    async def release_batch(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        return await self._decide_batch(tenant_id, batch_id, "release")

    async def reject_batch(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        return await self._decide_batch(tenant_id, batch_id, "reject")

    async def remove_batch(self, tenant_id: str, batch_id: str) -> dict[str, Any]:
        return await self._decide_batch(tenant_id, batch_id, "remove")

    async def held_batches(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = await self.conn.fetch(
            f"""
            SELECT id, tenant_id, series_id, source, fetched_at, region, currency, unit, status, reason, jump_limit
            FROM {SCHEMA}.batches
            WHERE tenant_id = $1 AND status = 'held'
            ORDER BY created_at, id
            """,
            uuid.UUID(str(tenant_id)),
        )
        return [_record(row) for row in rows]

    async def _decide_batch(self, tenant_id: str, batch_id: str, action: str) -> dict[str, Any]:
        owner = uuid.UUID(str(tenant_id))
        row = await self.conn.fetchrow(
            f"SELECT * FROM {SCHEMA}.batches WHERE id = $1 AND tenant_id = $2",
            batch_id,
            owner,
        )
        if row is None:
            raise RuntimeError("batch is not in this tenant")
        status = row["status"]
        if action == "reject":
            if status != "held":
                raise RuntimeError("only a held batch can be rejected")
            await self.conn.execute(
                f"UPDATE {SCHEMA}.batches SET status = 'rejected' WHERE id = $1 AND tenant_id = $2",
                batch_id,
                owner,
            )
        elif action == "release":
            if status != "held":
                raise RuntimeError("only a held batch can be released")
            held = await self.conn.fetch(
                f"SELECT * FROM {SCHEMA}.batch_points WHERE batch_id = $1 AND tenant_id = $2",
                batch_id,
                owner,
            )
            for point in held:
                await self.conn.execute(
                    f"""
                    INSERT INTO {SCHEMA}.points (
                        series_id, date, value, fetched_at, source, region, currency, unit, batch_id
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (series_id, date) DO NOTHING
                    """,
                    point["series_id"],
                    point["date"],
                    point["value"],
                    point["fetched_at"],
                    point["source"],
                    point["region"],
                    point["currency"],
                    point["unit"],
                    batch_id,
                )
            await self.conn.execute(
                f"UPDATE {SCHEMA}.batches SET status = 'pooled', reason = '' WHERE id = $1 AND tenant_id = $2",
                batch_id,
                owner,
            )
        elif action == "remove":
            if status != "rejected":
                await self.conn.execute(
                    f"""
                    DELETE FROM {SCHEMA}.points p
                    USING {SCHEMA}.series s
                    WHERE p.series_id = s.id AND s.tenant_id = $1 AND p.batch_id = $2
                    """,
                    owner,
                    batch_id,
                )
                await self.conn.execute(
                    f"UPDATE {SCHEMA}.batches SET status = 'removed' WHERE id = $1 AND tenant_id = $2",
                    batch_id,
                    owner,
                )
        else:
            raise RuntimeError("unknown batch action")
        fresh = await self.conn.fetchrow(
            f"SELECT * FROM {SCHEMA}.batches WHERE id = $1 AND tenant_id = $2",
            batch_id,
            owner,
        )
        return _record(fresh)

    async def save_event(self, series_id: str, tenant_id: str, event: dict[str, Any]) -> bool:
        owned = await self.conn.fetchval(
            f"SELECT 1 FROM {SCHEMA}.series WHERE id = $1 AND tenant_id = $2",
            series_id,
            uuid.UUID(str(tenant_id)),
        )
        if owned != 1:
            raise RuntimeError("series is not in this tenant")
        result = await self.conn.execute(
            f"""
            INSERT INTO {SCHEMA}.events (series_id, date, kind, size)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (series_id, date, kind) DO NOTHING
            """,
            series_id,
            event["date"],
            event["kind"],
            event["size"],
        )
        return result.endswith(" 1")

    async def save_opportunity(self, row: dict[str, Any]) -> None:
        await self.conn.execute(
            f"""
            INSERT INTO {SCHEMA}.opportunities (
                id, tenant_id, made_at, area, segment, demand_idx, supply_idx,
                gap, saturation, reachability, trend, rank, evidence
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13::jsonb
            )
            """,
            uuid.UUID(str(row["id"])),
            uuid.UUID(str(row["tenant_id"])),
            row["made_at"],
            row["area"],
            row["segment"],
            row["demand_idx"],
            row["supply_idx"],
            row["gap"],
            row["saturation"],
            row["reachability"],
            json.dumps(row["trend"]),
            row["rank"],
            json.dumps(row["evidence"]),
        )

    async def save_prediction(self, row: dict[str, Any]) -> None:
        row = metadata(row)
        # Literal schema name: no string formatting in this statement (Semgrep raw-query rule).
        await self.conn.execute(
            """
            INSERT INTO agent_market_lab.predictions (
                id, tenant_id, made_at, statement, metric_series_id, comparator,
                target, resolve_by, rationale, sources, outcome, resolved_at, score,
                horizon_seconds, confidence, realised_value, realised_date, kind, feed
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13,
                $14, $15, $16, $17, $18, $19
            )
            """,
            uuid.UUID(str(row["id"])),
            uuid.UUID(str(row["tenant_id"])),
            _timestamp(row["made_at"]),
            row["statement"],
            row["metric_series_id"],
            row["comparator"],
            row["target"],
            _timestamp(row["resolve_by"]),
            row["rationale"],
            json.dumps(row["sources"]),
            row.get("outcome"),
            _timestamp(row.get("resolved_at")),
            row.get("score"),
            row["horizon_seconds"],
            row.get("confidence"),
            row.get("realised_value"),
            date.fromisoformat(str(row["realised_date"])[:10]) if row.get("realised_date") else None,
            row.get("kind"),
            row.get("feed"),
        )

    async def save_report(self, row: dict[str, Any]) -> None:
        await self.conn.execute(
            """
            INSERT INTO agent_market_lab.reports (id, tenant_id, made_at, question, answer, citations, receipts)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
            """,
            uuid.UUID(str(row["id"])),
            uuid.UUID(str(row["tenant_id"])),
            row["made_at"],
            row["question"],
            row["answer"],
            json.dumps(row["citations"]),
            json.dumps(row.get("receipts", [])),
        )

    async def save_brief(self, report: dict, status: str, reason: str | None) -> None:
        async with self.conn.transaction():
            await self.save_report(report)
            await self.conn.execute(
                "INSERT INTO agent_market_lab.brief_deliveries(tenant_id,report_id,channel,status,reason) "
                "SELECT tenant_id,id,c.channel,$3,$4 FROM agent_market_lab.reports "
                "CROSS JOIN (VALUES ('email'),('webhook')) AS c(channel) "
                "WHERE tenant_id=$1 AND id=$2 ON CONFLICT DO NOTHING",
                uuid.UUID(str(report["tenant_id"])), uuid.UUID(str(report["id"])), status, reason)
            await self.conn.execute(
                "INSERT INTO agent_market_lab.brief_delivery_history "
                "(tenant_id,report_id,channel,status,reason,happened_at) "
                "SELECT tenant_id,report_id,channel,status,reason,updated_at "
                "FROM agent_market_lab.brief_deliveries WHERE tenant_id=$1 AND report_id=$2",
                uuid.UUID(str(report["tenant_id"])), uuid.UUID(str(report["id"])))

    async def briefs_for(self, tenant: str, report_id: str | None = None) -> list[dict]:
        rows = await self.conn.fetch(
            "SELECT r.*, (SELECT jsonb_agg(jsonb_build_object('channel',d.channel,'status',d.status,'reason',d.reason) ORDER BY d.channel) "
            "FROM agent_market_lab.brief_deliveries d WHERE d.tenant_id=r.tenant_id AND d.report_id=r.id) AS deliveries "
            "FROM agent_market_lab.reports r WHERE r.tenant_id=$1 "
            "AND length(trim(r.answer)) > 0 "
            "AND EXISTS (SELECT 1 FROM agent_market_lab.brief_deliveries d WHERE d.tenant_id=$1 AND d.report_id=r.id) "
            "AND ($2::uuid IS NULL OR r.id=$2) ORDER BY r.made_at DESC,r.id DESC LIMIT 12",
            uuid.UUID(str(tenant)), uuid.UUID(str(report_id)) if report_id else None)
        result = []
        for row in rows:
            report = _record(row)
            if isinstance(report["deliveries"], str):
                report["deliveries"] = json.loads(report["deliveries"])
            result.append(report)
        return result

    async def brief_history(self, tenant: str, ident: str) -> list[dict]:
        rows = await self.conn.fetch(
            "SELECT channel,status,reason,happened_at FROM agent_market_lab.brief_delivery_history "
            "WHERE tenant_id=$1 AND report_id=$2 ORDER BY happened_at,id",
            uuid.UUID(str(tenant)), uuid.UUID(str(ident)))
        return [dict(row) for row in rows]

    async def pending_briefs(self, tenant: str, channel: str) -> list[dict]:
        rows = await self.conn.fetch(
            "SELECT r.* FROM agent_market_lab.reports r JOIN agent_market_lab.brief_deliveries d "
            "ON d.tenant_id=r.tenant_id AND d.report_id=r.id "
            "WHERE d.tenant_id=$1 AND d.channel=$2 AND d.status='pending' "
            "ORDER BY r.made_at,r.id LIMIT 3", uuid.UUID(str(tenant)), channel)
        return [_record(row) for row in rows]

    async def update_brief(self, tenant: str, ident: str, channel: str, status: str,
                           reason: str | None = None, *, expected: str = "sending") -> bool:
        changed = await self.conn.fetchval(
            "WITH changed AS (UPDATE agent_market_lab.brief_deliveries SET status=$4,reason=$5,updated_at=now() "
            "WHERE tenant_id=$1 AND report_id=$2 AND channel=$3 AND status=$6 RETURNING *) "
            "INSERT INTO agent_market_lab.brief_delivery_history "
            "(tenant_id,report_id,channel,status,reason,happened_at) "
            "SELECT tenant_id,report_id,channel,status,reason,updated_at FROM changed RETURNING report_id",
            uuid.UUID(str(tenant)), uuid.UUID(str(ident)), channel, status, reason, expected)
        return changed is not None

    async def series_for(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = await self.conn.fetch(
            f"""
            SELECT id, tenant_id, source, ref, unit, engine
            FROM {SCHEMA}.series
            WHERE tenant_id = $1
            ORDER BY id
            """,
            uuid.UUID(str(tenant_id)),
        )
        return [_record(row) for row in rows]

    async def points_for(self, tenant_id: str, series_id: str | None = None) -> list[dict[str, Any]]:
        if series_id is None:
            rows = await self.conn.fetch(
                f"""
                SELECT p.series_id, p.date, p.value, p.fetched_at
                FROM {SCHEMA}.points p
                JOIN {SCHEMA}.series s ON s.id = p.series_id
                WHERE s.tenant_id = $1
                ORDER BY p.series_id, p.date
                """,
                uuid.UUID(str(tenant_id)),
            )
        else:
            rows = await self.conn.fetch(
                f"""
                SELECT p.series_id, p.date, p.value, p.fetched_at
                FROM {SCHEMA}.points p
                JOIN {SCHEMA}.series s ON s.id = p.series_id
                WHERE s.tenant_id = $1 AND p.series_id = $2
                ORDER BY p.date
                """,
                uuid.UUID(str(tenant_id)),
                series_id,
            )
        return [_record(row) for row in rows]

    async def predictions_for(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = await self.conn.fetch(
            f"""
            SELECT id, tenant_id, made_at, statement, metric_series_id, comparator,
                   target, resolve_by, rationale, sources, outcome, resolved_at, score,
                   horizon_seconds, confidence, realised_value, realised_date, kind, feed
            FROM {SCHEMA}.predictions
            WHERE tenant_id = $1
            ORDER BY made_at, id
            """,
            uuid.UUID(str(tenant_id)),
        )
        return [_record(row) for row in rows]


    async def calibration_for(self, tenant_id: str) -> dict:
        return calibration(await self.predictions_for(tenant_id), tenant_id)


def _plain_point(point: dict[str, Any]) -> dict[str, Any]:
    stored = dict(point)
    value = stored.get("value")
    if type(value).__name__ == "Decimal":
        number = float(value)
        stored["value"] = int(number) if number.is_integer() else number
    return stored


def _statements(script: str) -> list[str]:
    found: list[str] = []
    for raw in script.split(";"):
        lines = [line for line in raw.splitlines() if not line.strip().startswith("--")]
        text = "\n".join(lines).strip()
        if text:
            found.append(text)
    return found


def _record(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if hasattr(value, "hex") and key in {"id", "tenant_id"}:
            data[key] = str(value)
        elif isinstance(value, str) and key in {"sources", "trend", "evidence", "citations", "receipts"}:
            data[key] = json.loads(value)
    return data


def _timestamp(value: Any) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
