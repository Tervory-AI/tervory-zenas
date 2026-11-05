"""Durable delivery state and counts. No visitor IDs, search text or IPs."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

EVENTS = frozenset({"searches", "setups_opened", "swaps", "quotes", "no_results"})
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = 300


def retention_days() -> int:
    """Finite retention even when the environment is missing or misconfigured."""
    try:
        days = int(os.environ.get("RIG_QUOTE_RETENTION_DAYS", "30") or "30")
    except ValueError:
        raise ValueError("RIG_QUOTE_RETENTION_DAYS must be 1 to 365") from None
    if not 1 <= days <= 365:
        raise ValueError("RIG_QUOTE_RETENTION_DAYS must be 1 to 365")
    return days


def _state(state: dict[str, Any]) -> dict[str, Any]:
    due = state.get("next_attempt_at")
    if isinstance(due, (int, float)):
        due = datetime.fromtimestamp(due, timezone.utc)
    return {"status": "needs_person" if state.get("needs_attention") else state["status"],
            "error": state.get("error"), "attempts": state["attempts"],
            "next_attempt_at": due.isoformat() if isinstance(due, datetime) else due}


class MemoryQuotes:
    def __init__(self, store: Any, clock=time.time) -> None:
        self.store = store
        self.clock = clock
        self.states: dict[tuple[str, str], dict[str, Any]] = {}
        self.counts: Counter = Counter()
        self.located_counts: Counter = Counter()
        self.lock = asyncio.Lock()
        self.search_events: dict[tuple[str, str], float] = {}

    async def create(self, tenant: str, quote: dict[str, Any], *, hold: bool = False) -> None:
        await self.store.add_quote(tenant, quote["id"], quote["payload"], quote["contact"])
        self.states[(tenant, quote["id"])] = {"status": "pending", "error": "tenant_quote_limit" if hold else None,
            "attempts": 0, "needs_attention": hold, "next_attempt_at": None if hold else self.clock(),
            "created_at": datetime.fromtimestamp(self.clock(), timezone.utc)}
        if not hold:
            await self.count(tenant, "quotes")
        if any(line.get("store") and line.get("public", {}).get("status") == "available_to_order"
                   for line in quote["payload"].get("locate", [])):
            self.located_counts[(tenant, datetime.now(timezone.utc).date())] += 1

    async def count(self, tenant: str, event: str) -> None:
        if event not in EVENTS:
            raise ValueError("Unknown event")
        self.counts[(tenant, datetime.now(timezone.utc).date(), event)] += 1

    async def analytics(self, tenant: str, since: date) -> dict[str, int]:
        return {event: sum(n for (t, day, e), n in self.counts.items()
                           if t == tenant and day >= since and e == event) for event in sorted(EVENTS)}

    async def settled_search(self, tenant: str, event_id: str, no_results: bool) -> bool:
        async with self.lock:
            now = self.clock()
            self.search_events = {key: at for key, at in self.search_events.items() if at > now - 86400}
            key = (tenant, event_id)
            if key in self.search_events:
                return False
            self.search_events[key] = now
            await self.count(tenant, "searches")
            if no_results:
                await self.count(tenant, "no_results")
            return True

    async def claim(self, tenant: str, quote_id: str) -> dict[str, Any] | None:
        async with self.lock:
            state = self.states.get((tenant, quote_id))
            if (not state or state["status"] != "pending" or state["needs_attention"]
                    or state["next_attempt_at"] > self.clock()):
                return None
            state["status"] = "sending"
            state["attempts"] += 1
            row = next(row for row in await self.store.quotes_for(tenant) if row["id"] == quote_id)
            return {**row, "_attempt": state["attempts"]}

    async def finish(self, tenant: str, quote_id: str, error: str | None, attempt: int | None = None) -> None:
        state = self.states[(tenant, quote_id)]
        if attempt is not None and attempt != state["attempts"]:
            return
        state.update(status="pending" if error else "sent", error=error,
                     needs_attention=bool(error and state["attempts"] >= MAX_ATTEMPTS),
                     next_attempt_at=self.clock() + BACKOFF_SECONDS * 2 ** (state["attempts"] - 1)
                     if error and state["attempts"] < MAX_ATTEMPTS else None)
        if not error:
            await self.store.mark_sent(tenant, quote_id)

    async def pending(self, tenant: str) -> list[str]:
        return [q for (t, q), state in self.states.items() if t == tenant and state["status"] == "pending"
                and not state["needs_attention"] and state["next_attempt_at"] <= self.clock()][:25]

    async def status(self, tenant: str, quote_id: str) -> dict[str, Any] | None:
        state = self.states.get((tenant, quote_id))
        return _state(state) if state else None

    async def attention(self, tenant: str, *, limit: int = 100,
                        after: tuple[datetime, str] | None = None) -> list[dict]:
        rows = sorted((state["created_at"], q, state) for (t, q), state in self.states.items()
                      if t == tenant and state["needs_attention"]
                      and (after is None or (state["created_at"], q) > after))
        return [{"request_id": q, "created_at": created.isoformat(), **_state(state)}
                for created, q, state in rows[:limit]]

    async def retry_now(self, tenant: str, quote_id: str, *, resume: bool = False) -> None:
        state = self.states.get((tenant, quote_id))
        if not state or state["status"] != "pending":
            return
        if resume and state["needs_attention"]:
            if state["error"] == "tenant_quote_limit":
                await self.count(tenant, "quotes")
            state.update(attempts=0, needs_attention=False)
        if not state["needs_attention"]:
            state["next_attempt_at"] = self.clock()


class PgQuotes:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def purge(self, tenant: str) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute("""DELETE FROM agent_rig.quotes q
                WHERE q.tenant_id=$1 AND q.created_at < now() - $2 * interval '1 day'
                AND NOT EXISTS (SELECT 1 FROM agent_rig.quote_handoffs h
                    WHERE h.tenant_id=q.tenant_id AND h.quote_id=q.id AND h.status='sending'
                    AND h.updated_at > now() - interval '5 minutes')""", UUID(tenant), retention_days())
        return int(result.split()[-1])

    async def expired_tenants(self) -> list[str]:
        # Maintenance discovery only. Every subsequent deletion is tenant-scoped,
        # including quotes whose retailer has since been removed.
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT DISTINCT tenant_id FROM agent_rig.quotes
                WHERE created_at < now() - $1 * interval '1 day'""", retention_days())
        return [str(row["tenant_id"]) for row in rows]

    async def create(self, tenant: str, quote: dict[str, Any], *, hold: bool = False) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            from rigbox.training import enqueue

            await conn.execute("""INSERT INTO agent_rig.quotes (tenant_id, id, payload, contact)
                VALUES ($1, $2, $3::jsonb, $4::jsonb)""", UUID(tenant), quote["id"],
                json.dumps(quote["payload"]), json.dumps(quote["contact"]))
            await enqueue(conn, tenant, "rig_quote", quote["id"], quote["payload"],
                          {"request_id": quote["id"]})
            await conn.execute("""INSERT INTO agent_rig.quote_handoffs
                (tenant_id, quote_id, needs_attention, error, next_attempt_at)
                VALUES ($1, $2, $3, $4, CASE WHEN $3 THEN NULL ELSE now() END)""",
                UUID(tenant), quote["id"], hold, "tenant_quote_limit" if hold else None)
            if not hold:
                await self._count(conn, tenant, "quotes")
            if any(line.get("store") and line.get("public", {}).get("status") == "available_to_order"
                   for line in quote["payload"].get("locate", [])):
                await conn.execute("""INSERT INTO agent_rig.widget_counts(tenant_id,day,event,count,located_count)
                    VALUES($1,(now() AT TIME ZONE 'UTC')::date,'quotes',0,1)
                    ON CONFLICT(tenant_id,day,event) DO UPDATE SET
                    located_count=agent_rig.widget_counts.located_count+1""", UUID(tenant))

    async def _count(self, conn: Any, tenant: str, event: str) -> None:
        if event not in EVENTS:
            raise ValueError("Unknown event")
        await conn.execute("""INSERT INTO agent_rig.widget_counts (tenant_id, day, event, count)
            VALUES ($1, (now() AT TIME ZONE 'UTC')::date, $2, 1)
            ON CONFLICT (tenant_id, day, event) DO UPDATE
            SET count = agent_rig.widget_counts.count + 1""", UUID(tenant), event)

    async def count(self, tenant: str, event: str) -> None:
        async with self.pool.acquire() as conn:
            await self._count(conn, tenant, event)

    async def analytics(self, tenant: str, since: date) -> dict[str, int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT event, sum(count) AS count FROM agent_rig.widget_counts
                WHERE tenant_id = $1 AND day >= $2 GROUP BY event""", UUID(tenant), since)
        totals = {event: 0 for event in sorted(EVENTS)}
        totals.update({row["event"]: int(row["count"]) for row in rows})
        return totals

    async def settled_search(self, tenant: str, event_id: str, no_results: bool) -> bool:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("""DELETE FROM agent_rig.widget_search_events
                WHERE tenant_id=$1 AND created_at < now() - interval '1 day'""", UUID(tenant))
            accepted = await conn.fetchval("""INSERT INTO agent_rig.widget_search_events (tenant_id, event_id)
                VALUES ($1,$2) ON CONFLICT DO NOTHING RETURNING event_id""", UUID(tenant), UUID(event_id))
            if accepted is None:
                return False
            await self._count(conn, tenant, "searches")
            if no_results:
                await self._count(conn, tenant, "no_results")
            return True

    async def _exhausted(self, conn: Any, tenant: str) -> None:
        # A crash during the last allowed attempt must also stop automatically.
        await conn.execute("""UPDATE agent_rig.quote_handoffs
            SET status='pending', needs_attention=true, error='delivery_attempts_exhausted', next_attempt_at=NULL
            WHERE tenant_id=$1 AND attempts >= $2 AND NOT needs_attention AND
            (status='pending' OR (status='sending' AND updated_at < now() - interval '5 minutes'))""",
            UUID(tenant), MAX_ATTEMPTS)

    async def claim(self, tenant: str, quote_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn, conn.transaction():
            await self._exhausted(conn, tenant)
            claimed = await conn.fetchval("""UPDATE agent_rig.quote_handoffs
                SET status = 'sending', updated_at = now(), attempts = attempts + 1
                WHERE tenant_id = $1 AND quote_id = $2 AND NOT needs_attention AND attempts < $3 AND
                EXISTS (SELECT 1 FROM agent_rig.quotes q WHERE q.tenant_id=$1 AND q.id=$2
                    AND q.created_at >= now() - $4 * interval '1 day') AND
                next_attempt_at <= now() AND
                (status = 'pending' OR (status = 'sending' AND updated_at < now() - interval '5 minutes'))
                RETURNING attempts""", UUID(tenant), quote_id, MAX_ATTEMPTS, retention_days())
            if not claimed:
                return None
            row = await conn.fetchrow("""SELECT id, payload, contact FROM agent_rig.quotes
                WHERE tenant_id = $1 AND id = $2""", UUID(tenant), quote_id)
        return {"id": row["id"], "_attempt": claimed,
                **{key: json.loads(row[key]) if isinstance(row[key], str) else row[key]
                                  for key in ("payload", "contact")}}

    async def finish(self, tenant: str, quote_id: str, error: str | None, attempt: int | None = None) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            updated = await conn.fetchval("""UPDATE agent_rig.quote_handoffs
                SET status = $3, error = $4, updated_at = now(),
                needs_attention = ($4::text IS NOT NULL AND attempts >= $5),
                next_attempt_at = CASE WHEN $4::text IS NULL OR attempts >= $5 THEN NULL
                    ELSE now() + ($6 * power(2, attempts - 1)) * interval '1 second' END
                WHERE tenant_id = $1 AND quote_id = $2 AND ($7::int IS NULL OR attempts=$7)
                RETURNING quote_id""", UUID(tenant), quote_id,
                "pending" if error else "sent", error, MAX_ATTEMPTS, BACKOFF_SECONDS, attempt)
            if updated and not error:
                await conn.execute("""UPDATE agent_rig.quotes SET sent_at = now()
                    WHERE tenant_id = $1 AND id = $2""", UUID(tenant), quote_id)

    async def pending(self, tenant: str) -> list[str]:
        async with self.pool.acquire() as conn:
            await self._exhausted(conn, tenant)
            rows = await conn.fetch("""SELECT quote_id FROM agent_rig.quote_handoffs
                WHERE tenant_id = $1 AND NOT needs_attention AND next_attempt_at <= now() AND (status = 'pending' OR
                    (status = 'sending' AND updated_at < now() - interval '5 minutes'))
                ORDER BY updated_at LIMIT 25""", UUID(tenant))
        return [row["quote_id"] for row in rows]

    async def status(self, tenant: str, quote_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT status, error, attempts, next_attempt_at, needs_attention FROM agent_rig.quote_handoffs
                WHERE tenant_id = $1 AND quote_id = $2""", UUID(tenant), quote_id)
        return _state(dict(row)) if row else None

    async def attention(self, tenant: str, *, limit: int = 100,
                        after: tuple[datetime, str] | None = None) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT h.quote_id, h.status, h.error, h.attempts,
                h.next_attempt_at, h.needs_attention, q.created_at
                FROM agent_rig.quote_handoffs h JOIN agent_rig.quotes q
                  ON q.tenant_id=h.tenant_id AND q.id=h.quote_id
                WHERE h.tenant_id=$1 AND h.needs_attention
                  AND ($2::timestamptz IS NULL OR (q.created_at, h.quote_id) > ($2, $3::text))
                ORDER BY q.created_at, h.quote_id LIMIT $4""", UUID(tenant),
                after[0] if after else None, after[1] if after else None, limit)
        return [{"request_id": row["quote_id"], "created_at": row["created_at"].isoformat(), **_state(dict(row))}
                for row in rows]

    async def retry_now(self, tenant: str, quote_id: str, *, resume: bool = False) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            held = await conn.fetchval("""SELECT needs_attention AND error='tenant_quote_limit'
                FROM agent_rig.quote_handoffs WHERE tenant_id=$1 AND quote_id=$2 AND status='pending'
                FOR UPDATE""", UUID(tenant), quote_id)
            await conn.execute("""UPDATE agent_rig.quote_handoffs SET next_attempt_at=now(),
                attempts=CASE WHEN $3 AND needs_attention THEN 0 ELSE attempts END,
                needs_attention=false WHERE tenant_id=$1 AND quote_id=$2 AND status='pending'
                AND (NOT needs_attention OR $3)""", UUID(tenant), quote_id, resume)
            if resume and held:
                await self._count(conn, tenant, "quotes")
