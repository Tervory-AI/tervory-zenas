"""Authoritative tenant records and transactional index jobs. Async SQL only."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from hermes.record_schema import PLATFORM_TENANT, schema_for, schema_sql

log = logging.getLogger("hermes.records")
MAX_EMBED_ATTEMPTS = 5

_SCHEMA = re.compile(r"agent_[a-z][a-z0-9_]{0,40}")
_OWNED_TABLES = frozenset(
    {
        "memory_records",
        "memory_index_jobs",
        "memory_publish_policy",
        "memory_publish_log",
    }
)


def _sql(statement: str, schema: str) -> str:
    """Schema names come only from Hermes's agent allowlist; checked again here."""
    if not _SCHEMA.fullmatch(schema):
        raise ValueError("invalid schema name")
    return statement.format(schema=schema)


def _alter_owner(schema: str, table: str) -> str:
    """DDL ownership uses checked schema + fixed table allowlist only."""
    if not _SCHEMA.fullmatch(schema):
        raise ValueError("invalid schema name")
    if table not in _OWNED_TABLES:
        raise ValueError("invalid table name")
    return "ALTER TABLE {schema}.{table} OWNER TO {schema}".format(
        schema=schema, table=table
    )


class ClaimTooLong(ValueError):
    """The pinned encoder tokenizer rejects the complete claim."""


