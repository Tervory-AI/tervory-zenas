"""Private writing lessons, using Tank's existing training-pair writer."""
import json
from uuid import UUID, uuid4

from fastapi import HTTPException


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


async def record(actor, brief: str, result: dict, *, context: dict | None = None, parent_write_id: UUID | None = None) -> dict:
    from main import pool, box_region
    from training import save_pair
    draft = result.get('text')
    if not isinstance(draft, str):
        return result
    write_id = uuid4()
    context = context or {}
    example_ids = [UUID(e["write_id"]) for e in context.get("accepted_examples", [])]
    report = result.get('report', {'delegated_to': 'zenas', 'notice': result.get('notice')})
    output = {'draft': draft, 'checker_results': report, 'feedback': {'status': 'pending', 'changes': ''}}
    async with pool().acquire() as conn, conn.transaction():
        await conn.execute('INSERT INTO tank.trigger_writes(id,tenant_id,user_id,brief,draft,checker_results,format,accepted_write_ids,write_context,parent_write_id) VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9::jsonb,$10)', write_id, actor.tenant_id, actor.user_id, brief, draft, json.dumps(report), context.get('format'), example_ids, json.dumps({**context, 'voice_revision': result.get('voice_revision')}), parent_write_id)
        await save_pair(conn, tenant=actor.tenant_id, agent='trigger', task='trigger.write',
                        value={'brief': brief, 'voice_revision': result.get('voice_revision')}, output=output,
                        approved_by='', source='trigger.write', ref=str(write_id), client_matter=True, public=False)
        await conn.execute("UPDATE tank.training_pairs SET active=false WHERE tenant_id=$1 AND region=$2 AND source='trigger.write' AND source_ref=$3", actor.tenant_id, box_region(), str(write_id))
    return {**result, 'write_id': str(write_id), 'accepted_write_ids': [str(i) for i in example_ids], 'parent_write_id': str(parent_write_id) if parent_write_id else None}


async def feedback(actor, write_id: UUID, status: str, changes: str, operation_id: UUID) -> dict:
    from main import pool, box_region
    async with pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow('SELECT * FROM tank.trigger_writes WHERE id=$1 AND tenant_id=$2 FOR UPDATE', write_id, actor.tenant_id)
        if row is None:
            raise HTTPException(404, 'Writing record unavailable')
        previous = await conn.fetchval('SELECT status FROM tank.trigger_feedback WHERE tenant_id=$1 AND operation_id=$2', actor.tenant_id, operation_id)
        if previous:
            return {'status': previous, 'write_id': str(write_id)}
        await conn.execute('INSERT INTO tank.trigger_feedback(tenant_id,write_id,status,changes,user_id,operation_id) VALUES($1,$2,$3,$4,$5,$6)', actor.tenant_id, write_id, status, changes, actor.user_id, operation_id)
        await conn.execute('UPDATE tank.trigger_writes SET feedback_status=$3 WHERE tenant_id=$1 AND id=$2', actor.tenant_id, write_id, status)
        # Reuse the existing receipt feedback fields consumed by case studies.
        # A foreign/repeated card cannot borrow another tenant's receipt.
        await conn.execute("""UPDATE tank.ledger l
            SET feedback=$4,feedback_note=$5,feedback_at=now(),feedback_by=$6
            WHERE l.tenant_id=$1 AND l.region=$2 AND l.task_type='trigger.write'
            AND EXISTS (SELECT 1 FROM tank.command_threads t
                CROSS JOIN LATERAL jsonb_each(t.state->'cards') c
                CROSS JOIN LATERAL jsonb_array_elements(CASE
                    WHEN c.value->>'tool'='trigger.write.batch' THEN c.value->'result'->'items'
                    ELSE jsonb_build_array(c.value->'result') END) item
                WHERE t.tenant_id=l.tenant_id AND c.value->>'tool' IN ('trigger.write','trigger.revise','trigger.write.batch')
                AND c.value->>'status'='confirmed'
                AND item->>'write_id'=$3
                AND item->>'ledger_id'=l.id::text)""",
            actor.tenant_id, box_region(), str(write_id), status, changes, actor.user_id)

        await conn.execute("""UPDATE tank.training_pairs SET output=jsonb_set(output,'{feedback}',$4::jsonb),
            active=$5,approved_by=$6,polarity=$7,public=false,client_matter=true
            WHERE tenant_id=$1 AND region=$2 AND source='trigger.write' AND source_ref=$3""",
            actor.tenant_id, box_region(), str(write_id), json.dumps({'status': status, 'changes': changes}),
            status == 'accepted', actor.user_id if status == 'accepted' else '',
            'positive' if status == 'accepted' else 'negative')
    return {'status': status, 'write_id': str(write_id), 'text': changes, 'model_calls': 0}


