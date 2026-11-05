"""Founder-only, tenant-scoped evidence and exports. No prompts or secrets."""
from __future__ import annotations

from tenant_db import tenant_transaction

from tenant_db import tenant_scope

import csv
import time
from collections import OrderedDict
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import json
from typing import Any
import uuid

from fastapi import APIRouter, Depends, Header, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response

from admin import require_admin
from gold import runtime

@asynccontextmanager
async def baseline_lifespan(app):
    from savings_indexes import ensure_index
    startup_pool = getattr(runtime(), "admin_pool", runtime().pool)()
    await ensure_index(startup_pool)
    from savings_defaults import initialize
    async with startup_pool.acquire() as conn:
        await initialize(conn)
    async def work():
        while True:
            await asyncio.sleep(1)
            try:
                await baseline_batch()
            except Exception:
                # The queue remains pending/running and retries; no model calls.
                import logging
                logging.getLogger(__name__).exception('Baseline repricing will retry')
                with suppress(Exception):
                    await runtime().pool().execute("UPDATE tank.baseline_changes SET error='Repricing unavailable; retrying.' WHERE id=(SELECT id FROM tank.baseline_changes WHERE status IN ('pending','running') ORDER BY created_at,id LIMIT 1)")
                await asyncio.sleep(5)
    with tenant_scope(None, administrative=True):
        worker = asyncio.create_task(work())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


router = APIRouter(prefix='/v1/proof', dependencies=[Depends(require_admin)], lifespan=baseline_lifespan)


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0))


# Identical predicates drive SQL totals and the paged receipts.
LIVE = "agent NOT IN ('bench','shadow') AND task_type NOT LIKE '%#shadow'"
SCOPE = "($1::uuid IS NULL OR tenant_id=$1) AND region=$2 AND ($3::text IS NULL OR task_type=$3)"
WINDOW = "($4::timestamptz IS NULL OR ts >= $4) AND ts < $5"
TOTALS = """count(*)::int AS calls,
    count(standard_cost_usd)::int AS priced_calls,
    coalesce(sum(cost_usd) FILTER (WHERE research_cost_status IS DISTINCT FROM 'unknown' AND cost_status='set'),0) AS actual_usd,
    coalesce(sum(cost_usd) FILTER (WHERE standard_cost_usd IS NOT NULL),0) AS compared_actual_usd,
    count(*) FILTER (WHERE (research_cost_status='unknown' OR cost_status='not_set'))::int AS unknown_cost_calls,
    coalesce(sum(standard_cost_usd),0) AS baseline_usd,
    coalesce(sum(standard_cost_usd-cost_usd),0) AS saved_usd,
    count(*) FILTER (WHERE baseline_status='backfilled' AND standard_cost_usd IS NOT NULL)::int AS backfilled_calls,
    count(*) FILTER (WHERE standard_cost_usd IS NULL)::int AS unpriced_calls,
    count(*) FILTER (WHERE rung=3 AND NOT cache_hit AND cost_usd=0 AND outcome='delivered' AND research_cost_status IS DISTINCT FROM 'unknown' AND cost_status='set')::int AS code_calls,
    bool_or(NOT EXISTS(SELECT 1 FROM tank.shadow sh JOIN tank.ledger sl ON sl.id=sh.ledger_id AND sl.tenant_id=sh.tenant_id
      WHERE sh.tenant_id=receipts.tenant_id AND sh.task_type=receipts.task_type AND sl.region=receipts.region)) AS quality_pending,
    array_remove(array_agg(DISTINCT standard_model),NULL) AS standards,
    array_remove(array_agg(DISTINCT top_model),NULL) AS tops,
    coalesce(sum(top_cost_usd-cost_usd) FILTER (WHERE top_cost_usd IS NOT NULL),0) AS top_saved_usd,
    coalesce(sum(top_cost_usd),0) AS top_baseline_usd,
    coalesce(sum(cost_usd) FILTER (WHERE top_cost_usd IS NOT NULL),0) AS top_actual_usd"""
PAGE_SIZE = 100


async def product_summary(conn: Any, tenant: uuid.UUID, product: str, region: str,
                          start: datetime, end: datetime) -> dict:
    """Case-study projection of CORE proof, without founder/all-tenant metadata."""
    agents = {"market_lab": ["market_lab", "market-lab"], "autumbra": ["ghost", "autumbra"]}.get(product, [product])
    row = dict(await conn.fetchrow(f"""SELECT {TOTALS},
        count(DISTINCT coalesce(task_id,id)) AS task_count,
        count(*) FILTER (WHERE cache_hit) AS cache_calls,
        count(*) FILTER (WHERE NOT cache_hit AND rung=3) AS executed_code,
        count(*) FILTER (WHERE NOT cache_hit AND rung<>3 AND vendor IS NULL) AS model_calls
        FROM {receipt_source('$1','$2','$4')} receipts
        WHERE tenant_id=$1 AND region=$2 AND ts >= $3 AND ts < $4 AND agent=ANY($5) AND {LIVE}""",
        tenant, region, start, end, agents))
    # Use the same conservative linked population as the existing client proof.
    saved = await conn.fetchval(savings_cte() + """SELECT coalesce(sum(standard_cost_usd-cost_usd),0)
        FROM eligible WHERE tenant_id=$1 AND ts >= $4 AND agent=ANY($5)""", tenant, region, end, start, agents)
    # Only this tenant's observations count. Global route_evidence would expose
    # other clients' work. Keep candidate model names out of the API projection.
    progress = await conn.fetch("""WITH samples AS (
        SELECT s.task_type,s.candidate_model,count(*) AS samples,avg(s.agreement) AS agreement
        FROM tank.shadow s JOIN tank.ledger l ON l.tenant_id=$1 AND l.id=s.ledger_id
        WHERE s.tenant_id=$1 AND l.region=$2 AND l.agent=ANY($5) AND l.ts >= $3 AND l.ts < $4
            AND s.created_at < $4 AND s.agreement IS NOT NULL
        GROUP BY s.task_type,s.candidate_model
    ) SELECT DISTINCT ON(s.task_type) s.task_type,s.samples,s.agreement,
        coalesce(p.min_shadow_samples,200) AS target,coalesce(p.eval_threshold,0.99) AS threshold
        FROM samples s LEFT JOIN LATERAL (SELECT min_shadow_samples,eval_threshold FROM tank.policy
            WHERE task_type=s.task_type AND (tenant_id=$1 OR tenant_id IS NULL)
            ORDER BY tenant_id NULLS LAST LIMIT 1) p ON true
        ORDER BY s.task_type,s.samples DESC,s.agreement DESC,s.candidate_model""", tenant, region, start, end, agents)
    return {"spent": row["actual_usd"], "saved": saved, "tasks": row["task_count"],
            "cost_per_task": row["actual_usd"] / row["task_count"] if row["task_count"] else 0,
            "cache_hits": row["cache_calls"], "code": row["executed_code"], "model": row["model_calls"],
            "unknown_cost_calls": row["unknown_cost_calls"], "calls": row["calls"],
            "graduation": [{"id": r["task_type"], "value": min(100, 100 * r["samples"] / max(1, r["target"])),
                            "samples": r["samples"], "target": r["target"],
                            "agreement": r["agreement"] or 0, "threshold": r["threshold"]} for r in progress]}