def public_row(row: Any) -> dict:
    result = dict(row)
    for key in ("evidence", "meta"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    for key, value in list(result.items()):
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


class Records:
    def __init__(
        self, pool: Any, index: Any, encoder: Any, model: str, agents: list[str]
    ):
        self.pool, self.index, self.encoder, self.model = pool, index, encoder, model
        self.agents = frozenset(agents)

    def schema(self, agent: str) -> str:
        if agent not in self.agents:
            raise ValueError("Unknown agent")
        return schema_for(agent)

    async def setup(self) -> None:
        # DDL only. Never copy or backfill legacy vectors into these tables.
        async with self.pool.acquire() as conn:
            for agent in sorted(self.agents):
                async with conn.transaction():
                    await conn.execute(schema_sql(agent))
                    s = self.schema(agent)
                    definition = await conn.fetchval(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=to_regclass($1) AND conname=$2",
                        s + ".memory_publish_log", "memory_publish_log_result_check")
                    if definition and "'withdrawn'" not in definition:
                        await conn.execute(_sql("ALTER TABLE {schema}.memory_publish_log DROP CONSTRAINT memory_publish_log_result_check", s))
                        await conn.execute(_sql("ALTER TABLE {schema}.memory_publish_log ADD CONSTRAINT memory_publish_log_result_check CHECK (result IN ('disabled','missing','refused','published','already_published','withdrawn'))", s))
                    # Match the chassis provisioner's existing ownership rule.
                    # Commit DDL + ownership together so restricted agent startup
                    # can safely reapply its schema regardless of startup order.
                    if await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", s
                    ):
                        for table in (
                            "memory_records",
                            "memory_index_jobs",
                            "memory_publish_policy",
                            "memory_publish_log",
                        ):
                            await conn.execute(_alter_owner(s, table))

    async def lock(self, conn: Any, s: str, row_id: uuid.UUID) -> None:
        # Also serializes absent rows: delete tombstones vs recreation of an id.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
            s + ":" + str(row_id),
        )

    async def save(
        self,
        agent: str,
        tenant: str,
        claim: str,
        evidence: list[str],
        *,
        confidence: float = 0.5,
        last_verified: datetime | None = None,
        kind: str = "fact",
        meta: dict | None = None,
        row_id: str | None = None,
        index: bool = True,
    ) -> dict:
        s = self.schema(agent)
        tenant_id, identity = (
            uuid.UUID(tenant),
            uuid.UUID(row_id) if row_id else uuid.uuid4(),
        )
        if index:
            try:
                await asyncio.to_thread(self.encoder.validate, claim)
            except ValueError as exc:
                raise ClaimTooLong(str(exc)) from None
        async with self.pool.acquire() as conn, conn.transaction():
            await self.lock(conn, s, identity)
            row = await conn.fetchrow(
                _sql(
                    """INSERT INTO {schema}.memory_records
                (id,tenant_id,claim,evidence,confidence,last_verified,kind,meta)
                VALUES($1,$2,$3,$4::jsonb,$5,$6,$7,$8::jsonb)
                ON CONFLICT (id) DO NOTHING RETURNING *""",
                    s,
                ),
                identity,
                tenant_id,
                claim,
                json.dumps(evidence),
                confidence,
                last_verified,
                kind,
                json.dumps({**(meta or {}), **({"_index_enabled": False} if not index else {})}),
            )
            if row is None:
                row = await conn.fetchrow(
                    _sql(
                        "SELECT * FROM {schema}.memory_records WHERE id=$1 AND tenant_id=$2 AND tier='client'",
                        s,
                    ),
                    identity,
                    tenant_id,
                )
                if (
                    row is None
                    or row["claim"] != claim
                    or json.loads(row["evidence"]) != evidence
                ):
                    raise ValueError("Memory id conflicts with a different record")
            if index:
                await self.enqueue(conn, s, identity, tenant_id, self.model)
        # The transaction has committed before any inference or vector call.
        if index:
            await self.process_one(agent, tenant_id, identity, self.model)
        saved = await self.get(agent, tenant, str(identity))
        if saved is None:
            raise ValueError("Memory was concurrently deleted")
        return {
            **saved,
            "text": saved["claim"],
            "sources": saved["evidence"],
            "source": next(iter(saved["evidence"]), ""),
            "deduped": False,
            "index_pending": index and saved["embed_model"] != self.model,
        }

    async def enqueue(
        self,
        conn: Any,
        s: str,
        row_id: uuid.UUID,
        tenant: uuid.UUID,
        model: str,
        operation: str = "upsert",
    ) -> None:
        await conn.execute(
            _sql(
                """INSERT INTO {schema}.memory_index_jobs(row_id,tenant_id,target_model,operation)
            VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING""",
                s,
            ),
            row_id,
            tenant,
            model,
            operation,
        )

    async def get(
        self, agent: str, tenant: str, row_id: str, tier: str = "client"
    ) -> dict | None:
        if tier not in ("client", "platform"):
            raise ValueError("Read-only reference corpus")
        owner = PLATFORM_TENANT if tier == "platform" else tenant
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                _sql(
                    """SELECT * FROM {schema}.memory_records
                WHERE id=$1 AND tenant_id=$2 AND tier=$3""",
                    self.schema(agent),
                ),
                uuid.UUID(row_id),
                uuid.UUID(owner),
                tier,
            )
        return public_row(row) if row else None

    async def list(
        self, agent: str, tenant: str, tier: str = "client", limit: int = 50, offset: int = 0
    ) -> list[dict]:
        if tier not in ("client", "platform"):
            raise ValueError("Read-only reference corpus")
        owner = PLATFORM_TENANT if tier == "platform" else tenant
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                _sql(
                    """SELECT * FROM {schema}.memory_records
                WHERE tenant_id=$1 AND tier=$2 ORDER BY created_at DESC,id LIMIT $3 OFFSET $4""",
                    self.schema(agent),
                ),
                uuid.UUID(owner),
                tier,
                limit, offset,
            )
        return [public_row(row) for row in rows]

    async def founder_get(self, row_id: uuid.UUID) -> dict | None:
        """ID-only inspection, called exclusively after founder authorization.

        Only configured agent schemas are searched. A colliding ID is refused
        instead of silently choosing one tenant's or agent's record.
        """
        found = None
        async with self.pool.acquire() as conn:
            for agent in sorted(self.agents):
                row = await conn.fetchrow(
                    _sql(
                        """SELECT id,tenant_id,agent,claim,evidence,confidence,
                    last_verified,embed_model,created_at,updated_at
                    FROM {schema}.memory_records WHERE id=$1""",
                        self.schema(agent),
                    ),
                    row_id,
                )
                if row is not None:
                    if found is not None:
                        raise ValueError("Memory id conflicts with a different record")
                    found = public_row(row)
        return found

    async def recall(
        self,
        agent: str,
        tenant: str,
        query: str,
        kind: str | None,
        limit: int,
        tier: str,
    ) -> list[dict]:
        if tier not in ("client", "platform"):
            raise ValueError("Read-only reference corpus")
        owner = PLATFORM_TENANT if tier == "platform" else tenant
        vector = await asyncio.to_thread(self.encoder.embed, query)
        hits = await asyncio.to_thread(
            self.index.search, agent, owner, tier, vector, self.model, kind, limit
        )
        results = []
        for row_id, score in hits:
            row = await self.get(agent, owner, row_id, tier)
            if (
                row
                and row["embed_model"] == self.model
                and (not kind or row["kind"] == kind)
            ):
                results.append(
                    {
                        **row,
                        "text": row["claim"],
                        "sources": row["evidence"],
                        "source": next(iter(row["evidence"]), ""),
                        "score": score,
                    }
                )
        return results

    async def process_one(
        self,
        agent: str,
        tenant: uuid.UUID,
        row_id: uuid.UUID,
        model: str,
        operation: str = "upsert",
    ) -> bool:
        s = self.schema(agent)
        async with self.pool.acquire() as conn, conn.transaction():
            await self.lock(conn, s, row_id)
            # Same lock order as save/forget: record first, then its index job.
            row = await conn.fetchrow(
                _sql(
                    """SELECT * FROM {schema}.memory_records
                WHERE tenant_id=$1 AND id=$2 FOR UPDATE""",
                    s,
                ),
                tenant,
                row_id,
            )
            job = await conn.fetchrow(
                _sql(
                    """SELECT * FROM {schema}.memory_index_jobs
                WHERE tenant_id=$1 AND row_id=$2 AND target_model=$3 AND operation=$4
                FOR UPDATE SKIP LOCKED""",
                    s,
                ),
                tenant,
                row_id,
                model,
                operation,
            )
            if not job:
                return False
            if (operation == "upsert" and row is not None and model == self.model
                    and job["attempts"] >= MAX_EMBED_ATTEMPTS):
                # Retain the terminal job so replay/schedule_stale cannot reset
                # the retry budget for this unchanged row and encoder pin.
                if job["last_error"] != "embed_failed":
                    await conn.execute(
                        _sql(
                            """UPDATE {schema}.memory_index_jobs SET last_error='embed_failed'
                        WHERE tenant_id=$1 AND row_id=$2 AND target_model=$3 AND operation=$4""",
                            s,
                        ),
                        tenant, row_id, model, operation,
                    )
                return False
            try:
                if operation == "delete" or row is None:
                    # A replay may recreate the same id after deletion. An old
                    # tombstone must never delete its successfully rebuilt index.
                    if row is None or row["embed_model"] != model:
                        await asyncio.to_thread(
                            self.index.delete, agent, str(row_id), model, str(tenant)
                        )
                elif model == self.model:
                    vector = await asyncio.to_thread(self.encoder.embed, row["claim"])
                    await asyncio.to_thread(self.index.upsert, dict(row), vector, model)
                    await conn.execute(
                        _sql(
                            """UPDATE {schema}.memory_records SET embed_model=$3,updated_at=now()
                        WHERE tenant_id=$1 AND id=$2""",
                            s,
                        ),
                        tenant,
                        row_id,
                        model,
                    )
                else:
                    # Superseded upsert job. schedule_stale queues the current pin.
                    pass
            except Exception as exc:
                # Keep the committed record; store only the exception class, never text/URLs.
                await conn.execute(
                    _sql(
                        """UPDATE {schema}.memory_index_jobs SET attempts=attempts+1,
                    last_error=$5,run_at=now()+least(3600,power(2,least(attempts,10))::int)*interval '1 second'
                    WHERE tenant_id=$1 AND row_id=$2 AND target_model=$3 AND operation=$4""",
                        s,
                    ),
                    tenant,
                    row_id,
                    model,
                    operation,
                    "embed_failed"
                    if operation == "upsert" and job["attempts"] + 1 >= MAX_EMBED_ATTEMPTS
                    else type(exc).__name__,
                )
                return False
            await conn.execute(
                _sql(
                    """DELETE FROM {schema}.memory_index_jobs
                WHERE tenant_id=$1 AND row_id=$2 AND target_model=$3 AND operation=$4""",
                    s,
                ),
                tenant,
                row_id,
                model,
                operation,
            )
            return True

    async def schedule_stale(self, agent: str, tenant: str) -> int:
        s = self.schema(agent)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                _sql(
                    """INSERT INTO {schema}.memory_index_jobs(row_id,tenant_id,target_model)
                SELECT id,tenant_id,$2 FROM {schema}.memory_records
                WHERE tenant_id=$1 AND embed_model IS DISTINCT FROM $2
                ON CONFLICT DO NOTHING RETURNING row_id""",
                    s,
                ),
                uuid.UUID(tenant),
                self.model,
            )
        return len(rows)

    async def tick(self) -> None:
        for agent in sorted(self.agents):
            s = self.schema(agent)
            async with self.pool.acquire() as conn:
                tenants = await conn.fetch(
                    _sql("SELECT DISTINCT tenant_id FROM {schema}.memory_records", s)
                )
            for tenant in tenants:
                await self.schedule_stale(agent, str(tenant["tenant_id"]))
            async with self.pool.acquire() as conn:
                due = (
                    "SELECT * FROM {schema}.memory_index_jobs WHERE run_at<=now()"
                    " AND (operation='delete' OR attempts<"
                    + str(MAX_EMBED_ATTEMPTS)
                    + " OR last_error IS DISTINCT FROM 'embed_failed')"
                    + " ORDER BY run_at LIMIT 100"
                )
                jobs = await conn.fetch(_sql(due, s))
            for job in jobs:
                await self.process_one(
                    agent,
                    job["tenant_id"],
                    job["row_id"],
                    job["target_model"],
                    job["operation"],
                )

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.warning("Memory index worker unavailable")
            await asyncio.sleep(30)

    async def forget(self, agent: str, tenant: str, row_id: str | None = None) -> int:
        s = self.schema(agent)
        async with self.pool.acquire() as conn, conn.transaction():
            candidates = await conn.fetch(
                _sql(
                    """SELECT id FROM {schema}.memory_records WHERE tenant_id=$1 AND tier='client'
                AND ($2::uuid IS NULL OR id=$2) ORDER BY id""",
                    s,
                ),
                uuid.UUID(tenant),
                uuid.UUID(row_id) if row_id else None,
            )
            removed = 0
            for candidate in candidates:
                await self.lock(conn, s, candidate["id"])
                row = await conn.fetchrow(
                    _sql(
                        """DELETE FROM {schema}.memory_records
                    WHERE tenant_id=$1 AND id=$2 AND tier='client' RETURNING id,embed_model""",
                        s,
                    ),
                    uuid.UUID(tenant),
                    candidate["id"],
                )
                if row is None:
                    continue
                removed += 1
                pending = await conn.fetch(
                    _sql(
                        """DELETE FROM {schema}.memory_index_jobs
                    WHERE tenant_id=$1 AND row_id=$2 RETURNING target_model""",
                        s,
                    ),
                    uuid.UUID(tenant),
                    row["id"],
                )
                for model in {
                    row["embed_model"],
                    self.model,
                    *(j["target_model"] for j in pending),
                } - {""}:
                    await self.enqueue(
                        conn, s, row["id"], uuid.UUID(tenant), model, "delete"
                    )
        return removed
