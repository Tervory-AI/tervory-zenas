"""Explicit founder opt-in per client and agent. No automatic publication."""

import json
import re
import uuid
from hermes.deidentify import deidentify, identity_inventory
from hermes.record_schema import PLATFORM_TENANT


_SCHEMA = re.compile(r"agent_[a-z][a-z0-9_]{0,40}")


def _sql(statement: str, schema: str) -> str:
    """Schema names come only from Hermes's agent allowlist; checked again here."""
    if not _SCHEMA.fullmatch(schema):
        raise ValueError("invalid schema name")
    return statement.format(schema=schema)


class Publisher:
    def __init__(self, records):
        self.records = records

    async def set_policy(
        self, agent: str, tenant: str, actor: str, enabled: bool, identifiers: dict
    ) -> dict:
        s = self.records.schema(agent)
        invalid = False
        if enabled:
            try:
                identity_inventory(identifiers)
            except ValueError:
                enabled, invalid = False, True
        async with self.records.pool.acquire() as conn:
            await conn.execute(
                _sql("""INSERT INTO {schema}.memory_publish_policy(tenant_id,enabled,identifiers,updated_by)
                VALUES($1,$2,$3::jsonb,$4) ON CONFLICT(tenant_id) DO UPDATE
                SET enabled=EXCLUDED.enabled,identifiers=EXCLUDED.identifiers,
                updated_by=EXCLUDED.updated_by,updated_at=now()""", s),
                uuid.UUID(tenant),
                enabled,
                json.dumps(identifiers),
                actor,
            )
        if invalid:
            # Commit OFF even if this replaces an earlier enabled policy.
            raise ValueError("Reviewed identity inventory is required")
        return dict(agent=agent, tenant_id=tenant, enabled=enabled)

    async def publish(
        self, agent: str, tenant: str, source_id: str, actor: str
    ) -> dict:
        s = self.records.schema(agent)
        tenant_id, source = uuid.UUID(tenant), uuid.UUID(source_id)
        target = uuid.uuid5(source, "platform-memory:" + tenant)
        result, published = "disabled", None
        async with self.records.pool.acquire() as conn, conn.transaction():
            # Lock policy against concurrent revoke; no row means off.
            policy = await conn.fetchrow(
                _sql("SELECT * FROM {schema}.memory_publish_policy WHERE tenant_id=$1 FOR UPDATE", s),
                tenant_id,
            )
            if policy and policy["enabled"]:
                try:
                    identity_inventory(json.loads(policy["identifiers"]))
                except ValueError:
                    # Recheck persisted policies too, including legacy empty
                    # inventories. Refusal, revoke and audit commit together.
                    await conn.execute(
                        _sql("""UPDATE {schema}.memory_publish_policy
                        SET enabled=false,updated_by=$2,updated_at=now() WHERE tenant_id=$1""", s),
                        tenant_id, actor,
                    )
                    result, policy = "refused", None
            if policy and policy["enabled"]:
                row = await conn.fetchrow(
                    _sql("""SELECT * FROM {schema}.memory_records
                    WHERE tenant_id=$1 AND id=$2 AND tier='client' FOR SHARE""", s),
                    tenant_id,
                    source,
                )
                withdrawn = await conn.fetchval(
                    _sql("SELECT EXISTS(SELECT 1 FROM {schema}.memory_publish_log WHERE tenant_id=$1 AND source_row=$2 AND result='withdrawn')", s),
                    tenant_id, source)
                if withdrawn:
                    result = "refused"
                elif not row:
                    result = "missing"
                else:
                    meta = row.get("meta") or {}
                    meta = json.loads(meta) if isinstance(meta, str) else meta
                    public_addresses = meta.get("public_addresses") or []
                    public_addresses = [a for a in public_addresses if isinstance(a, str)][:16] if isinstance(public_addresses, list) else []
                    try:
                        claim, evidence = deidentify(
                            row["claim"],
                            json.loads(row["evidence"]),
                            tenant,
                            json.loads(policy["identifiers"]),
                            public_addresses=public_addresses,
                        )
                    except ValueError:
                        result = "refused"
                    else:
                        await self.records.lock(conn, s, target)
                        created = await conn.fetchval(
                            _sql("""INSERT INTO {schema}.memory_records
                            (id,tenant_id,claim,evidence,confidence,last_verified,tier,kind,meta)
                            VALUES($1,$2,$3,$4::jsonb,$5,$6,'platform',$7,$8::jsonb) ON CONFLICT(id) DO NOTHING RETURNING id""", s),
                            target,
                            uuid.UUID(PLATFORM_TENANT),
                            claim,
                            json.dumps(evidence),
                            row["confidence"],
                            row["last_verified"], row.get("kind", "fact"),
                            json.dumps({"_index_enabled": False} if meta.get("_index_enabled") is False else {}),
                        )
                        result = "published" if created else "already_published"
                        published = target
                        if meta.get("_index_enabled") is not False:
                            await self.records.enqueue(conn, s, target, uuid.UUID(PLATFORM_TENANT), self.records.model)
            await conn.execute(
                _sql("""INSERT INTO {schema}.memory_publish_log(tenant_id,actor,source_row,result,published_row)
                VALUES($1,$2,$3,$4,$5)""", s),
                tenant_id,
                actor,
                source,
                result,
                published,
            )
        # Indexing is durable but deliberately not coupled to publication success.
        return dict(result=result, published_row=str(published) if published else None)


    async def withdraw(self, agent: str, tenant: str, published_id: str, actor: str) -> dict:
        """One source-bound platform record, only if the publication audit proves origin."""
        s = self.records.schema(agent)
        tenant_id, target = uuid.UUID(tenant), uuid.UUID(published_id)
        platform = uuid.UUID(PLATFORM_TENANT)
        async with self.records.pool.acquire() as conn, conn.transaction():
            await self.records.lock(conn, s, target)
            publication = await conn.fetchrow(_sql(
                "SELECT source_row FROM {schema}.memory_publish_log WHERE tenant_id=$1 AND published_row=$2 AND result IN ('published','already_published') ORDER BY happened_at DESC LIMIT 1", s), tenant_id, target)
            if not publication or target != uuid.uuid5(publication["source_row"], "platform-memory:" + tenant):
                raise ValueError("No matching publication")
            row = await conn.fetchrow(_sql(
                "DELETE FROM {schema}.memory_records WHERE id=$1 AND tenant_id=$2 AND tier='platform' RETURNING id,embed_model", s), target, platform)
            if row is None:
                prior = await conn.fetchval(_sql(
                    "SELECT EXISTS(SELECT 1 FROM {schema}.memory_publish_log WHERE tenant_id=$1 AND published_row=$2 AND result='withdrawn')", s), tenant_id, target)
                if prior:
                    return {"result": "withdrawn", "published_row": str(target)}
                raise ValueError("No matching platform record")
            pending = await conn.fetch(_sql(
                "DELETE FROM {schema}.memory_index_jobs WHERE tenant_id=$1 AND row_id=$2 RETURNING target_model", s), platform, target)
            for model in {row["embed_model"], *(j["target_model"] for j in pending)} - {""}:
                await self.records.enqueue(conn, s, target, platform, model, "delete")
            await conn.execute(_sql(
                "INSERT INTO {schema}.memory_publish_log(tenant_id,actor,source_row,result,published_row) VALUES($1,$2,$3,'withdrawn',$4)", s), tenant_id, actor, publication["source_row"], target)
        return {"result": "withdrawn", "published_row": str(target)}
