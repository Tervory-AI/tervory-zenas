"""Durable tenant watches and an SMTP outbox; source snapshots are committed whole."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, timedelta
from uuid import UUID, uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_zenas.regulation_watches (
    tenant_id uuid NOT NULL REFERENCES agent_zenas.tenants(tenant_id),
    id uuid NOT NULL,
    region text NOT NULL CHECK (region IN ('us-east', 'za')),
    source text NOT NULL,
    topic text NOT NULL,
    options jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled boolean NOT NULL DEFAULT true,
    initialized boolean NOT NULL DEFAULT false,
    seen jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_success timestamptz,
    PRIMARY KEY (tenant_id, id)
);
CREATE TABLE IF NOT EXISTS agent_zenas.regulation_changes (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    watch_id uuid NOT NULL,
    document jsonb NOT NULL,
    previous jsonb,
    summary text,
    letter jsonb,
    mail_state text NOT NULL DEFAULT 'waiting',
    attempts integer NOT NULL DEFAULT 0,
    next_attempt timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, watch_id)
      REFERENCES agent_zenas.regulation_watches(tenant_id, id)
);
ALTER TABLE agent_zenas.regulation_changes ADD COLUMN IF NOT EXISTS event_sent boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS regulation_pending
    ON agent_zenas.regulation_changes(tenant_id, mail_state, next_attempt);
"""


