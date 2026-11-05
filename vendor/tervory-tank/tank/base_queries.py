"""Database aggregation keeps the 77k-row reference off the polling response path."""
from __future__ import annotations
from typing import Any
import uuid
from base_metrics import COUNTS, RUNGS, obj
from vendor_meter import PLATFORM

FILTER = "(cardinality($2::text[])=0 OR state=ANY($2)) AND (cardinality($3::text[])=0 OR licence_type=ANY($3)) AND ($4 OR licence_type<>'03')"
EXPRESSIONS = {"all": "true", "attempted": "true", "website": "website", "phone": "phone", "email": "email",
    "email_and_phone": "email AND phone", "email_only": "email AND NOT phone", "phone_only": "phone AND NOT email",
    "neither": "NOT email AND NOT phone", "verified": "website OR phone OR email", "failed": "status IN ('done','failed') AND NOT (website AND phone AND email AND socials)"}


async def record_page(store: Any, tenant: uuid.UUID, states: list[str], kinds: list[str], collectors: bool,
                      metric: str, reason: str | None, offset: int, limit: int, batch: str | None = None) -> dict:
    predicate = EXPRESSIONS[metric]
    async with store.rt.pool().acquire() as conn:
        rows = await conn.fetch(f"""WITH latest AS (
            SELECT DISTINCT ON(coalesce(licence_key,record_id::text)) * FROM tank.base_record_results
            WHERE tenant_id=$1 AND {FILTER} AND ($8::text IS NULL OR batch=$8)
            ORDER BY coalesce(licence_key,record_id::text),checked_at DESC,record_id DESC),
            flags AS (SELECT *,coalesce((fields->'website'->>'verified')::boolean,false) AS website,
            coalesce((fields->'phone'->>'verified')::boolean,false) AS phone,
            coalesce((fields->'email'->>'verified')::boolean,false) AS email,
            coalesce((fields->'socials'->>'verified')::boolean,false) AS socials FROM latest)
            SELECT record_id,licence_key,state,licence_type,name,fields,checked_at,count(*) OVER() AS total FROM flags
            WHERE ({predicate}) AND ($5::text IS NULL OR EXISTS(SELECT 1 FROM jsonb_each(fields) f(k,v)
                WHERE NOT coalesce((v->>'verified')::boolean,false) AND coalesce(v->>'reason','No verified source.')=$5))
            ORDER BY state,licence_type,licence_key,record_id OFFSET $6 LIMIT $7""",
            tenant, states, kinds, collectors, reason, offset, limit, batch)
    return {"total": rows[0]["total"] if rows else 0, "rows": [{**dict(row), "fields": obj(row["fields"])} for row in rows]}


async def ledger_page(store: Any, tenant: uuid.UUID, state: str | None, month: str | None,
                      rung: int | None, kinds: list[str], collectors: bool, offset: int, limit: int, cost_status: str | None = None) -> dict:
    async with store.rt.pool().acquire() as conn:
        rows = await conn.fetch("""SELECT l.id,b.record_id,l.ts,l.rung,l.cache_hit,
            CASE WHEN l.cost_status='set' THEN l.cost_usd ELSE NULL END AS cost_usd,
            l.cost_status,l.vendor,l.units,l.vendor_cost_status,l.vendor_list_cost_usd,l.outcome,
            CASE WHEN l.cost_status='not_set' THEN 'price not set' END AS status,count(*) OVER() AS total
            FROM tank.ledger l LEFT JOIN tank.base_ledger_links b ON b.tenant_id=$1 AND b.ledger_id=l.id
            LEFT JOIN tank.base_record_results r ON r.tenant_id=$1 AND r.record_id=b.record_id
            WHERE l.tenant_id=$1 AND l.agent='base' AND ($2::text IS NULL OR r.state=$2)
            AND ($3::text IS NULL OR to_char(l.ts AT TIME ZONE 'UTC','YYYY-MM')=$3)
            AND ($4::int IS NULL OR CASE WHEN l.cache_hit THEN 4 ELSE l.rung END=$4)
            AND (cardinality($5::text[])=0 OR r.licence_type=ANY($5))
            AND ($6 OR r.licence_type IS NULL OR r.licence_type<>'03') AND ($9::text IS NULL OR l.cost_status=$9)
            ORDER BY l.ts,l.id OFFSET $7 LIMIT $8""", tenant, state, month, rung, kinds, collectors, offset, limit, cost_status)
    return {"total": rows[0]["total"] if rows else 0, "rows": [dict(row) for row in rows]}