def receipt_source(tenant: str | None, region: str, end: str, clients_only: bool = False) -> str:
    """SQL placeholders only; reconstruct the comparison at a report's cutoff."""
    scope = f"({tenant}::uuid IS NULL OR raw.tenant_id={tenant}) AND " if tenant else ''
    if clients_only:
        scope += "raw.tenant_id<>'00000000-0000-0000-0000-000000000001'::uuid AND "
    return f"""(SELECT r.* FROM tank.ledger raw LEFT JOIN LATERAL (SELECT comparison FROM tank.comparison_history h
          WHERE h.ledger_id=raw.id AND h.tenant_id=raw.tenant_id AND h.region=raw.region
            AND h.valid_from <= {end} AND h.valid_until > {end} ORDER BY h.valid_until LIMIT 1) h ON true
        CROSS JOIN LATERAL jsonb_populate_record(NULL::tank.ledger,to_jsonb(raw)||coalesce(h.comparison,'{{}}'::jsonb)||CASE WHEN raw.cost_status='not_set' THEN jsonb_build_object('cost_usd',NULL,'standard_cost_usd',NULL,'top_cost_usd',NULL,'baseline_cost_usd',NULL) ELSE '{{}}'::jsonb END) r
        WHERE {scope}raw.region={region} AND raw.ts < {end})"""


