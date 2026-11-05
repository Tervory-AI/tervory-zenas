"""Append-only points and events. A re-run does not overwrite a number."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from feeds.batch_guard import (
    DEFAULT_JUMP_LIMIT,
    Observation,
    SeriesRule,
    assess_batch,
    jump_limit_of,
    parse_decimal,
)
from feeds.keys import key_is_set
from feeds.models import SourceSpec


def canonical_number(value: object) -> str:
    if value is None or value == "":
        raise ValueError("empty number")
    number = Decimal(str(value))
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _revision_suffix(base: str, revision: str) -> int:
    if revision == base:
        return 1
    tail = revision[len(base) + 1 :]
    if tail.isdigit():
        return int(tail)
    return 0


class Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._batch_id: str | None = None
        self._staged: list[dict[str, Any]] = []

    def begin_batch(self, batch_id: str) -> None:
        self._batch_id = batch_id
        self._staged = []

    def clear_batch(self) -> None:
        self._batch_id = None
        self._staged = []

    def set_official(self, series_id: str, date: str, value: object) -> None:
        self.conn.execute(
            """
            INSERT INTO official_values (series_id, date, value)
            VALUES (?, ?, ?)
            ON CONFLICT(series_id, date) DO UPDATE SET value = excluded.value
            """,
            (series_id, date[:10], canonical_number(value)),
        )

    def set_jump_limit(self, series_id: str, limit: object) -> None:
        self.conn.execute(
            "UPDATE series SET jump_limit = ? WHERE id = ?",
            (float(jump_limit_of(limit)), series_id),
        )

    def finish_batch(self) -> str:
        batch_id = self._batch_id
        staged = list(self._staged)
        self.clear_batch()
        if not batch_id or not staged:
            return "pooled"
        return self._commit_staged(batch_id, staged)

    def upsert_source(self, source: SourceSpec) -> None:
        self.conn.execute(
            """
            INSERT INTO feed_sources (
                id, name, kind, url, licence, terms_checked_at, cadence, enabled,
                key_name, key_required
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                kind = excluded.kind,
                url = excluded.url,
                licence = excluded.licence,
                terms_checked_at = excluded.terms_checked_at,
                cadence = excluded.cadence,
                enabled = excluded.enabled,
                key_name = excluded.key_name,
                key_required = excluded.key_required
            """,
            (
                source.id,
                source.name,
                source.kind,
                source.url,
                source.licence,
                source.terms_checked_at,
                source.cadence,
                1 if source.enabled else 0,
                source.key_name,
                1 if source.key_required else 0,
            ),
        )

    def ensure_series(
        self,
        source_id: str,
        ref: str,
        title: str,
        unit: str,
        geo_level: str,
        geo: str,
        frequency: str,
    ) -> str:
        series_id = f"{source_id}:{ref}"
        self.conn.execute(
            """
            INSERT INTO series (
                id, source_id, ref, title, unit, geo_level, geo, frequency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (series_id, source_id, ref, title, unit, geo_level, geo, frequency),
        )
        return series_id

    def put_point(self, series_id: str, date: str, value: object, revision: str) -> bool:
        """Keep every vintage. A return to an earlier value is a new vintage.

        Re-running the latest vintage is a no-op. A→B→A under the same
        revision base stores three rows.
        """
        stored = canonical_number(value)
        rows = self.conn.execute(
            "SELECT revision, value FROM points WHERE series_id = ? AND date = ?",
            (series_id, date),
        ).fetchall()
        matched = [
            row
            for row in rows
            if row["revision"] == revision or str(row["revision"]).startswith(revision + "#")
        ]
        if not matched:
            return self._keep_point(series_id, date, stored, revision)
        latest = max(matched, key=lambda row: _revision_suffix(revision, str(row["revision"])))
        if canonical_number(latest["value"]) == stored:
            return False
        suffix = max(_revision_suffix(revision, str(row["revision"])) for row in matched)
        return self._keep_point(series_id, date, stored, f"{revision}#{suffix + 1}")

    def _keep_point(self, series_id: str, date: str, stored: str, revision: str) -> bool:
        meta = self._series_meta(series_id)
        row = {
            "series_id": series_id,
            "date": date,
            "value": stored,
            "revision": revision,
            "source_id": meta["source_id"],
            "fetched_at": utc_now(),
            "region": meta["region"],
            "currency": meta["currency"],
            "unit": meta["unit"],
            "batch_id": self._batch_id or str(uuid.uuid4()),
        }
        if self._batch_id:
            self._staged.append(row)
            return True
        status = self._commit_staged(row["batch_id"], [row])
        return status == "pooled"

    def _series_meta(self, series_id: str) -> dict[str, str]:
        series = self.conn.execute(
            "SELECT source_id, unit, currency, region, jump_limit FROM series WHERE id = ?",
            (series_id,),
        ).fetchone()
        if series is None:
            raise RuntimeError("series is not in the store")
        region = str(series["region"] or "")
        if region not in {"us-east", "za"}:
            region = "us-east"
        currency = str(series["currency"] or "") or "none"
        return {
            "source_id": str(series["source_id"]),
            "unit": str(series["unit"]),
            "currency": currency,
            "region": region,
            "jump_limit": str(series["jump_limit"] if series["jump_limit"] is not None else DEFAULT_JUMP_LIMIT),
        }

    def _commit_staged(self, batch_id: str, staged: list[dict[str, Any]]) -> str:
        by_series: dict[str, list[dict[str, Any]]] = {}
        for row in staged:
            by_series.setdefault(row["series_id"], []).append(row)
        failed = False
        reasons: list[str] = []
        for series_id, rows in by_series.items():
            meta = self._series_meta(series_id)
            last = self._last_value(series_id)
            official = {
                str(item["date"]): parse_decimal(item["value"])
                for item in self.conn.execute(
                    "SELECT date, value FROM official_values WHERE series_id = ?",
                    (series_id,),
                ).fetchall()
            }
            observations = [
                Observation(
                    source=row["source_id"],
                    fetched_at=row["fetched_at"],
                    region=row["region"],
                    currency=row["currency"],
                    unit=row["unit"],
                    batch_id=batch_id,
                    series_id=series_id,
                    date=row["date"],
                    value=parse_decimal(row["value"]),
                )
                for row in rows
            ]
            status, reason = assess_batch(
                observations,
                SeriesRule(
                    unit=meta["unit"],
                    currency="" if meta["currency"] == "none" and not self._series_currency_set(series_id) else meta["currency"],
                    region="" if not self._series_region_set(series_id) else meta["region"],
                    last_value=last,
                    jump_limit=jump_limit_of(meta["jump_limit"]),
                    official=official,
                ),
            )
            if status != "pooled":
                failed = True
                if reason and reason not in reasons:
                    reasons.append(reason)
        status = "held" if failed else "pooled"
        reason = "; ".join(reasons)
        first = staged[0]
        self.conn.execute(
            """
            INSERT INTO batches (
                id, source_id, series_id, region, currency, unit, fetched_at, status, reason, jump_limit, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                first["source_id"],
                first["series_id"],
                first["region"],
                first["currency"],
                first["unit"],
                first["fetched_at"],
                status,
                reason,
                float(jump_limit_of(self._series_meta(first["series_id"])["jump_limit"])),
                utc_now(),
            ),
        )
        for row in staged:
            self.conn.execute(
                """
                INSERT INTO batch_points (
                    batch_id, series_id, date, value, source_id, fetched_at, region, currency, unit, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    row["series_id"],
                    row["date"],
                    row["value"],
                    row["source_id"],
                    row["fetched_at"],
                    row["region"],
                    row["currency"],
                    row["unit"],
                    row["revision"],
                ),
            )
            if status == "pooled":
                self._insert_point(row, batch_id)
        if status == "pooled":
            for series_id in by_series:
                meta = self._series_meta(series_id)
                self.conn.execute(
                    """
                    UPDATE series
                    SET currency = CASE WHEN currency = '' THEN ? ELSE currency END,
                        region = CASE WHEN region = '' THEN ? ELSE region END
                    WHERE id = ?
                    """,
                    (meta["currency"], meta["region"], series_id),
                )
        return status

    def _series_currency_set(self, series_id: str) -> bool:
        row = self.conn.execute("SELECT currency FROM series WHERE id = ?", (series_id,)).fetchone()
        return bool(row and str(row["currency"] or "").strip())

    def _series_region_set(self, series_id: str) -> bool:
        row = self.conn.execute("SELECT region FROM series WHERE id = ?", (series_id,)).fetchone()
        return bool(row and str(row["region"] or "").strip())

    def _last_value(self, series_id: str) -> Decimal | None:
        row = self.conn.execute(
            "SELECT value FROM points WHERE series_id = ? ORDER BY date DESC, rowid DESC LIMIT 1",
            (series_id,),
        ).fetchone()
        if row is None:
            return None
        return parse_decimal(row["value"])

    def _insert_point(self, row: dict[str, Any], batch_id: str) -> None:
        self.conn.execute(
            """
            INSERT INTO points (
                series_id, date, value, fetched_at, revision, source_id, region, currency, unit, batch_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["series_id"],
                row["date"],
                row["value"],
                row["fetched_at"],
                row["revision"],
                row["source_id"],
                row["region"],
                row["currency"],
                row["unit"],
                batch_id,
            ),
        )

    def release_batch(self, batch_id: str) -> str:
        return self._move_batch(batch_id, "release")

    def reject_batch(self, batch_id: str) -> str:
        return self._move_batch(batch_id, "reject")

    def remove_batch(self, batch_id: str) -> str:
        return self._move_batch(batch_id, "remove")

    def held_batches(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM batches WHERE status = 'held' ORDER BY created_at, id"
        ).fetchall()
        return [dict(row) for row in rows]

    def _move_batch(self, batch_id: str, action: str) -> str:
        row = self.conn.execute("SELECT status FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if row is None:
            raise RuntimeError("batch is not in the store")
        status = str(row["status"])
        if action == "reject":
            if status != "held":
                raise RuntimeError("only a held batch can be rejected")
            self.conn.execute("UPDATE batches SET status = 'rejected' WHERE id = ?", (batch_id,))
            return "rejected"
        if action == "release":
            if status != "held":
                raise RuntimeError("only a held batch can be released")
            points = self.conn.execute(
                "SELECT * FROM batch_points WHERE batch_id = ?",
                (batch_id,),
            ).fetchall()
            for point in points:
                self._insert_point(dict(point), batch_id)
            self.conn.execute(
                "UPDATE batches SET status = 'pooled', reason = '' WHERE id = ?",
                (batch_id,),
            )
            return "pooled"
        if action == "remove":
            if status != "rejected":
                self.conn.execute("DELETE FROM points WHERE batch_id = ?", (batch_id,))
                self.conn.execute("UPDATE batches SET status = 'removed' WHERE id = ?", (batch_id,))
                return "removed"
            return "rejected"
        raise RuntimeError("unknown batch action")

    def put_event(
        self,
        source_id: str,
        kind: str,
        geo: str,
        date: str,
        title: str,
        body: str,
        url: str,
    ) -> bool:
        existing = self.conn.execute(
            """
            SELECT 1 FROM events
            WHERE source_id = ? AND kind = ? AND geo = ? AND date = ?
              AND title = ? AND body = ? AND url = ?
            """,
            (source_id, kind, geo, date, title, body, url),
        ).fetchone()
        if existing is not None:
            return False
        self.conn.execute(
            """
            INSERT INTO events (
                id, source_id, kind, geo, date, title, body, url, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), source_id, kind, geo, date, title, body, url, utc_now()),
        )
        return True

    def latest_event_body(self, source_id: str, kind: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT body FROM events
            WHERE source_id = ? AND kind = ?
            ORDER BY fetched_at DESC, rowid DESC
            LIMIT 1
            """,
            (source_id, kind),
        ).fetchone()
        if row is None:
            return None
        return str(row["body"])

    def finish_run(self, source_id: str, started: str, rows: int, error: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO fetch_runs (id, source_id, started, finished, rows, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), source_id, started, utc_now(), rows, error),
        )
        self.conn.commit()

    def status(self) -> list[dict[str, Any]]:
        sources = self.conn.execute(
            "SELECT * FROM feed_sources ORDER BY id"
        ).fetchall()
        rows: list[dict[str, Any]] = []
        for source in sources:
            points = self.conn.execute(
                """
                SELECT COUNT(*) AS n FROM points p
                JOIN series s ON s.id = p.series_id
                WHERE s.source_id = ?
                """,
                (source["id"],),
            ).fetchone()["n"]
            events = self.conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE source_id = ?",
                (source["id"],),
            ).fetchone()["n"]
            last = self.conn.execute(
                """
                SELECT started, finished, rows, error FROM fetch_runs
                WHERE source_id = ?
                ORDER BY started DESC, rowid DESC
                LIMIT 1
                """,
                (source["id"],),
            ).fetchone()
            success = self.conn.execute(
                """
                SELECT finished, rows FROM fetch_runs
                WHERE source_id = ? AND error IS NULL
                ORDER BY finished DESC, rowid DESC
                LIMIT 1
                """,
                (source["id"],),
            ).fetchone()
            mode = "copy" if not str(source["licence"]).startswith("Linked only.") else "linked"
            key_name = str(source["key_name"] or "")
            required = bool(source["key_required"])
            present = key_is_set(key_name) if required else True
            missing = required and not present
            enabled = 0 if missing or not source["enabled"] else 1
            rows.append(
                {
                    "id": source["id"],
                    "name": source["name"],
                    "kind": source["kind"],
                    "url": source["url"],
                    "licence": source["licence"],
                    "terms_checked_at": source["terms_checked_at"],
                    "cadence": source["cadence"],
                    "enabled": enabled,
                    "key_name": key_name,
                    "key_required": required,
                    "key_set": present,
                    "state": "off" if enabled == 0 else "on",
                    "note": f"needs key {key_name}" if missing else "",
                    "mode": mode,
                    "points": points,
                    "events": events,
                    "last_started": None if last is None else last["started"],
                    "last_finished": None if last is None else last["finished"],
                    "last_rows": None if last is None else last["rows"],
                    "last_error": None if last is None else last["error"],
                    "last_success": None if success is None else success["finished"],
                    "success_rows": None if success is None else success["rows"],
                }
            )
        return rows

