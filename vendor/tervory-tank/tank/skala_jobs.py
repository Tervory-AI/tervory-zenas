"""Serialize evaluation work and replay a completed browser request's receipt."""
from contextvars import ContextVar
from functools import wraps
import hashlib
import inspect
import json
import re

from fastapi import HTTPException

request_key = ContextVar('skala_request_key', default='')


def single_evaluation(function):
    @wraps(function)
    async def run(skill_id, version, body=None):
        import main
        key = request_key.get()
        if key and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key):
            raise HTTPException(400, 'Request refused.')
        owner = body.by if body else None
        fingerprint = hashlib.sha256(json.dumps([skill_id, version, owner]).encode()).hexdigest()
        # One platform evaluation holds a session connection; serializing also
        # leaves the existing pool available for its evidence and model receipts.
        lock = int.from_bytes(hashlib.sha256(b'skala.evaluation').digest()[:8], 'big', signed=True)
        # This is a platform session lock, not a transaction around the eval.
        # Keep the pending receipt committed before work, and do not block
        # concurrent startup indexes with an idle transaction snapshot.
        async with main.admin_pool().acquire() as conn:
            if not await conn.fetchval('SELECT pg_try_advisory_lock($1::bigint)', lock):
                raise HTTPException(409, 'An evaluation is already running. Reload before trying again.')
            try:
                if key:
                    previous = await conn.fetchrow("""SELECT action,detail FROM tank.policy_log
                        WHERE task_type='skala.evaluation' AND detail->>'key'=$1
                        ORDER BY created_at DESC LIMIT 1""", key)
                    if previous:
                        detail = previous['detail']
                        detail = json.loads(detail) if isinstance(detail, str) else detail
                        if detail['fingerprint'] != fingerprint or previous['action'] != 'complete':
                            raise HTTPException(409, 'An evaluation is already running. Reload before trying again.')
                        return detail['result']
                    # Reserve before work. A lost worker leaves an uncertain request
                    # blocked instead of replaying model calls or evidence writes.
                    await conn.execute("""INSERT INTO tank.policy_log(task_type,action,by_user,detail)
                        VALUES('skala.evaluation','pending',$1,$2::jsonb)""", owner,
                        json.dumps({'key': key, 'fingerprint': fingerprint}))
                result = await function(skill_id, version, body)
                if key:
                    await conn.execute("""UPDATE tank.policy_log SET action='complete', detail=$2::jsonb
                        WHERE task_type='skala.evaluation' AND action='pending' AND detail->>'key'=$1""", key,
                        json.dumps({'key': key, 'fingerprint': fingerprint, 'result': result}, default=str))
                return result
            finally:
                await conn.execute('SELECT pg_advisory_unlock($1::bigint)', lock)
    run.__signature__ = inspect.signature(function, eval_str=True)
    return run