async def numbers(store: Any, tenant: uuid.UUID, states: list[str], kinds: list[str], collectors: bool) -> dict:
    args = (tenant, states, kinds, collectors)
    expressions = EXPRESSIONS
    cte = f"""WITH latest AS (
        SELECT DISTINCT ON(coalesce(licence_key,record_id::text)) * FROM tank.base_record_results
        WHERE tenant_id=$1 AND {FILTER} ORDER BY coalesce(licence_key,record_id::text),checked_at DESC,record_id DESC
    ), flags AS (SELECT *,coalesce((fields->'website'->>'verified')::boolean,false) AS website,
        coalesce((fields->'phone'->>'verified')::boolean,false) AS phone,
        coalesce((fields->'email'->>'verified')::boolean,false) AS email,
        coalesce((fields->'socials'->>'verified')::boolean,false) AS socials FROM latest) """
    async with store.rt.pool().acquire() as conn:
        found = await conn.fetch(cte + "SELECT state,licence_type," + ",".join(
            f"count(*) FILTER(WHERE {expressions[name]})::int AS {name}" for name in COUNTS
        ) + " FROM flags GROUP BY state,licence_type", *args)
        groups = {(row["state"], row["licence_type"]): {**dict(row), "licensees": 0, "failed_by_reason": {}} for row in found}
        failures = await conn.fetch(cte + """SELECT state,licence_type,reason,count(*)::int AS n FROM (
            SELECT DISTINCT record_id,state,licence_type,coalesce(v->>'reason','No verified source.') AS reason
            FROM latest CROSS JOIN LATERAL jsonb_each(fields) f(k,v) WHERE status IN ('done','failed') AND NOT coalesce((v->>'verified')::boolean,false)
        ) f GROUP BY state,licence_type,reason""", *args)
        for row in failures:
            groups[row["state"], row["licence_type"]]["failed_by_reason"][row["reason"]] = row["n"]
        reference = await conn.fetch(f"""SELECT state,licence_type,count(*)::int AS n FROM tank.base_reference_entries
            WHERE file_id=(SELECT id FROM tank.base_reference_files WHERE owner_id=$1 ORDER BY published_on DESC LIMIT 1)
            AND {FILTER} GROUP BY state,licence_type""", PLATFORM, states, kinds, collectors)
        for row in reference:
            key = row["state"], row["licence_type"]
            groups.setdefault(key, {"state": key[0], "licence_type": key[1], **{name: 0 for name in COUNTS}, "failed_by_reason": {}})["licensees"] = row["n"]
        costs = await conn.fetch(f"""WITH results AS (SELECT * FROM tank.base_record_results WHERE tenant_id=$1 AND {FILTER}),
            verified AS (SELECT state,to_char(checked_at AT TIME ZONE 'UTC','YYYY-MM') AS month,
                count(DISTINCT coalesce(licence_key,record_id::text))::int AS n FROM results
                WHERE (fields->'website'->>'verified')::boolean OR (fields->'phone'->>'verified')::boolean OR (fields->'email'->>'verified')::boolean
                GROUP BY state,month), missing AS (
                SELECT state,to_char(checked_at AT TIME ZONE 'UTC','YYYY-MM') AS month,count(*)::int AS n
                FROM results r WHERE NOT EXISTS(SELECT 1 FROM tank.base_ledger_links b WHERE b.tenant_id=$1 AND b.record_id=r.record_id)
                GROUP BY state,month), expenses AS (
                SELECT r.state,to_char(l.ts AT TIME ZONE 'UTC','YYYY-MM') AS month,
                    CASE WHEN l.cache_hit THEN 4 ELSE l.rung END AS rung,
                    sum(l.cost_usd) FILTER(WHERE l.cost_status='set') AS known,
                    count(*) FILTER(WHERE l.cost_status='not_set')::int AS unpriced,
                    array_agg(DISTINCT coalesce(l.vendor,'')) AS vendors
                FROM tank.ledger l JOIN tank.base_ledger_links b ON b.tenant_id=$1 AND b.ledger_id=l.id
                JOIN results r ON r.record_id=b.record_id WHERE l.tenant_id=$1 AND l.agent='base'
                GROUP BY r.state,month,CASE WHEN l.cache_hit THEN 4 ELSE l.rung END)
            SELECT e.*,coalesce(v.n,0) AS verified_records,coalesce(m.n,0) AS missing_receipts FROM expenses e
                LEFT JOIN verified v USING(state,month) LEFT JOIN missing m USING(state,month)
            ORDER BY e.state,e.month,e.rung""", *args)
        unattributed = await conn.fetchval("""SELECT count(*) FROM tank.ledger l WHERE l.tenant_id=$1 AND l.agent='base'
            AND NOT EXISTS(SELECT 1 FROM tank.base_ledger_links b WHERE b.tenant_id=$1 AND b.ledger_id=l.id)""", tenant)
        missing = await conn.fetchval(f"""SELECT count(*) FROM tank.base_record_results r WHERE r.tenant_id=$1 AND {FILTER}
            AND NOT EXISTS(SELECT 1 FROM tank.base_ledger_links b WHERE b.tenant_id=$1 AND b.record_id=r.record_id)""", *args)
    amounts = []
    for row in costs:
        known, n = row["known"] or 0, row["verified_records"]
        amounts.append({"state": row["state"], "month": row["month"], "rung": row["rung"], "rung_label": RUNGS.get(row["rung"], "other"),
            "known_cost_usd": str(known), "cost_usd": None if row["unpriced"] else str(known),
            "verified_records": n, "unpriced": row["unpriced"], "vendors": row["vendors"],
            "cost_per_verified_record": str(known / n) if n and not row["missing_receipts"] and not row["unpriced"] else None,
            "status": "price not set" if row["unpriced"] else "receipts unavailable" if row["missing_receipts"] else "no verified records" if not n else "priced"})
    return {"groups": [groups[key] for key in sorted(groups)], "costs": amounts,
            "unattributed_calls": unattributed, "records_without_receipts": missing}