async def snapshot(tenant: uuid.UUID | None, task: str | None = None,
                   start: datetime | None = None, end: datetime | None = None, page: int = 0, clients_only: bool = False) -> dict:
    region = runtime().box_region()
    end = end or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc)
    if start and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if start:
        start = start.astimezone(timezone.utc)
    if start and start >= end:
        from fastapi import HTTPException
        raise HTTPException(422, 'Start must precede end.')
    args = (tenant, region, task, start, end)
    where = f"{SCOPE} AND {WINDOW} AND {LIVE}"
    async with tenant_transaction(runtime().pool(), isolation='repeatable_read', readonly=True) as conn:
        tasks = [dict(r) for r in await conn.fetch(f"SELECT task_type,{TOTALS} FROM {receipt_source('$1','$2','$5',clients_only)} receipts WHERE {where} GROUP BY task_type ORDER BY task_type", *args)]
        all_total = dict(await conn.fetchrow(f"SELECT {TOTALS} FROM {receipt_source(None,'$1','$2',clients_only)} receipts WHERE region=$1 AND ts < $2 AND {LIVE}", region, end))
        tenants = [str(r['tenant_id']) for r in await conn.fetch("SELECT DISTINCT tenant_id FROM tank.ledger WHERE region=$1 UNION SELECT '00000000-0000-0000-0000-000000000001'::uuid ORDER BY tenant_id", region)]
        ledger = [dict(r) for r in await conn.fetch(f"""SELECT l.id,l.tenant_id,l.ts,l.task_type,l.agent,l.model,l.rung,l.tokens_in,l.tokens_out,
            CASE WHEN (l.research_cost_status='unknown' OR l.cost_status='not_set') THEN NULL ELSE l.cost_usd END AS cost_usd,
            CASE WHEN l.cost_status='not_set' THEN 'price not set' ELSE l.cost_status END AS cost_status,
            l.vendor,l.units,l.vendor_cost_status,l.vendor_list_cost_usd,l.research_cost_status,l.baseline_model,l.baseline_cost_usd,l.baseline_status,l.baseline_priced_at,l.cache_hit,l.outcome,
            l.standard_cost_usd-l.cost_usd AS saved_usd, l.standard_model,l.standard_cost_usd,l.top_model,l.top_cost_usd,
            l.baseline_cost_usd < l.cost_usd AS baseline_below_actual,
            (SELECT a.family<>b.family FROM tank.proof_prices a,tank.proof_prices b
             WHERE a.model=l.model AND b.model=l.baseline_model) AS baseline_family_mismatch
            FROM {receipt_source('$1','$2','$5',clients_only)} l WHERE {where} ORDER BY ts DESC,id DESC LIMIT $6 OFFSET $7""", *args, PAGE_SIZE+1, page*PAGE_SIZE)]
        shadow_from = """FROM tank.shadow s JOIN tank.ledger l ON l.id=s.ledger_id AND l.tenant_id=s.tenant_id
            LEFT JOIN tank.ledger rc ON rc.id=s.candidate_ledger_id AND rc.routellm_parent_id=l.id
                AND rc.routellm_sample_tenant=s.tenant_id
            WHERE ($1::uuid IS NULL OR s.tenant_id=$1) AND l.region=$2 AND ($3::text IS NULL OR s.task_type=$3)
            AND ($4::timestamptz IS NULL OR l.ts >= $4) AND l.ts < $5"""
        if clients_only:
            shadow_from += " AND s.tenant_id<>'00000000-0000-0000-0000-000000000001'::uuid"
        client_scope = " AND tenant_id<>'00000000-0000-0000-0000-000000000001'::uuid" if clients_only else ''
        # Conditional router comparisons use actual ledger costs on both sides,
        # excluding judge overhead and unpriced/failed candidate calls. They are
        # measured sample savings, never extrapolated to unsampled traffic.
        primary_cost = "CASE WHEN s.routellm_config IS NULL THEN s.primary_cost_usd ELSE l.cost_usd END"
        candidate_cost = "CASE WHEN s.routellm_config IS NOT NULL THEN rc.routellm_model_cost_usd WHEN s.candidate_model LIKE 'cache:embedding:%' THEN 0 ELSE s.candidate_cost_usd END"
        evaluation_cost = "CASE WHEN s.routellm_config IS NULL AND s.candidate_model LIKE 'cache:embedding:%' THEN s.candidate_cost_usd ELSE 0 END"
        comparisons = [dict(r) for r in await conn.fetch(f"""SELECT s.task_type,s.primary_model,s.candidate_model,
            count(*)::int AS samples,avg(s.agreement) AS agreement,sum({primary_cost}) AS primary_cost_usd,
            sum({candidate_cost}) AS could_have_cost_usd,
            sum({evaluation_cost}) AS evaluation_cost_usd,
            sum({primary_cost}-{candidate_cost}) AS saved_usd """+shadow_from+"""
            AND (s.routellm_config IS NULL OR (l.routellm_primary_cost_known AND rc.routellm_model_cost_usd IS NOT NULL
                 AND rc.outcome='delivered' AND rc.model=l.routellm_weak_model AND rc.ts<$5))
            GROUP BY s.task_type,s.primary_model,s.candidate_model""", *args)]
        semantic = [dict(r) for r in await conn.fetch("""SELECT o.task_type,
            count(*)::int AS misses_checked,
            count(*) FILTER (WHERE o.status='hit')::int AS would_have_hits,
            count(*) FILTER (WHERE o.status='hit' AND o.agreement IS NOT NULL)::int AS scored_hits,
            avg(o.agreement) AS agreement,
            coalesce(sum(o.primary_cost_usd) FILTER (WHERE o.status='hit'),0) AS could_have_saved_usd
            FROM tank.semantic_observations o JOIN tank.ledger l ON l.id=o.ledger_id AND l.tenant_id=o.tenant_id
            WHERE ($1::uuid IS NULL OR o.tenant_id=$1) AND o.region=$2 AND l.region=$2
              AND ($3::text IS NULL OR o.task_type=$3) AND ($4::timestamptz IS NULL OR l.ts >= $4) AND l.ts < $5
            """ + (" AND o.tenant_id<>'00000000-0000-0000-0000-000000000001'::uuid" if clients_only else "") +
            " GROUP BY o.task_type", *args)]
        shadows = [dict(r) for r in await conn.fetch(f"""SELECT s.id,s.tenant_id,s.ledger_id,s.task_type,s.primary_model,s.candidate_model,
            {primary_cost} AS primary_cost_usd,CASE WHEN s.routellm_config IS NULL THEN s.candidate_cost_usd ELSE rc.routellm_model_cost_usd END AS candidate_cost_usd,s.agreement,s.method,s.created_at """+shadow_from+
            " ORDER BY l.ts DESC,s.id DESC LIMIT $6 OFFSET $7", *args, PAGE_SIZE+1, page*PAGE_SIZE)]
        runs = [dict(r) for r in await conn.fetch(f"""SELECT id,tenant_id,task_type,model,comparison_model,suite_hash,expected_n,
            score,cost_per_call,status,error,started_at,finished_at FROM tank.bench_runs
            WHERE {SCOPE}{client_scope} AND ($4::timestamptz IS NULL OR started_at >= $4) AND started_at < $5
            ORDER BY started_at DESC,id DESC LIMIT $6 OFFSET $7""", *args, PAGE_SIZE+1, page*PAGE_SIZE)]
        cases = [dict(r) for r in await conn.fetch(f"""SELECT c.id,c.tenant_id,c.run_id,r.task_type,r.model,c.case_ref,c.case_kind,
            c.ledger_id,c.score,c.reserved_usd,c.charged_usd,c.status,c.created_at,
            l.cost_usd,l.tokens_in,l.tokens_out,l.outcome
            FROM tank.bench_calls c JOIN tank.bench_runs r ON r.id=c.run_id AND r.tenant_id=c.tenant_id AND r.region=c.region
            LEFT JOIN tank.ledger l ON l.id=c.ledger_id AND l.tenant_id=c.tenant_id AND l.region=c.region
            WHERE ($1::uuid IS NULL OR c.tenant_id=$1) AND c.region=$2 {client_scope.replace('tenant_id','c.tenant_id')} AND ($3::text IS NULL OR r.task_type=$3)
            AND ($4::timestamptz IS NULL OR c.created_at >= $4) AND c.created_at < $5
            ORDER BY c.created_at DESC,c.id DESC LIMIT $6 OFFSET $7""", *args, PAGE_SIZE+1, page*PAGE_SIZE)]
        policies = [dict(r) for r in await conn.fetch("""SELECT p.tenant_id,p.task_type,p.model,p.candidate_model,p.auto_promote,
            coalesce(s.override_model,tank.standard_model_for(a.family)) AS baseline_model,
            tank.standard_model_for(a.family) AS standard_model
            FROM tank.policy p LEFT JOIN tank.baseline_settings s ON s.task_type=p.task_type
            LEFT JOIN tank.proof_prices a ON a.model=p.model
            WHERE ($1::uuid IS NULL OR p.tenant_id=$1 OR p.tenant_id IS NULL) AND ($2::text IS NULL OR p.task_type=$2)
            ORDER BY p.task_type,p.tenant_id NULLS LAST""", tenant, task)]
        promotions = [dict(r) for r in await conn.fetch("""SELECT id,task_type,action,from_model,to_model,created_at
            FROM tank.policy_log WHERE action IN ('promote','rollback') AND ($1::text IS NULL OR task_type=$1)
            AND ($2::timestamptz IS NULL OR created_at >= $2) AND created_at < $3
            ORDER BY created_at DESC,id DESC LIMIT $4 OFFSET $5""", task, start, end, PAGE_SIZE+1, page*PAGE_SIZE)]
        state = [dict(r) for r in await conn.fetch("""SELECT task_type,bench_at,bench_status,bench_error FROM tank.search_state
            WHERE ($1::text IS NULL OR task_type=$1) ORDER BY task_type""", task)]
    all_total['quality_note']='same work assumed; quality comparison pending' if all_total.get('quality_pending') else None
    by_task = {r['task_type']: r for r in tasks}
    for name in sorted({r['task_type'] for group in (comparisons,runs,policies) for r in group} - by_task.keys()):
        row = dict(task_type=name, calls=0, priced_calls=0,actual_usd=0,compared_actual_usd=0,unknown_cost_calls=0,baseline_usd=0,saved_usd=0,backfilled_calls=0,unpriced_calls=0,code_calls=0)
        tasks.append(row)
    for row in tasks:
        row['code_share'] = row['code_calls']/row['calls'] if row['calls'] else None
        row['shadow_pairs'] = [p for p in comparisons if p['task_type']==row['task_type']]
        row['semantic_cache'] = next((p for p in semantic if p['task_type']==row['task_type']), None)
        row.update(comparison_numbers(row['compared_actual_usd'],row['baseline_usd']))
        row['quality_note']='same work assumed; quality comparison pending' if row.get('quality_pending',not row['shadow_pairs']) else None
    sections = dict(ledger=ledger,shadows=shadows,runs=runs,cases=cases,promotions=promotions)
    more = {name: len(rows)>PAGE_SIZE for name,rows in sections.items()}
    page_rows = ledger[:PAGE_SIZE]
    return jsonable_encoder({'headline': headline_text(all_total if tenant is None and task is None and start is None else {key:sum(money(t.get(key)) for t in tasks) for key in ['compared_actual_usd','baseline_usd','top_actual_usd','top_baseline_usd']} , sorted({n for t in tasks for n in (t.get('standards') or [])}), sorted({n for t in tasks for n in (t.get('tops') or [])})), 'tenant_id': str(tenant) if tenant else ('clients' if clients_only else 'all'), 'region': region,
        'tenants': tenants, 'all_tenant_total': {**all_total, **comparison_numbers(all_total.get('compared_actual_usd'),all_total.get('baseline_usd'))}, 'tasks': tasks,
        **{name: rows[:PAGE_SIZE] for name,rows in sections.items()}, 'policies': policies, 'state': state,
        'window': {'start': start, 'end': end}, 'page': page, 'page_size': PAGE_SIZE, 'has_more': more,
        'receipt_page_total': {'calls':len(page_rows), 'saved_usd':sum((money(r['saved_usd']) for r in page_rows),Decimal(0))}})


