"""OPLI code-session projection; existing thread ownership and routing policy apply."""
from __future__ import annotations
import re
import uuid
from typing import Any
from fastapi import Depends, HTTPException

# Same credential families as cli/src/code-tools.mjs, followed by Tank's
# existing storage mask. Files, patches, project notes and tool results are
# never added to durable code history.
_PATTERNS = [
    r'\bAKIA[0-9A-Z]{16}\b', r'\bghp_[A-Za-z0-9]{20,}\b', r'\bgithub_pat_[A-Za-z0-9_]{20,}\b',
    r'\bxox[baprs]-[A-Za-z0-9-]{10,}', r'\bsk-ant-[A-Za-z0-9_\-]{10,}',
    r'(?<![A-Za-z0-9_])sk-[A-Za-z0-9]{20,}', r'\b(?:sk|pk|rk)_live_[A-Za-z0-9]{8,}',
    r'\bAIza[0-9A-Za-z0-9_\-]{30,}', r'\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}',
    r'''(?:^|[^A-Za-z0-9])(?:aws_secret_access_key|api[_-]?key|secret[_-]?key|client[_-]?secret|password|passwd|access[_-]?token|auth[_-]?token)['"]?\s*[:=]\s*['"]?[^\s'"#]{12,}''',
    r'tv[rd]?1\.(?:us-east|za)\.[A-Za-z0-9_.-]+', r'tzd1\.[A-Za-z0-9_.-]+',
]
_SECRET = re.compile('|'.join(_PATTERNS), re.I)

def code_text(value: str) -> str:
    from main import _storage_mask
    value = re.sub(r'-----BEGIN ((?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY)-----[\s\S]*?(?:-----END \1-----|$)', '[redacted]', value)
    value = '\n'.join('[redacted]' if _SECRET.search(line) else line for line in value.split('\n'))
    masked = _storage_mask(value)
    return masked if isinstance(masked, str) else '[omitted: masking unavailable]'


def remember(state: dict, kind: str, content: str) -> None:
    state['code_session'] = True
    history = state.setdefault('code_history', [])
    content = code_text(content)
    if len(content.encode()) <= 200000:
        history.append({'kind': kind, 'content': content})
    while len(history) > 40 or sum(len(item['content'].encode()) for item in history) > 200000:
        history.pop(0)


async def visible_models(actor) -> list[dict]:
    import command
    from main import list_models, model_call_refusal, box_region
    actor = await command.authorized_actor(actor)
    result = []
    for row in await list_models():
        name = row.get('name')
        if not isinstance(name, str):
            continue
        if not actor.founder and model_call_refusal('', name, box=box_region(), tenant_id=str(actor.tenant_id), task_type='command.code'):
            continue
        # Never expose upstream credentials, provider config or arbitrary fields.
        result.append({key: row[key] for key in ('name', 'input_usd_per_m', 'output_usd_per_m') if key in row})
    return result


def install(command) -> None:
    @command.router.get('/code-models')
    async def code_models(actor: Any = Depends(command.identity)) -> list[dict]:
        return await visible_models(actor)

    @command.router.get('/code-sessions')
    async def code_sessions(actor: Any = Depends(command.identity)) -> list[dict]:
        actor = await command.authorized_actor(actor)
        async with command.db().acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tank.command_threads WHERE tenant_id=$1 AND user_id=$2 AND trigger_signin_issuer=$3 "
                "AND state->>'code_session'='true' ORDER BY updated_at DESC LIMIT 100",
                actor.tenant_id, actor.user_id, __import__('trigger_signin_config').issuer())
        return [{'thread_id': str(row['thread_id']), 'title': code_text(row['title']), 'updated_at': row['updated_at']}
                for row in rows]

    @command.router.get('/code-sessions/{thread_id}')
    async def code_session(thread_id: uuid.UUID, actor: Any = Depends(command.identity)) -> dict:
        actor = await command.authorized_actor(actor)
        async with command.db().acquire() as conn:
            row = await command.owned(conn, thread_id, actor)
        state = command.unpack(row['state'])
        if not state.get('code_session'):
            raise HTTPException(404, 'Code session not found')
        history = [{'kind': item['kind'], 'content': code_text(item['content'])}
                   for item in state.get('code_history', []) if item.get('kind') in {'user', 'assistant'}]
        return {'thread_id': str(thread_id), 'history': history, 'active': bool(state.get('active_turn')),
                'cost_usd': command.thread_totals(state)['cost_usd'], 'savings_usd': savings(state)}

# Client-owned MCP credentials never cross this API. Policy stores identities only.
from pydantic import BaseModel, ConfigDict, Field, field_validator

class MCPPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server_id: str = Field(min_length=1, max_length=2000)
    blocked: bool

    @field_validator('server_id')
    @classmethod
    def identity(cls, value):
        from urllib.parse import urlsplit
        if value == '*' or re.fullmatch(r'stdio:[A-Za-z0-9_.-]+', value):
            return value
        url = urlsplit(value.removeprefix('http:'))
        if not value.startswith('http:https://') or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            raise ValueError('Invalid MCP server identity')
        return value

class CodeUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    thread_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    base64: str = Field(max_length=6990508)


def install_reach(command):
    @command.router.get('/code-mcp-policy')
    async def code_mcp_policy(actor: Any = Depends(command.identity)) -> dict:
        actor = await command.authorized_actor(actor)
        async with command.db().acquire() as conn:
            rows = await conn.fetch('SELECT server_id FROM tank.opli_mcp_policy WHERE tenant_id=$1 AND blocked', actor.tenant_id)
        return {'blocked': [row['server_id'] for row in rows]}

    @command.router.put('/code-mcp-policy')
    async def set_code_mcp_policy(body: MCPPolicy, actor: Any = Depends(command.identity)) -> dict:
        actor = await command.authorized_actor(actor)
        if not (actor.founder or actor.tenant_admin):
            raise HTTPException(403, 'Tenant administrator required')
        async with command.db().acquire() as conn:
            await conn.execute('''INSERT INTO tank.opli_mcp_policy(tenant_id,server_id,blocked,updated_by)
                VALUES($1,$2,$3,$4) ON CONFLICT(tenant_id,server_id) DO UPDATE
                SET blocked=EXCLUDED.blocked,updated_by=EXCLUDED.updated_by,updated_at=now()''',
                actor.tenant_id, body.server_id, body.blocked, actor.user_id)
        return {'server_id': body.server_id, 'blocked': body.blocked}

    @command.router.post('/code-attachments')
    async def code_attachment(body: CodeUpload, actor: Any = Depends(command.identity)) -> dict:
        import base64
        from starlette.requests import Request
        try:
            content = base64.b64decode(body.base64, validate=True)
        except ValueError:
            raise HTTPException(422, 'Invalid attachment') from None
        if len(content) > command.MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, 'Attachment exceeds 5 MiB')
        async def receive():
            return {'type': 'http.request', 'body': content, 'more_body': False}
        return await command.upload_attachment(body.thread_id, Request({'type':'http'}, receive), body.name, actor)

from contextvars import ContextVar
TOKEN_SINK = ContextVar('opli_code_token_sink', default=None)


def savings(state):
    total = 0.0
    for step_id, step in state.get('steps', {}).items():
        model = step.get('requested_model')
        if model is None:
            continue
        rows = [r for r in state['receipts'] if r['step_id'] == step_id]
        if not rows or any(r.get('cost_usd') is None for r in rows):
            return None
        if model != 'auto':
            if any(r.get('auto_cost_status') != 'priced' for r in rows):
                return None
            total += sum(r['auto_cost_usd'] - r['cost_usd'] for r in rows)
    return total


async def streaming(command, thread_id, body, actor):
    import asyncio
    import contextlib
    from fastapi.responses import StreamingResponse
    from command_stream import AnswerText
    async def events():
        queue = asyncio.Queue(maxsize=1)
        done = asyncio.Event()
        preview = AnswerText(field='answer', max_bytes=400000)
        async def token(delta):
            if preview.feed(delta):
                await queue.put(('code_preview', {'text':code_text(preview.shown)}))
        async def work():
            marker = TOKEN_SINK.set(token)
            try:
                result = await command.code_turn(thread_id, body, actor)
                await queue.put(('code_result', result))
            except asyncio.CancelledError:
                raise
            except Exception:
                await queue.put(('error', {'message':'Code stream failed; nothing was retried.'}))
            finally:
                TOKEN_SINK.reset(marker)
                done.set()
        task = asyncio.create_task(work())
        try:
            while not done.is_set() or not queue.empty():
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=0.25)
                    yield 'event: ' + kind + '\ndata: ' + command.dump(data) + '\n\n'
                except asyncio.TimeoutError:
                    yield ': heartbeat\n\n'
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


async def validate_mcp(schema, arguments, timeout=5):
    """Untrusted MCP schemas cannot fetch URLs or block the API event loop."""
    import asyncio
    import json
    import sys
    source = """
import json, sys
from jsonschema import Draft202012Validator
from referencing import Registry
try:
    schema, arguments = json.load(sys.stdin)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, registry=Registry()).validate(arguments)
except Exception:
    sys.exit(1)
"""
    payload = json.dumps([schema, arguments]).encode()
    if len(payload) > 1_500_000:
        raise HTTPException(422, 'Code MCP schema or arguments exceed the limit')
    process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', source,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, env={})
    try:
        await asyncio.wait_for(process.communicate(payload), timeout)
        if process.returncode:
            raise HTTPException(422, 'Code MCP schema or arguments refused')
    except asyncio.TimeoutError:
        raise HTTPException(422, 'Code MCP validation timed out; no tool was run') from None
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