async def guidance(actor, brief: str, format: str | None) -> dict:
    """Bounded newest candidates, then stable overlap rank; never cross tenants."""
    import re
    from main import pool
    async with pool().acquire() as conn:
        rows = await conn.fetch("""SELECT id,draft FROM tank.trigger_writes
            WHERE tenant_id=$1 AND format IS NOT DISTINCT FROM $2 AND feedback_status='accepted'
            ORDER BY created_at DESC,id DESC LIMIT 5""", actor.tenant_id, format)
        notes = await conn.fetch("""SELECT f.changes FROM tank.trigger_feedback f
            JOIN tank.trigger_writes w ON w.tenant_id=f.tenant_id AND w.id=f.write_id
            WHERE f.tenant_id=$1 AND w.format IS NOT DISTINCT FROM $2
                AND f.status='changes_requested' AND btrim(f.changes)<>''
            ORDER BY f.created_at DESC,f.id DESC LIMIT 5""", actor.tenant_id, format)
    words = set(re.findall(r"\w+", brief.casefold()))
    examples = sorted(rows, key=lambda row: -len(words & set(re.findall(r"\w+", row['draft'].casefold()))))
    return {'accepted_examples': [{'write_id': str(r['id']), 'text': r['draft']} for r in examples],
            'change_notes': [r['changes'] for r in notes]}


async def revision(actor, write_id: UUID) -> tuple[dict, UUID]:
    from main import pool
    async with pool().acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM tank.trigger_writes WHERE tenant_id=$1 AND id=$2', actor.tenant_id, write_id)
        note = await conn.fetchval("""SELECT changes FROM tank.trigger_feedback
            WHERE tenant_id=$1 AND write_id=$2 AND status='changes_requested'
            ORDER BY created_at DESC,id DESC LIMIT 1""", actor.tenant_id, write_id)
    if row is None or row['feedback_status'] != 'changes_requested' or not note:
        raise HTTPException(404, 'Writing record unavailable')
    context = obj(row['write_context'])
    ids = [UUID(e['write_id']) for e in context.get('accepted_examples', [])]
    if ids:
        async with pool().acquire() as conn:
            valid = await conn.fetchval("""SELECT count(*) FROM tank.trigger_writes
                WHERE tenant_id=$1 AND id=ANY($2::uuid[]) AND feedback_status='accepted'
                AND format IS NOT DISTINCT FROM $3""", actor.tenant_id, ids, row['format'])
        if valid != len(ids):
            raise HTTPException(409, 'Writing record unavailable')
    # Preserve the original snapshot; never silently learn from newer examples.
    body = {k: v for k, v in context.items() if k != 'voice_revision'}
    body.update(text=row['brief'], format=row['format'], revision_context={
        'previous_draft': row['draft'], 'changes': note, 'voice_revision': context.get('voice_revision')})
    return body, write_id


async def learn_notes(actor, write_ids) -> list[str]:
    from main import pool
    notes = []
    async with pool().acquire() as conn:
        for write_id in write_ids:
            note = await conn.fetchval("""SELECT f.changes FROM tank.trigger_feedback f
                JOIN tank.trigger_writes w ON w.tenant_id=f.tenant_id AND w.id=f.write_id
                WHERE f.tenant_id=$1 AND f.write_id=$2 AND f.status='changes_requested'
                AND btrim(f.changes)<>'' ORDER BY f.created_at DESC,f.id DESC LIMIT 1""", actor.tenant_id, UUID(str(write_id)))
            if not note:
                raise HTTPException(404, 'Writing record unavailable')
            notes.append(note)
    return notes


def score_rows(rows, now):
    import re
    from datetime import timedelta
    formats = ('product description', 'Google Business Profile post', 'Facebook post', 'Instagram caption',
               'LinkedIn post', 'X post', 'email', 'page meta description', 'article', None)
    periods = {}
    for key, lower, upper in [('last_30_days', now-timedelta(days=30), now),
                              ('prior_30_days', now-timedelta(days=60), now-timedelta(days=30))]:
        metrics = {}
        for name in formats:
            group = [r for r in rows if r['format'] == name and lower <= r['created_at'] < upper]
            count = len(group)
            words = sum(len(re.findall(r"\w+", r['draft'])) for r in group)
            metrics[name or 'unspecified'] = {'drafts': count,
                **{status + '_rate': sum(r['feedback_status'] == status for r in group) / count if count else 0
                   for status in ('accepted', 'changes_requested', 'rejected')},
                'findings_per_1000_words': sum(obj(r['checker_results']).get('issue_count', 0) for r in group) * 1000 / words if words else 0,
                'drafts_using_accepted_examples': sum(bool(r['accepted_write_ids']) for r in group)}
        periods[key] = metrics
    return periods


async def scorecard(actor):
    from datetime import datetime, timezone, timedelta
    from main import pool
    now = datetime.now(timezone.utc)
    async with pool().acquire() as conn:
        rows = await conn.fetch('SELECT format,draft,feedback_status,checker_results,accepted_write_ids,created_at FROM tank.trigger_writes WHERE tenant_id=$1 AND created_at >= $2 AND created_at < $3', actor.tenant_id, now-timedelta(days=60), now)
    return {'as_of': now.isoformat(), **score_rows(rows, now)}