@router.get('')
async def proof(tenant_id: uuid.UUID | None = None, task: str | None = Query(default=None, max_length=200),
                start: datetime | None = None, end: datetime | None = None, page: int = Query(default=0, ge=0, le=100000), clients_only: bool = False) -> dict:
    return await snapshot(tenant_id, task, start, end, page, clients_only)


def csv_export(data: dict) -> str:
    stream = StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['section', 'tenant_id', 'region', 'task_type', 'row_id', 'field', 'value'])
    for section in ('metadata','tasks','ledger','shadows','runs','cases','promotions','policies','state'):
        for row in ([{k:v for k,v in data.items() if k not in ('tasks','ledger','shadows','runs','cases','promotions','policies','state')}] if section=='metadata' else data[section]):
            for key, value in row.items():
                if isinstance(value, (list,dict)):
                    value = json.dumps(value, sort_keys=True)
                # Spreadsheet formula injection protection for all text cells.
                cells = [section, row.get('tenant_id',data['tenant_id']), data['region'], row.get('task_type',''), row.get('id',''), key, '' if value is None else value]
                writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in cells])
    return stream.getvalue()


def pdf_export(data: dict) -> bytes:
    """Paginated Unicode PDF containing totals and the selected evidence page."""
    import textwrap
    lines = ['Tervory | Tank proof', f"Region: {data['region']}  Tenant: {data['tenant_id']}",
             'Savings = named standard cost - actual. Percent saved = savings / standard cost; cheaper multiple = standard cost / actual.',
             'Headline compares the named standard model; the named top-model comparison is separate.',
             'Totals cover the selected time window. Evidence contains the selected page only.']
    if data.get('description'):
        lines = ['Tervory | Client savings', f"Region: {data['region']}  Client: {data['tenant_id']}",
                 data['description'], 'Free work includes tools and reused answers with verified zero cost.',
                 'No billing data yet: no payment or margin is inferred.']
    for section in ('metadata','tasks','ledger','shadows','runs','cases','promotions','policies','state'):
        lines.append(section.upper())
        for row in ([{k:v for k,v in data.items() if k not in ('tasks','ledger','shadows','runs','cases','promotions','policies','state')}] if section=='metadata' else data[section]):
            lines.extend(textwrap.wrap(json.dumps(row, sort_keys=True, ensure_ascii=False), width=110, break_on_hyphens=False) or [''])
    if data.get('headline'):
        lines[1:1]=textwrap.wrap(data['headline'],110)
    from savings_export import PDF
    pdf=PDF(data.get('client_name') or 'Tervory | Tank proof')
    for line in lines:
        pdf.text(line,8)
    return pdf.finish()


@router.get('/export/{format}')
async def export(format: str, tenant_id: uuid.UUID | None = None, task: str | None = Query(default=None, max_length=200),
                 start: datetime | None = None, end: datetime | None = None, page: int = Query(default=0, ge=0, le=100000), clients_only: bool = False, client_name: str = '') -> Response:
    from fastapi import HTTPException
    if format not in ('csv','pdf'):
        raise HTTPException(404, 'not found')
    data = await snapshot(tenant_id, task, start, end, page, clients_only)
    content = csv_export(data) if format=='csv' else pdf_export(dict(data,client_name=client_name))
    return Response(content, media_type='text/csv' if format=='csv' else 'application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="tank-proof.{format}"', 'Cache-Control': 'no-store'})