async def reference_page(store: Any, states: list[str], kinds: list[str], collectors: bool, offset: int, limit: int) -> dict:
    files = await store.reference_history()
    async with store.rt.pool().acquire() as conn:
        check = await conn.fetchrow("SELECT checked_at,status,message FROM tank.base_reference_checks WHERE owner_id=$1", PLATFORM)
    health = {"refresh": dict(check) if check else None, "duplicate_count": files[0].get("duplicate_count", 0) if files else 0}
    if not files:
        return {"file": None, "total_all": 0, "selected": 0, "collectors": 0, "groups": [], "rows": [], "changes": [], "history": [], **health}
    file = files[0]
    async with store.rt.pool().acquire() as conn:
        groups = [dict(row) for row in await conn.fetch(f"""SELECT state,licence_type,count(*)::int AS licensees FROM tank.base_reference_entries
            WHERE file_id=$1 AND {FILTER} GROUP BY state,licence_type ORDER BY state,licence_type""", file["id"], states, kinds, collectors)]
        rows = [dict(row) for row in await conn.fetch(f"""SELECT licence_key,licence_number,state,licence_type,name,address FROM tank.base_reference_entries
            WHERE file_id=$1 AND {FILTER} ORDER BY state,licence_type,licence_key OFFSET $5 LIMIT $6""", file["id"], states, kinds, collectors, offset, limit)]
        collector_count = await conn.fetchval("SELECT count(*) FROM tank.base_reference_entries WHERE file_id=$1 AND licence_type='03'", file["id"])
        changes = []
        if len(files) > 1:
            changes = [dict(row) for row in await conn.fetch("""WITH before AS (SELECT * FROM tank.base_reference_entries WHERE file_id=$1),
                after AS (SELECT * FROM tank.base_reference_entries WHERE file_id=$2)
                SELECT coalesce(a.licence_key,b.licence_key) AS licence_key,
                    CASE WHEN b.licence_key IS NULL THEN 'new' WHEN a.licence_key IS NULL THEN 'lapsed' ELSE 'moved' END AS change,
                    b.address AS previous,a.address AS current FROM before b FULL JOIN after a USING(licence_key)
                WHERE b.licence_key IS NULL OR a.licence_key IS NULL OR a.address<>b.address
                ORDER BY licence_key""", files[1]["id"], file["id"])]
    for row in rows:
        row.update(source_url=file["source_url"], checked_at=file["checked_at"], published_on=file["published_on"], address=obj(row["address"]))
    return {"file": file, "total_all": file["row_count"], "selected": sum(row["licensees"] for row in groups), "collectors": collector_count,
            "groups": groups, "rows": rows, "changes": changes, "history": files, **health}
