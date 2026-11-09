"""Stage 2 checks run inside the existing tenant/user-owned code transaction."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

# This is a build-time feature until the founder enables it. No local service,
# model, paid request, or runtime configuration is introduced by importing it.
SETTING = 'ENGINE_CODE_GATE_ENABLED'
MAX_ERRORS = 64
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def enabled() -> bool:
    return os.environ.get(SETTING, '').strip() == 'true'


def command_error(output: str) -> str | None:
    """Read the existing CLI command envelope; source files are never evidence."""
    try:
        envelope = json.loads(output)
    except (ValueError, RecursionError):
        return 'invalid-command-result'
    if not isinstance(envelope, dict) or not isinstance(envelope.get('result'), dict):
        return 'invalid-command-result'
    result = envelope['result']
    if isinstance(result.get('error'), str):
        error = result['error']
    elif result.get('ran') is not True:
        return None  # A refused command did not execute a compiler or test.
    elif result.get('code') == 0 and not result.get('timed_out'):
        return None
    else:
        error = result.get('output')
        if not isinstance(error, str):
            return 'invalid-command-result'
        if not error.strip():
            error = json.dumps({'code': result.get('code'), 'timed_out': result.get('timed_out')}, sort_keys=True)
    error = ANSI.sub('', error).replace('\r\n', '\n').strip()
    return hashlib.sha256(error.encode()).hexdigest()


def advance(state: dict[str, Any], prompt_id: str, results, progress=None, continuation=False) -> dict | None:
    """Called once per durably reserved request, before routing even one token.

    Only digests/counts survive; no tool output, commands, source or diagnostics
    are added to the resumable thread. A new user prompt opens a new task.
    """
    if not enabled():
        return None
    gate = state.get('code_gate')
    if not isinstance(gate, dict) or gate.get('prompt_id') != prompt_id:
        gate = {'prompt_id': prompt_id, 'errors': {}, 'stopped': None}
        state['code_gate'] = gate
    if gate['stopped']:
        return gate['stopped']
    # A model can request several commands at once. The same error occurring
    # three times in that batch must stop just as it would across three rounds.
    for result in results:
        if result.name != 'run_command':
            continue
        fingerprint = command_error(result.output)
        if fingerprint is None:
            continue
        if fingerprint == 'invalid-command-result':
            gate['stopped'] = {'status': 'stopped', 'reason': 'invalid_command_result',
                               'message': 'A local check result is incomplete. Run the check again before continuing.'}
            break
        errors = gate['errors']
        if fingerprint not in errors and len(errors) >= MAX_ERRORS:
            gate['stopped'] = {'status': 'stopped', 'reason': 'error_limit',
                               'message': 'This task has too many distinct errors. Review the local checks before continuing.'}
            break
        errors[fingerprint] = errors.get(fingerprint, 0) + 1
        if errors[fingerprint] >= 3:
            gate['stopped'] = {'status': 'stopped', 'reason': 'repeated_error', 'occurrences': errors[fingerprint],
                               'message': 'The same local error occurred three times. No further model call was made. Review the error and start a new task when ready.'}
            break
    if not gate['stopped']:
        import code_progress
        gate['stopped'] = code_progress.advance(gate, progress, results, continuation)
    return gate['stopped']


async def stopped_response(command, conn, row, state, actor, turn_id, gate):
    """Persist the $0 stop through Command's existing task/receipt/event path."""
    from trigger_routes import code_receipt
    from uuid import UUID
    ledger = await code_receipt(actor.tenant_id, 'command.code', task_id=UUID(turn_id), agent='orchestrator', conn=conn)
    await command.record_receipts(conn, row, state, turn_id, [ledger])
    await conn.execute("UPDATE tank.tasks SET status='done',result=$3,ledger_id=$4,finished_at=now() "
                       "WHERE id=$1 AND tenant_id=$2", UUID(turn_id), actor.tenant_id, gate['message'], ledger)
    state['active_turn'] = None
    await command.step(conn, row, state, {**state['steps'][turn_id], 'status': 'done', 'label': 'Stopped before model call',
                                         'code_gate': gate, 'receipt_status': 'recorded'})
    await command.event(conn, row, 'done', {'message_id': turn_id})
    rows = [command.public_receipt(r, command.current_actor(actor).founder) for r in state['receipts'] if r['step_id'] == turn_id]
    return {'answer': gate['message'], 'requests': [], 'cards': [], 'gate': gate,
            'receipt': {'rows': rows, 'totals': command.totals(rows)},
            'turn': {'model': 'Auto', 'cost_usd': 0.0}}