# CORE-2: a single, conservative population powers graphs and their drill-downs.
# A job link is required so every amount can be explained without exposing call IDs.
# No-token tools/cache can prove zero cost without a model-family comparison.
SAVINGS_BODY = """), linked AS (
    SELECT l.*, t.id AS job_id, t.title AS job_title,
      (l.cost_usd=0 AND l.tokens_in=0 AND l.tokens_out=0
       AND (l.rung=3 OR l.cache_hit) AND l.outcome='delivered') AS free_work,
      a.family AS actual_family, b.family AS baseline_family,
      EXISTS(SELECT 1 FROM tank.shadow sh JOIN tank.ledger sl ON sl.id=sh.ledger_id AND sl.tenant_id=sh.tenant_id
        WHERE sh.task_type=l.task_type AND sh.tenant_id=l.tenant_id AND sl.region=l.region AND sh.created_at<$3) AS quality_compared,
      (l.top_cost_usd >= l.cost_usd AND (l.top_cost_usd=0 OR EXISTS(SELECT 1 FROM tank.proof_prices tp WHERE tp.model=l.top_model AND tp.family=a.family))) AS top_valid
    FROM receipts l
    LEFT JOIN tank.proof_prices a ON a.model=l.model
    LEFT JOIN tank.proof_prices b ON b.model=l.standard_model
    LEFT JOIN LATERAL (SELECT id,title FROM tank.tasks t
      WHERE t.tenant_id=l.tenant_id AND t.status='done' AND t.finished_at < $3
        AND (t.id=l.task_id OR t.ledger_id=l.id)
      ORDER BY (t.id=l.task_id) DESC NULLS LAST,t.id LIMIT 1) t ON true
    WHERE ($1::uuid IS NULL OR l.tenant_id=$1) AND l.region=$2 AND l.ts < $3
      AND l.agent NOT IN ('bench','shadow') AND l.task_type NOT LIKE '%#shadow'
), checked AS (
    SELECT *, CASE
      WHEN cost_status='not_set' THEN 'price not set'
      WHEN research_cost_status='unknown' THEN 'Unknown actual cost'
      WHEN vendor IS NOT NULL OR model LIKE 'vendor:%' THEN 'Fee without a comparable baseline'
      WHEN standard_cost_usd IS NULL OR baseline_status NOT IN ('measured','backfilled','no_tokens','re-priced')
        THEN 'Unknown baseline'
      WHEN baseline_priced_at > $3 THEN 'Comparison checked after this snapshot'
      WHEN cost_usd < 0 OR standard_cost_usd < cost_usd THEN 'Baseline below actual cost'
      WHEN actual_family IS NOT NULL AND baseline_family IS NOT NULL AND actual_family<>baseline_family
        THEN 'Different model family'
      WHEN NOT (free_work AND standard_cost_usd=0) AND (actual_family IS NULL OR baseline_family IS NULL)
        THEN 'Unknown model family'
      WHEN outcome <> 'delivered' THEN 'Work not delivered'
      WHEN job_id IS NULL THEN 'No linked job'
      ELSE NULL END AS excluded_reason,
      CASE agent WHEN 'base' THEN 'Base' WHEN 'tiger' THEN 'Tiger'
        WHEN 'market_lab' THEN 'Market Lab' WHEN 'market-lab' THEN 'Market Lab'
        WHEN 'zenas' THEN 'Zenas' WHEN 'rig' THEN 'Rig' WHEN 'tisan' THEN 'Tisan'
        WHEN 'radix' THEN 'Radix' ELSE 'Other work' END AS member
    FROM linked
), eligible AS (SELECT * FROM checked WHERE excluded_reason IS NULL) """
def savings_cte(clients_only: bool = False) -> str:
    return "WITH receipts AS (SELECT * FROM "+receipt_source('$1','$2','$3',clients_only)+" r"+SAVINGS_BODY


SAVINGS_TOTALS = """count(*)::int AS work_count, count(DISTINCT job_id)::int AS jobs_count,
    coalesce(sum(cost_usd),0) AS spent, coalesce(sum(standard_cost_usd),0) AS would_have_cost,
    coalesce(sum(standard_cost_usd-cost_usd),0) AS saved,
    count(*) FILTER (WHERE free_work)::int AS free_count,
    array_remove(array_agg(DISTINCT standard_model),NULL) AS standards,
    array_remove(array_agg(DISTINCT top_model) FILTER (WHERE top_valid),NULL) AS tops,
    coalesce(sum(top_cost_usd) FILTER (WHERE top_valid),0) AS top_cost,
    coalesce(sum(cost_usd) FILTER (WHERE top_valid),0) AS top_spent,
    coalesce(sum(top_cost_usd-cost_usd) FILTER (WHERE top_valid),0) AS top_saved,
    count(*) FILTER (WHERE top_valid)::int AS top_count,
    bool_or(NOT quality_compared) AS quality_pending"""


def headline_text(row: dict, standards: list | None = None, tops: list | None = None) -> str:
    def phrase(actual, baseline):
        numbers=comparison_numbers(actual,baseline)
        if numbers['percent_cheaper'] is None:
            return 'No measured saving'
        if numbers['percent_cheaper'] < 0:
            return f"{-numbers['percent_cheaper']:.1f}% more costly ({money(actual)/money(baseline):.2f}× the cost)"
        multiple=f"{numbers['times_cheaper']:.2f}× cheaper" if numbers['times_cheaper'] is not None else 'no AI cost; multiple unavailable'
        return f"{numbers['percent_cheaper']:.1f}% cheaper ({multiple})"
    standard=', '.join(standards or row.get('standards') or []) or 'not yet identified'
    top=', '.join(tops or row.get('tops') or []) or 'not yet identified'
    return (f"{phrase(row.get('spent',row.get('compared_actual_usd')),row.get('would_have_cost',row.get('baseline_usd')))} than the standard model ({standard}). "
            f"{phrase(row.get('top_spent',row.get('top_actual_usd')),row.get('top_cost',row.get('top_baseline_usd')))} vs the top model ({top}).")


def comparison_numbers(actual: Any, baseline: Any) -> dict:
    actual, baseline = money(actual), money(baseline)
    return {'percent_cheaper': (baseline-actual)*100/baseline if baseline>0 else None,
            'times_cheaper': baseline/actual if actual>0 and baseline>0 else None}


def decorate_savings(row: dict) -> dict:
    row['standards']=row.get('standards') or []
    row['tops']=row.get('tops') or []
    row.update(comparison_numbers(row['spent'],row['would_have_cost']))
    row['top_comparison']=comparison_numbers(row['top_spent'],row['top_cost'])
    row['quality_note']='same work assumed; quality comparison pending' if row.get('quality_pending') else None
    row['headline']=headline_text(row)
    return row


def savings_encoded(value: Any) -> Any:
    # Decimal strings preserve the receipt precision through JSON and exports.
    return jsonable_encoder(value, custom_encoder={Decimal: lambda value: format(value,'f')})


def savings_end(end: datetime | None) -> datetime:
    from fastapi import HTTPException
    now = datetime.now(timezone.utc)
    end = end or now
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc)
    if end > now:
        raise HTTPException(422, 'End cannot be in the future.')
    return end


