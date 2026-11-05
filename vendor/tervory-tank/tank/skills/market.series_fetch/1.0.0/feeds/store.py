"""Append-only points and events. A re-run does not overwrite a number."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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
            self._insert_point(series_id, date, stored, revision)
            return True
        latest = max(matched, key=lambda row: _revision_suffix(revision, str(row["revision"])))
        if canonical_number(latest["value"]) == stored:
            return False
        suffix = max(_revision_suffix(revision, str(row["revision"])) for row in matched)
        self._insert_point(series_id, date, stored, f"{revision}#{suffix + 1}")
        return True

    def _insert_point(self, series_id: str, date: str, stored: str, revision: str) -> None:
        self.conn.execute(
            """
            INSERT INTO points (series_id, date, value, fetched_at, revision)
            VALUES (?, ?, ?, ?, ?)
            """,
            (series_id, date, stored, utc_now(), revision),
        )

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

