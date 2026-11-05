"""Submit explicitly, estimate by live HF hardware price, reconcile billed receipts.

No production DB credential is ever sent to a GPU job. Shared training runs on
the US box; the Zenas-only run stays with its source and holdout rows on ZA.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import uuid

PLATFORM = uuid.UUID('00000000-0000-0000-0000-000000000001')
HARDWARE = frozenset({'a10g-small', 'a10g-large'})


def training_hardware(agent: str) -> str:
    if agent not in ('', 'zenas'):
        raise ValueError('Unsupported training agent')
    # Qwen3-4B bf16 LoRA with batch size one and gradient checkpointing fits
    # the 24 GB A10G used by the small flavor, including Zenas at 4096 tokens.
    return 'a10g-small'


def estimate(rate: Decimal, minutes: int, cap: Decimal) -> Decimal:
    if not rate.is_finite() or not cap.is_finite() or rate <= 0 or cap <= 0 or minutes <= 0:
        raise ValueError('Invalid budget')
    amount = rate * minutes
    if amount > cap:
        raise ValueError('Estimated training cost exceeds HF_TRAIN_MAX_USD')
    return amount


def validate_name(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value):
        raise ValueError('Invalid repository name')
    return value


async def link_snapshot_batches(conn, job_id: str, model_repo: str, day: str) -> None:
    """Remember which batches this shared model was trained on."""
    if not day:
        return
    rows = await conn.fetch(
        """SELECT batch_id, region FROM tank.training_snapshot_batches
        WHERE tenant_id=$1 AND day=$2""",
        PLATFORM, date.fromisoformat(day),
    )
    for row in rows:
        await conn.execute(
            """INSERT INTO tank.training_job_batches (tenant_id, job_id, model_repo, batch_id, region)
            VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING""",
            PLATFORM, job_id, model_repo, row["batch_id"], row["region"],
        )


async def record_receipt(conn, job_id: str, actual: Decimal, evaluation: dict | None = None):
    """Append the provider's final billed amount once, including failed jobs."""
    if not actual.is_finite() or actual < 0:
        raise ValueError('Invalid billed amount')
    async with conn.transaction():
        row = await conn.fetchrow('SELECT * FROM tank.training_jobs WHERE id=$1 AND tenant_id=$2 FOR UPDATE', job_id, PLATFORM)
        if not row:
            raise ValueError('Unknown training job')
        if row['ledger_id']:
            if Decimal(row['actual_usd']) != actual:
                raise ValueError('Receipt conflicts with recorded bill')
            return row['ledger_id']
        if evaluation is not None:
            n = int(evaluation.get('n', 0))
            passed = int(evaluation.get('passed', -1))
            agreement = evaluation.get('agreement')
            if (evaluation.get('gold_holdout') is not True or evaluation.get('method') != 'exact_outcome'
                    or n < 0 or passed < 0 or passed > n or agreement != (passed / n if n else 0.0)):
                raise ValueError('Invalid holdout evaluation')
            await conn.execute("""INSERT INTO tank.policy
                (task_type,rung,model,priority,cache_scope,max_tokens_out,eval_threshold,
                 candidate_model,shadow_rate,min_shadow_samples,max_reject_rate,auto_promote)
                VALUES ('zenas.reasoning',1,'luna','interactive','region',4096,.99,
                        'tervory-own-zenas-za',0,200,.05,false)
                ON CONFLICT DO NOTHING""")
            await conn.execute("""UPDATE tank.policy
                SET candidate_model='tervory-own-zenas-za', shadow_rate=0,
                    eval_threshold=GREATEST(eval_threshold,.99),
                    min_shadow_samples=GREATEST(min_shadow_samples,200), auto_promote=false
                WHERE task_type='zenas.reasoning' AND tenant_id IS NULL""")
            for item in evaluation.get('rows', []):
                await conn.execute("""INSERT INTO tank.shadow
                    (ledger_id,tenant_id,task_type,primary_model,candidate_model,
                     primary_content,candidate_content,agreement,method)
                    VALUES (gen_random_uuid(),$1,'zenas.reasoning','court-order',
                            'tervory-own-zenas-za','',$2,$3,'exact_outcome')""",
                    PLATFORM, str(item.get('predicted') or ''), 1 if item.get('passed') is True else 0)
        ident = await conn.fetchval("""INSERT INTO tank.ledger
            (tenant_id,region,agent,task_type,model,priority,cost_usd,outcome)
            VALUES ($1,$2,'tank','training.lora',$3,'batch',$4,'delivered') RETURNING id""",
            PLATFORM, row['region'], row['model_repo'], actual)
        await conn.execute('UPDATE tank.training_jobs SET actual_usd=$3,ledger_id=$4 WHERE id=$1 AND tenant_id=$2', job_id, PLATFORM, actual, ident)
        return ident