async def savings_snapshot(tenant: uuid.UUID | None, end: datetime | None = None,
                           founder: bool = False, clients_only: bool = False) -> dict:
    cte = savings_cte(clients_only)
    end = savings_end(end)
    args = (tenant, runtime().box_region(), end)
    async with tenant_transaction(runtime().pool(), isolation='repeatable_read', readonly=True) as conn:
        total = dict(await conn.fetchrow(cte+f'SELECT {SAVINGS_TOTALS} FROM eligible', *args))
        month = dict(await conn.fetchrow(cte+f"SELECT {SAVINGS_TOTALS} FROM eligible WHERE ts >= $4",
                                        *args, end.replace(day=1,hour=0,minute=0,second=0,microsecond=0)))
        daily = [dict(r) for r in await conn.fetch(cte+f"""SELECT (ts AT TIME ZONE 'UTC')::date::text AS day,
            {SAVINGS_TOTALS}, sum(sum(standard_cost_usd-cost_usd)) OVER (ORDER BY (ts AT TIME ZONE 'UTC')::date) AS cumulative
            FROM eligible GROUP BY (ts AT TIME ZONE 'UTC')::date ORDER BY day""", *args)]
        members = [dict(r) for r in await conn.fetch(cte+f'SELECT member,{SAVINGS_TOTALS} FROM eligible GROUP BY member ORDER BY member', *args)]
        revision = await conn.fetchval('SELECT coalesce(sum(revision),0) FROM tank.savings_revisions WHERE ($1::uuid IS NULL OR tenant_id=$1)', tenant)
        data = dict(as_of=end, total=decorate_savings(total), month=decorate_savings(month), daily=[decorate_savings(d) for d in daily], members=[decorate_savings(m) for m in members], revision=str(revision))
        if founder:
            # No payment amount is stored in billing_events. A price-book rate is
            # not proof of payment, so neither paid nor margin can be inferred.
            cost_totals = """coalesce(sum(cost_usd) FILTER (WHERE research_cost_status IS DISTINCT FROM 'unknown' AND cost_status='set'),0) AS actual,
                count(*) FILTER (WHERE (research_cost_status='unknown' OR cost_status='not_set'))::int AS unknown_costs,
                coalesce(sum(standard_cost_usd),0) AS baseline, array_remove(array_agg(DISTINCT standard_model),NULL) AS standards,
                count(*) FILTER (WHERE standard_cost_usd IS NULL)::int AS unknown_baselines"""
            costs = dict(await conn.fetchrow(cte+f'SELECT {cost_totals} FROM checked', *args))
            cost_days = [dict(r) for r in await conn.fetch(cte+f"SELECT (ts AT TIME ZONE 'UTC')::date::text AS day,{cost_totals} FROM checked GROUP BY (ts AT TIME ZONE 'UTC')::date ORDER BY day", *args)]
            tenants = [str(r['tenant_id']) for r in await conn.fetch("""SELECT DISTINCT tenant_id FROM tank.ledger WHERE region=$1
                UNION SELECT $2::uuid ORDER BY tenant_id""", args[1], uuid.UUID('00000000-0000-0000-0000-000000000001'))]
            excluded = [dict(r) for r in await conn.fetch(cte+'SELECT excluded_reason,count(*)::int AS count FROM checked WHERE excluded_reason IS NOT NULL GROUP BY excluded_reason ORDER BY excluded_reason', *args)]
            data.update(tenant_id=str(tenant) if tenant else ('clients' if clients_only else 'all'), tenants=tenants, region=args[1], excluded=excluded,
                        margin=dict(status='no billing data yet', paid=None, margin=None, margin_percent=None,
                                    total=costs, daily=cost_days))
    return savings_encoded(data)


async def savings_rows(tenant: uuid.UUID | None, end: datetime, page: int = 0,
                       start: datetime | None = None, until: datetime | None = None,
                       member: str | None = None, free_only: bool = False,
                       population: str = 'eligible', clients_only: bool = False, founder: bool = False) -> dict:
    cte = savings_cte(clients_only)
    end = savings_end(end)
    # Population comes only from the founder-authorized endpoint, never SQL input.
    source = {'eligible':'eligible', 'excluded':'checked', 'costs':'checked'}[population]
    condition = ' AND excluded_reason IS NOT NULL' if population=='excluded' else ''
    where = f"FROM {source} WHERE ($4::timestamptz IS NULL OR ts >= $4) AND ($5::timestamptz IS NULL OR ts < $5) AND ($6::text IS NULL OR member=$6) AND (NOT $7::boolean OR free_work){condition}"
    args = (tenant, runtime().box_region(), end, start, until, member, free_only)
    async with tenant_transaction(runtime().pool(), isolation='repeatable_read', readonly=True) as conn:
        total = dict(await conn.fetchrow(cte+f'SELECT {SAVINGS_TOTALS} '+where, *args))
        fields = """job_id,job_title,member,(ts AT TIME ZONE 'UTC')::date::text AS day,
            CASE WHEN (research_cost_status='unknown' OR cost_status='not_set') THEN NULL ELSE cost_usd END AS spent,
            standard_cost_usd AS would_have_cost,standard_cost_usd-cost_usd AS saved,free_work,
            standard_model AS standard_name,top_model AS top_name,
            CASE WHEN top_valid THEN top_cost_usd ELSE NULL END AS top_cost,
            CASE WHEN top_valid THEN top_cost_usd-cost_usd ELSE NULL END AS top_saved,
            baseline_model AS selected_name,baseline_cost_usd AS selected_cost,quality_compared,
            baseline_status AS comparison_status,baseline_priced_at AS compared_at"""
        if founder or population != 'eligible':
            fields += ',id,tenant_id,model,baseline_model,baseline_status,excluded_reason'
        rows = [dict(r) for r in await conn.fetch(cte+'SELECT '+fields+' '+where+
                ' ORDER BY ts DESC,id DESC LIMIT $8 OFFSET $9', *args, PAGE_SIZE+1, page*PAGE_SIZE)]
    return savings_encoded(dict(rows=rows[:PAGE_SIZE], total=decorate_savings(total), page=page, has_more=len(rows)>PAGE_SIZE, as_of=end))