def decoded(row):
    if row is None:
        return None
    result = dict(row)
    for key in ("options", "seen", "document", "previous", "letter"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    for key in ("tenant_id", "id", "watch_id"):
        if key in result:
            result[key] = str(result[key])
    return result


def compare(watch, documents):
    """Keep previous versions even when a rolling source listing omits them."""
    seen = dict(watch["seen"])
    changes = []
    for document in documents:
        old = seen.get(document["id"])
        if old is not None and old["version"] == document["version"]:
            continue
        # Sources provide publication dates, not intraday publication times.
        after_creation = date.fromisoformat(document["date"]) > watch["created_at"].date()
        if after_creation:
            changes.append((document, old))
        seen[document["id"]] = document
    return seen, changes


class PgWatches:
    def __init__(self, conn):
        self.conn = conn

    async def apply_schema(self):
        await self.conn.execute(SCHEMA)

    @asynccontextmanager
    async def daily_lock(self):
        # Session lock on this region's database; no lease expires mid-SMTP.
        locked = await self.conn.fetchval("SELECT pg_try_advisory_lock(92742019)")
        try:
            yield locked
        finally:
            if locked:
                await self.conn.execute("SELECT pg_advisory_unlock(92742019)")

    async def tenants(self, region):
        # Scheduler-only enumeration; every subsequent access supplies tenant_id.
        return [
            str(row["tenant_id"])
            for row in await self.conn.fetch(
                "SELECT tenant_id FROM agent_zenas.tenants WHERE home_region=$1", region
            )
        ]

    async def add_watch(self, tenant_id, region, source, topic, options, now):
        row = await self.conn.fetchrow(
            """
            INSERT INTO agent_zenas.regulation_watches
              (tenant_id,id,region,source,topic,options,created_at)
            SELECT tenant_id,$2,$3,$4,$5,$6::jsonb,$7 FROM agent_zenas.tenants
            WHERE tenant_id=$1 AND home_region=$3 RETURNING *
        """,
            UUID(tenant_id),
            uuid4(),
            region,
            source,
            topic,
            json.dumps(options),
            now,
        )
        return decoded(row)

    async def watches(self, tenant_id, region):
        return [
            decoded(row)
            for row in await self.conn.fetch(
                """
            SELECT * FROM agent_zenas.regulation_watches
            WHERE tenant_id=$1 AND region=$2 ORDER BY created_at,id
        """,
                UUID(tenant_id),
                region,
            )
        ]

    async def set_enabled(self, tenant_id, watch_id, enabled):
        return decoded(
            await self.conn.fetchrow(
                """
            UPDATE agent_zenas.regulation_watches SET enabled=$3
            WHERE tenant_id=$1 AND id=$2 RETURNING *
        """,
                UUID(tenant_id),
                UUID(watch_id),
                enabled,
            )
        )

    async def record_snapshot(self, tenant_id, watch_id, documents, now):
        async with self.conn.transaction():
            watch = decoded(
                await self.conn.fetchrow(
                    """
                SELECT * FROM agent_zenas.regulation_watches
                WHERE tenant_id=$1 AND id=$2 FOR UPDATE
            """,
                    UUID(tenant_id),
                    UUID(watch_id),
                )
            )
            if not watch or not watch["enabled"]:
                return 0
            seen, changes = compare(watch, documents)
            for document, previous in changes:
                await self.conn.execute(
                    """
                    INSERT INTO agent_zenas.regulation_changes
                      (tenant_id,id,watch_id,document,previous,created_at,next_attempt)
                    VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$6)
                """,
                    UUID(tenant_id),
                    uuid4(),
                    UUID(watch_id),
                    json.dumps(document),
                    json.dumps(previous),
                    now,
                )
            await self.conn.execute(
                """
                UPDATE agent_zenas.regulation_watches
                SET seen=$3::jsonb,initialized=true,last_success=$4
                WHERE tenant_id=$1 AND id=$2
            """,
                UUID(tenant_id),
                UUID(watch_id),
                json.dumps(seen),
                now,
            )
            return len(changes)

    async def changes(self, tenant_id):
        return [
            decoded(row)
            for row in await self.conn.fetch(
                """
            SELECT * FROM agent_zenas.regulation_changes
            WHERE tenant_id=$1 ORDER BY created_at,id
        """,
                UUID(tenant_id),
            )
        ]

    async def mark_event_sent(self, tenant_id, change_id):
        await self.conn.execute("""UPDATE agent_zenas.regulation_changes SET event_sent=true
            WHERE tenant_id=$1 AND id=$2""", UUID(tenant_id), UUID(change_id))

    async def save_summary(self, tenant_id, change_id, summary, letter):
        await self.conn.execute(
            """
            UPDATE agent_zenas.regulation_changes
            SET summary=$3,letter=$4::jsonb,mail_state='pending'
            WHERE tenant_id=$1 AND id=$2 AND mail_state='waiting'
        """,
            UUID(tenant_id),
            UUID(change_id),
            summary,
            json.dumps(letter),
        )

    async def pending_mail(self, tenant_id, now):
        return [
            str(row["id"])
            for row in await self.conn.fetch(
                """
            SELECT id FROM agent_zenas.regulation_changes
            WHERE tenant_id=$1 AND mail_state='pending' AND next_attempt <= $2
            ORDER BY created_at,id
        """,
                UUID(tenant_id),
                now,
            )
        ]

    async def claim_mail(self, tenant_id, change_id, now):
        async with self.conn.transaction():
            row = decoded(
                await self.conn.fetchrow(
                    """
                UPDATE agent_zenas.regulation_changes SET mail_state='sending',attempts=attempts+1
                WHERE tenant_id=$1 AND id=$2 AND mail_state='pending' AND next_attempt <= $3
                RETURNING *
            """,
                    UUID(tenant_id),
                    UUID(change_id),
                    now,
                )
            )
            if row is None:
                return None
            row["allowed"] = bool(
                await self.conn.fetchval(
                    """
                SELECT enabled FROM agent_zenas.regulation_watches
                WHERE tenant_id=$1 AND id=$2
            """,
                    UUID(tenant_id),
                    UUID(row["watch_id"]),
                )
            )
            return row

    async def finish_mail(self, tenant_id, change_id, state, now):
        await self.conn.execute(
            """
            UPDATE agent_zenas.regulation_changes
            SET mail_state=$3, next_attempt=$4::timestamptz +
                make_interval(secs => LEAST(3600,60 * power(2,LEAST(attempts-1,6)))::integer)
            WHERE tenant_id=$1 AND id=$2 AND mail_state='sending'
        """,
            UUID(tenant_id),
            UUID(change_id),
            state,
            now,
        )


class MemoryWatches:
    """Offline double for the same state transitions, never a production fallback."""

    def __init__(self):
        self.rows = {}
        self.records = {}
        self.locked = False

    @asynccontextmanager
    async def daily_lock(self):
        if self.locked:
            yield False
            return
        self.locked = True
        try:
            yield True
        finally:
            self.locked = False

    async def tenants(self, region):
        return sorted({t for (t, _), w in self.rows.items() if w["region"] == region})

    async def add_watch(self, tenant_id, region, source, topic, options, now):
        row = dict(
            tenant_id=tenant_id,
            id=str(uuid4()),
            region=region,
            source=source,
            topic=topic,
            options=options,
            enabled=True,
            initialized=False,
            seen={},
            last_success=None,
            created_at=now,
        )
        self.rows[tenant_id, row["id"]] = row
        return dict(row)

    async def watches(self, tenant_id, region):
        return [
            dict(w)
            for (t, _), w in self.rows.items()
            if t == tenant_id and w["region"] == region
        ]

    async def set_enabled(self, tenant_id, watch_id, enabled):
        row = self.rows.get((tenant_id, watch_id))
        if row:
            row["enabled"] = enabled
        return row

    async def record_snapshot(self, tenant_id, watch_id, documents, now):
        watch = self.rows.get((tenant_id, watch_id))
        if not watch or not watch["enabled"]:
            return 0
        seen, changes = compare(watch, documents)
        for document, previous in changes:
            row = dict(
                tenant_id=tenant_id,
                id=str(uuid4()),
                watch_id=watch_id,
                document=document,
                previous=previous,
                summary=None,
                letter=None,
                mail_state="waiting",
                attempts=0,
                next_attempt=now,
                created_at=now,
            )
            self.records[tenant_id, row["id"]] = row
        watch.update(seen=seen, initialized=True, last_success=now)
        return len(changes)

    async def changes(self, tenant_id):
        return [dict(row) for (t, _), row in self.records.items() if t == tenant_id]

    async def mark_event_sent(self, tenant_id, change_id):
        row = self.records.get((tenant_id, change_id))
        if row is not None:
            row['event_sent'] = True

    async def save_summary(self, tenant_id, change_id, summary, letter):
        row = self.records.get((tenant_id, change_id))
        if row and row["mail_state"] == "waiting":
            row.update(summary=summary, letter=letter, mail_state="pending")

    async def pending_mail(self, tenant_id, now):
        return [
            r["id"]
            for r in await self.changes(tenant_id)
            if r["mail_state"] == "pending" and r["next_attempt"] <= now
        ]

    async def claim_mail(self, tenant_id, change_id, now):
        row = self.records.get((tenant_id, change_id))
        if not row or row["mail_state"] != "pending" or row["next_attempt"] > now:
            return None
        row.update(mail_state="sending", attempts=row["attempts"] + 1)
        return dict(row, allowed=self.rows[tenant_id, row["watch_id"]]["enabled"])

    async def finish_mail(self, tenant_id, change_id, state, now):
        row = self.records.get((tenant_id, change_id))
        if row and row["mail_state"] == "sending":
            row.update(
                mail_state=state,
                next_attempt=now
                + timedelta(seconds=min(3600, 60 * 2 ** min(row["attempts"] - 1, 6))),
            )
