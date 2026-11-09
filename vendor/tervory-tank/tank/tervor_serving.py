"""Opt-in bridge from private Tervor evidence to Tank's existing E7 ladder."""
import os

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

import engine_quality
import tervor_eval
import tervor_registry


class Serving(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['hf-scale-zero', 'gguf']
    artifact: str = Field(min_length=1, max_length=300, pattern=r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    min_replicas: Literal[0] = 0


def enabled():
    return os.environ.get('TERVOR_SERVING_ENABLED') == 'true'


async def pinned_candidates(rt, rows):
    trained = [row for row in rows if row['model'].startswith('tervor-')]
    if not trained:
        return rows
    ordinary = [row for row in rows if not row['model'].startswith('tervor-')]
    if not enabled():
        return ordinary
    roster = {row['model_name']: row for row in await rt._roster_models()}
    return [*ordinary, *(row for row in trained if
        roster.get(row['model'], {}).get('model_info', {}).get('tervor_serving', {}).get('sha256') == row['checkpoint'])]


async def register(rt, body):
    if not enabled():
        raise HTTPException(409, 'Tervor serving is disabled; TERVOR_SERVING_ENABLED is unset')
    spec = body.serving
    if spec is None or spec.sha256 != body.checkpoint or '..' in spec.artifact.split('/'):
        raise HTTPException(422, 'A pinned scale-to-zero or GGUF serving artifact is required')
    if spec.kind == 'gguf' and not spec.artifact.endswith('.gguf'):
        raise HTTPException(422, 'Local artifacts must be GGUF')
    if rt.is_build_only_model(body.model):
        raise HTTPException(422, 'Candidate must be on the serving roster')
    roster = {r['model_name']: r for r in await rt._roster_models()}
    declared = roster.get(body.model, {}).get('model_info', {}).get('tervor_serving')
    if declared != spec.model_dump():
        raise HTTPException(422, 'Serving artifact must match the existing LiteLLM roster')
    async with rt.pool().acquire() as conn:
        model = await tervor_registry.get(conn, body.tenant_id, body.model)
        if not model or not body.task_type.startswith(model['product'] + '.'):
            raise HTTPException(422, 'Model must belong to this tenant and product')
        if model['rung'] != ('local' if spec.kind == 'gguf' else 'own-trained'):
            raise HTTPException(422, 'Serving kind must match the registered rung')
        reports = await tervor_eval.reports(conn, body.tenant_id, body.model, region=rt.box_region())
        report = reports[0] if reports else {}
        current = await conn.fetchval('''SELECT model FROM tank.engine_state
            WHERE tenant_id=$1 AND region=$2 AND task_type=$3''', body.tenant_id, rt.box_region(), body.task_type)
        if current is None:
            current = (await rt.load_policy(body.task_type, body.tenant_id))['model']
        if (report.get('region') != rt.box_region() or report.get('eligible') is not True
                or report.get('status') != 'evaluated'
                or report.get('completed', 0) < 200 or report.get('quality_version') != engine_quality.VERSION
                or report.get('gold_sha256') not in model['data_hashes']
                or report.get('artifact_sha256') != body.checkpoint
                or report.get('baselines', {}).get(body.task_type) != current):
            raise HTTPException(422, 'Current pinned gold evaluation is required before shadowing')
        detail = dict(tenant_id=str(body.tenant_id), region=rt.box_region(), model_id=body.model,
            task_type=body.task_type, serving=spec.model_dump(), report_id=report['id'], rollback=current)
        async with conn.transaction():
            await conn.execute('''INSERT INTO tank.engine_candidates(tenant_id,region,task_type,model,checkpoint)
                VALUES($1,$2,$3,$4,$5) ON CONFLICT(tenant_id,region,task_type,model)
                DO UPDATE SET checkpoint=excluded.checkpoint,created_at=now()''',
                body.tenant_id, rt.box_region(), body.task_type, body.model, body.checkpoint)
            await conn.execute('''INSERT INTO tank.policy_log(task_type,action,by_user,detail)
                VALUES('tervor.serving','candidate','founder',$1::jsonb)''', tervor_registry.encoded(detail))
    return dict(state='candidate', model=body.model, checkpoint=body.checkpoint)


async def private_record(conn, tenant, model_id, *, region: str):
    model = await tervor_registry.get(conn, tenant, model_id)
    if model is None:
        return None
    reports = await tervor_eval.reports(conn, tenant, model_id, region=region)
    moves = await conn.fetch('''SELECT task_type,previous_model,samples,agreement FROM tank.engine_graduations
        WHERE tenant_id=$1 AND model=$2 AND region=$3 ORDER BY created_at DESC''', tenant, model_id, region)
    return dict(manifest=model, promotion_reports=reports, graduations=[dict(row) for row in moves])