@router.get('/savings')
async def founder_savings(tenant_id: uuid.UUID | None = None, end: datetime | None = None, clients_only: bool = False) -> dict:
    return await savings_snapshot(tenant_id, end, founder=True, clients_only=clients_only)


@router.get('/savings/rows')
async def founder_savings_rows(end: datetime, tenant_id: uuid.UUID | None = None,
        page: int = Query(default=0, ge=0, le=100000), start: datetime | None = None,
        until: datetime | None = None, member: str | None = Query(default=None, max_length=80),
        free_only: bool = False, population: str = Query(default='eligible', pattern='^(eligible|excluded|costs)$'), clients_only: bool = False) -> dict:
    return await savings_rows(tenant_id, end, page, start, until, member, free_only, population, clients_only, founder=True)


@router.get('/savings/export/{format}')
async def savings_export(format: str, end: datetime, tenant_id: uuid.UUID | None = None,
                         page: int = Query(default=0, ge=0, le=100000), clients_only: bool = False, client_name: str = '') -> Response:
    from fastapi import HTTPException
    if format not in ('csv', 'pdf'):
        raise HTTPException(404, 'not found')
    report = await savings_snapshot(tenant_id, end, founder=True, clients_only=clients_only)
    evidence = await savings_rows(tenant_id, end, page, clients_only=clients_only, founder=True)
    # Reuse the bounded evidence exporters: formula-safe CSV and embedded-font PDF.
    data = dict(tenant_id=report['tenant_id'], region=report['region'], as_of=report['as_of'], headline=report['total']['headline'],
                description='Headline: savings against the named standard model, percent cheaper and times cheaper. Second comparison: vs the named top model. Defensible linked work only; evidence is the selected page.',
                page=page, has_more=evidence['has_more'], page_size=PAGE_SIZE,
                tasks=[report['total'],dict(this_month=report['month']),dict(team_members=report['members']),dict(margin=report['margin'])],
                ledger=evidence['rows'], shadows=[], runs=[], cases=[], promotions=[], policies=[], state=report['daily'])
    content = csv_export(data) if format=='csv' else pdf_export(dict(data,client_name=client_name))
    return Response(content, media_type='text/csv' if format=='csv' else 'application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="client-savings.{format}"', 'Cache-Control':'no-store'})


@router.get('/savings/client-copy/{format}')
async def savings_client_copy(format: str, end: datetime, tenant_id: uuid.UUID,
                              page: int = Query(default=0, ge=0, le=100000), client_name: str = '') -> Response:
    from fastapi import HTTPException
    from savings_export import csv_copy, pdf_copy
    if format not in ('csv','pdf'):
        raise HTTPException(404,'not found')
    client_tenant(tenant_id)
    # Required UUID: never an all-client aggregate or founder population.
    report=client_payload(await savings_snapshot(tenant_id,end))
    evidence=client_payload(await savings_rows(tenant_id,end,page))
    content=csv_copy(report,evidence) if format=='csv' else pdf_copy(report,evidence,client_name or 'Client savings')
    return Response(content,media_type='text/csv' if format=='csv' else 'application/pdf',
        headers={'Content-Disposition':f'attachment; filename="your-savings.{format}"','Cache-Control':'no-store'})


# Registered separately: no founder dependency and no optional/all-tenant scope.
from dashboards import workspace_only, client_tenant
client_router = APIRouter(prefix='/v1/client-savings', dependencies=[Depends(workspace_only), Depends(client_tenant)])


# Explicit projection keeps names, receipt metadata and founder-only comparisons
# out of the client JSON, even if a shared report gains fields later.
CLIENT_FIELDS = frozenset("as_of revision total month daily members rows page has_more spent would_have_cost saved work_count jobs_count free_count top_cost top_spent top_saved top_count quality_pending quality_note percent_cheaper times_cheaper top_comparison day member cumulative job_id job_title free_work quality_compared".split())


def client_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {k:client_payload(v) for k,v in value.items() if k in CLIENT_FIELDS}
    if isinstance(value, list):
        return [client_payload(v) for v in value]
    return value


_CLIENT_CACHE: OrderedDict = OrderedDict()
_CLIENT_PENDING: dict = {}


@client_router.get('/revision')
async def client_savings_revision(tenant_id: uuid.UUID) -> dict:
    client_tenant(tenant_id)
    async with runtime().pool().acquire() as conn:
        revision = await conn.fetchval('SELECT revision FROM tank.savings_revisions WHERE tenant_id=$1',tenant_id)
    return {'revision':datetime.now(timezone.utc).date().isoformat()+':'+str(revision or 0)}


@client_router.get('')
async def client_savings(tenant_id: uuid.UUID) -> dict:
    revision=(await client_savings_revision(tenant_id))['revision']
    key=(runtime().box_region(),str(tenant_id),revision)
    cached=_CLIENT_CACHE.get(key)
    if cached and time.monotonic()-cached[0]<300:
        _CLIENT_CACHE.move_to_end(key)
        return cached[1]
    async def build():
        data=client_payload(await savings_snapshot(tenant_id))
        data['revision']=revision
        _CLIENT_CACHE[key]=(time.monotonic(),data)
        _CLIENT_CACHE.move_to_end(key)
        while len(_CLIENT_CACHE)>128:
            _CLIENT_CACHE.popitem(last=False)
        return data
    if key not in _CLIENT_PENDING:
        task=asyncio.create_task(build())
        _CLIENT_PENDING[key]=task
        task.add_done_callback(lambda _: _CLIENT_PENDING.pop(key,None))
    return await asyncio.shield(_CLIENT_PENDING[key])


@client_router.get('/rows')
async def client_savings_rows(tenant_id: uuid.UUID, end: datetime,
        page: int = Query(default=0, ge=0, le=100000), start: datetime | None = None,
        until: datetime | None = None, member: str | None = Query(default=None, max_length=80), free_only: bool = False) -> dict:
    client_tenant(tenant_id)
    return client_payload(await savings_rows(tenant_id, end, page, start, until, member, free_only))