async def run(args):
    import asyncpg
    from huggingface_hub import HfApi
    agent = getattr(args, 'agent', '')
    region = os.environ.get('TANK_REGION', 'us-east')
    if region != 'us-east' and not (region == 'za' and agent == 'zenas'):
        raise ValueError('Submit and reconcile training from the US box')
    org = validate_name(os.environ['HF_ORG'])
    api = HfApi(token=os.environ['HF_TOKEN'])
    if args.receipt:
        receipt = json.loads(Path(args.receipt).read_text())
        job_id = validate_name(receipt['job_id'])
        job = api.inspect_job(job_id=job_id, namespace=org)
        if job.status.stage not in ('COMPLETED', 'ERROR', 'CANCELED', 'DELETED'):
            raise ValueError('Job has not finished')
        if receipt.get('currency') != 'USD' or receipt.get('namespace') != org:
            raise ValueError('Receipt scope does not match')
        conn = await asyncpg.connect(os.environ['DATABASE_URL'])
        try:
            await record_receipt(conn, job_id, Decimal(str(receipt['billed_usd'])))
        finally:
            await conn.close()
        print('Billed cost recorded')
        return {'status': 'complete', 'name': job_id, 'cost_usd': Decimal(str(receipt['billed_usd']))}
    if getattr(args, 'job_id', ''):
        job_id = validate_name(args.job_id)
        job = api.inspect_job(job_id=job_id, namespace=org)
        if job.status.stage not in ('COMPLETED', 'ERROR', 'CANCELED', 'DELETED'):
            return {'status': 'job has not finished'}
        conn = await asyncpg.connect(os.environ['DATABASE_URL'])
        try:
            stored = await conn.fetchrow(
                'SELECT model_repo FROM tank.training_jobs WHERE id=$1 AND tenant_id=$2', job_id, PLATFORM,
            )
            if not stored:
                raise ValueError('Unknown training job')
            from huggingface_hub import hf_hub_download
            evaluation_path = hf_hub_download(repo_id=stored['model_repo'], filename='holdout.json')
            evaluation = json.loads(Path(evaluation_path).read_text())
            ledger_id = await record_receipt(conn, job_id, Decimal(args.billed_usd), evaluation)
        finally:
            await conn.close()
        return {'status': 'complete', 'name': job_id, 'cost_usd': Decimal(args.billed_usd),
                'ledger_id': str(ledger_id)}
    flavor = training_hardware(agent)
    if flavor not in HARDWARE:
        raise ValueError('Training hardware is not allowed')
    hardware = next(h for h in api.list_jobs_hardware() if h.name == flavor)
    if hardware.unit_label != 'minute':
        raise ValueError('Unknown billing unit')
    amount = estimate(Decimal(hardware.unit_cost_micro_usd) / 1_000_000, args.minutes,
                      Decimal(os.environ.get('HF_TRAIN_MAX_USD') or '50'))
    print(f'Maximum estimated compute cost: USD {amount:.4f}')
    if not args.submit:
        return {'status': 'ready', 'cost_usd': amount, 'hardware': flavor}
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        await conn.execute('SELECT pg_advisory_lock(871228)')
        try:
            if await conn.fetchval('SELECT EXISTS (SELECT 1 FROM tank.training_jobs WHERE tenant_id=$1 AND ledger_id IS NULL)', PLATFORM):
                raise ValueError('Reconcile the previous training job before submitting another')
            repo = f'{org}/tervory-own-zenas-za' if agent == 'zenas' else f'{org}/tervory-squad-{uuid.uuid4().hex}'
            pending = f'pending-{uuid.uuid4().hex}'
            # Autocommit the reservation before contacting HF. An uncertain submit
            # must remain blocking, even after an exception or process crash.
            await conn.execute('INSERT INTO tank.training_jobs(id,tenant_id,region,model_repo,estimated_usd) VALUES ($1,$2,$3,$4,$5)',
                               pending, PLATFORM, region, repo, amount)
            api.create_repo(repo_id=repo, private=True, repo_type='model')
            if api.model_info(repo).private is not True:
                raise ValueError('Output repository must be private')
            job = api.run_uv_job(str(Path(__file__).with_name('train.py')), namespace=org,
                                flavor=flavor, timeout=args.minutes * 60,
                                env={'HF_ORG': org, 'TRAIN_DAY': args.day, 'OUTPUT_REPO': repo,
                                     'TRAIN_AGENT': agent},
                                secrets={'HF_TOKEN': os.environ['HF_TOKEN']})
            await conn.execute('UPDATE tank.training_jobs SET id=$1 WHERE id=$2 AND tenant_id=$3', job.id, pending, PLATFORM)
            await link_snapshot_batches(conn, job.id, repo, args.day)
            print(f'Training job: {job.id}')
            return {'status': 'running', 'name': job.id, 'cost_usd': amount, 'hardware': flavor}
        finally:
            await conn.execute('SELECT pg_advisory_unlock(871228)')
    finally:
        await conn.close()



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--minutes', type=int, default=120)
    parser.add_argument('--day', default='')
    parser.add_argument('--submit', action='store_true')
    parser.add_argument('--agent', choices=['zenas'], default='')
    parser.add_argument('--receipt', help='Local final HF billing receipt JSON; no credentials')
    parser.add_argument('--job-id', default='')
    parser.add_argument('--billed-usd', default='0')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.submit:
        from datetime import date
        date.fromisoformat(args.day)
    try:
        result = asyncio.run(run(args))
        if args.json:
            print(json.dumps(result, default=str))
    except Exception:
        raise SystemExit('Training operation failed; check configuration, budget and provenance. Credentials are not logged.') from None