async def check_recent_receipts(conn) -> int:
    """Bounded inbox reconciliation, including late commits after an override."""
    region=runtime().box_region()
    tenant=await conn.fetchval('SELECT tenant_id FROM tank.baseline_receipt_checks WHERE region=$1 ORDER BY tenant_id LIMIT 1',region)
    if tenant is None:
        return 0
    rows=await conn.fetch("""SELECT ledger_id FROM tank.baseline_receipt_checks
        WHERE tenant_id=$1 AND region=$2 ORDER BY ledger_id LIMIT 5000 FOR UPDATE SKIP LOCKED""",tenant,region)
    ids=[r['ledger_id'] for r in rows]
    # No setting locks. A concurrent later override has its own durable request.
    await conn.execute("""UPDATE tank.ledger l SET comparison_revision=s.revision
        FROM tank.baseline_settings s WHERE l.tenant_id=$1 AND l.region=$2
        AND l.id=ANY($3::uuid[]) AND s.task_type=l.task_type AND l.comparison_revision<s.revision""",tenant,region,ids)
    await conn.execute('DELETE FROM tank.baseline_receipt_checks WHERE tenant_id=$1 AND region=$2 AND ledger_id=ANY($3::uuid[])',tenant,region,ids)
    return len(ids)


# Founder choices are durable requests; the worker can resume after interruption.
async def baseline_batch() -> int:
    async with runtime().pool().acquire() as conn, conn.transaction():
        change = await conn.fetchrow("SELECT * FROM tank.baseline_changes WHERE status IN ('pending','running') ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED")
        if not change:
            return await check_recent_receipts(conn)
        setting=await conn.fetchrow('SELECT * FROM tank.baseline_settings WHERE task_type=$1 FOR SHARE',change['task_type'])
        if setting and setting['revision']!=change['revision']:
            await conn.execute("UPDATE tank.baseline_changes SET status='superseded' WHERE id=$1",change['id'])
            return 1
        tenant = await conn.fetchval("""SELECT tenant_id FROM tank.ledger WHERE task_type=$1 AND region=$2
            AND comparison_revision<$3 ORDER BY tenant_id LIMIT 1""", change['task_type'], runtime().box_region(), change['revision'])
        if tenant is None:
            await conn.execute("UPDATE tank.baseline_changes SET status='complete',error=NULL WHERE id=$1",change['id'])
            return 1
        result = await conn.execute("""WITH batch AS (
            SELECT id FROM tank.ledger WHERE tenant_id=$1 AND region=$2 AND task_type=$3 AND comparison_revision<$4
            ORDER BY id LIMIT 5000 FOR UPDATE SKIP LOCKED)
            UPDATE tank.ledger l SET comparison_revision=$4 FROM batch b
            WHERE l.id=b.id AND l.tenant_id=$1 AND l.region=$2""", tenant,runtime().box_region(),change['task_type'],change['revision'])
        count=int(result.split()[-1])
        await conn.execute("UPDATE tank.baseline_changes SET status='running',rows_updated=rows_updated+$2,error=NULL WHERE id=$1",change['id'],count)
        return count or 1


@router.get('/baselines')
async def baseline_options() -> dict:
    async with runtime().pool().acquire() as conn:
        tasks=await conn.fetch("""SELECT p.task_type,p.model,tank.standard_model_for(a.family) AS standard,
            s.override_model,s.revision,s.updated_at,
            (SELECT b.model FROM tank.proof_prices b WHERE b.family=a.family AND b.active ORDER BY b.input_usd+b.output_usd DESC,b.model LIMIT 1) AS top,
            EXISTS(SELECT 1 FROM tank.shadow sh JOIN tank.ledger l ON l.id=sh.ledger_id AND l.tenant_id=sh.tenant_id
              WHERE sh.task_type=p.task_type AND l.region=$1) AS quality_compared,
            ARRAY(SELECT b.model FROM tank.proof_prices b WHERE b.family=a.family AND b.active ORDER BY b.model) AS choices
            FROM tank.policy p LEFT JOIN tank.proof_prices a ON a.model=p.model
            LEFT JOIN tank.baseline_settings s ON s.task_type=p.task_type WHERE p.tenant_id IS NULL ORDER BY p.task_type""", runtime().box_region())
        changes=await conn.fetch('SELECT * FROM tank.baseline_changes ORDER BY created_at DESC LIMIT 100')
        prices=await conn.fetch('SELECT * FROM tank.baseline_price_changes ORDER BY changed_at DESC,id DESC LIMIT 100')
    return savings_encoded(dict(tasks=[dict(r) for r in tasks],changes=[dict(r) for r in changes],prices=[dict(r) for r in prices]))


from pydantic import BaseModel, Field
class BaselineChoice(BaseModel):
    task: str = Field(min_length=1,max_length=200)
    model: str | None = Field(default=None,max_length=200)


async def baseline_actor(x_tervory_admin: str = Header(...), _=Depends(require_admin)) -> str:
    return x_tervory_admin


@router.post('/baselines')
async def change_baseline(choice: BaselineChoice, by: str = Depends(baseline_actor)) -> dict:
    from fastapi import HTTPException
    async with runtime().pool().acquire() as conn, conn.transaction():
        policy=await conn.fetchrow('SELECT * FROM tank.policy WHERE task_type=$1 AND tenant_id IS NULL FOR UPDATE',choice.task)
        if not policy:
            raise HTTPException(404,'Task not found.')
        if choice.model and not await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM tank.proof_prices a,tank.proof_prices b
            WHERE a.model=$1 AND b.model=$2 AND b.active AND a.family=b.family)""",policy['model'],choice.model):
            raise HTTPException(422,'Choose an available model in the same family.')
        old=await conn.fetchrow('SELECT * FROM tank.baseline_settings WHERE task_type=$1',choice.task)
        if old and old['override_model']==choice.model:
            return {'changed':False}
        default=await conn.fetchval('SELECT tank.standard_model_for(family) FROM tank.proof_prices WHERE model=$1',policy['model'])
        revision=(old['revision'] if old else 1)+1
        await conn.execute("""INSERT INTO tank.baseline_settings(task_type,override_model,revision,updated_by)
            VALUES($1,$2,$3,$4) ON CONFLICT(task_type) DO UPDATE SET override_model=$2,revision=$3,updated_by=$4,updated_at=now()""",
            choice.task,choice.model,revision,by)
        await conn.execute("""INSERT INTO tank.baseline_changes(task_type,old_model,new_model,by_user,revision)
            VALUES($1,$2,$3,$4,$5)""",choice.task,(old['override_model'] if old else None) or default,choice.model or default,by,revision)
    return {'changed':True,'revision':revision,'status':'pending'}
