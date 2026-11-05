"""MCP 2025-11-25 Streamable HTTP, JSON response mode, no model router.

The latest official SDK cannot resolve alongside Tank's Pydantic pin (see
MCP README). Only transport/session bookkeeping is implemented here; tools,
identity, validation, durable Confirm and receipts reuse the Command corridor.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

import command
import device_login as device
import personal_tokens as pat
from mcp_oauth import ISSUER, PROTECTED_METADATA, bounded_body, east_only
from tenant_db import tenant_scope

router = APIRouter(tags=['MCP'])
PROTOCOL = '2025-11-25'
READ_WAIT_SECONDS = 60
ORIGINS = frozenset({ISSUER, 'https://claude.ai', 'https://chatgpt.com', 'https://cursor.com'})
SESSION = re.compile(r'[A-Za-z0-9_-]{43}\Z')
_background: set[asyncio.Task] = set()


def rpc_error(ident, code: int, message: str, status: int = 200):
    return JSONResponse({'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': message}},
                        status_code=status, headers=device.NO_STORE)


def rpc_result(ident, value, *, session=None):
    headers = {**device.NO_STORE}
    if session:
        headers['Mcp-Session-Id'] = session
    return JSONResponse({'jsonrpc': '2.0', 'id': ident, 'result': value}, headers=headers)


def readonly(name: str) -> bool:
    adapter = command.ADAPTERS.get(name)
    return adapter is not None and adapter.method == 'GET' and not adapter.paid


def tools(actor):
    result = []
    for name in command.allowed_tools(actor):
        adapter = command.ADAPTERS.get(name)
        # worker.build is Command's existing special action, without an HTTP
        # adapter. Its existing validator uses BuildArgs and always asks Confirm.
        arguments = adapter.arguments if adapter else command.BuildArgs
        result.append({'name': name, 'description': adapter.label if adapter else name,
                       'inputSchema': arguments.model_json_schema(), 'annotations': {'readOnlyHint': readonly(name)}})
    return {'tools': result}


async def session_row(request, actor, credential):
    session = request.headers.get('mcp-session-id', '')
    if not SESSION.fullmatch(session):
        raise HTTPException(404, 'Session not found')
    async with command.db().acquire() as conn:
        row = await conn.fetchrow('SELECT thread_id,initialized FROM tank.mcp_sessions WHERE tenant_id=$1 AND user_id=$2 '
            'AND token_id=$3 AND client_id=$4 AND session_hash=$5 AND expires_at>now()',
            actor.tenant_id, actor.user_id, credential['token_id'], credential['client_id'], pat.digest(session))
    if row is None:
        raise HTTPException(404, 'Session not found')
    return session, row


async def call_tool(params: dict, actor, row):
    name, arguments = params.get('name'), params.get('arguments', {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise HTTPException(422, 'Invalid tool arguments')
    action = command.validate_action(command.PlannedAction(tool=name, arguments=arguments), actor)
    thread = row['thread_id']
    async with command.editing(thread, actor) as (conn, owned, state):
        actor = await command.authorized_actor(actor, conn)
        action = command.validate_action(action, actor)
        card = await command.propose_action(conn, owned, state, action, actor)
    link = ISSUER + '/chats?thread=' + str(thread)
    pending = {'thread_url': link, 'card_id': card['card_id']}
    if not readonly(name):
        payload = {**pending, 'status': 'pending', 'message': 'Waiting for Confirm in /chats.'}
        return {'content': [{'type': 'text', 'text': command.dump(payload)}]}
    # This is the same durable claim and authorization recheck as human Confirm.
    task = asyncio.create_task(command.card_action(uuid.UUID(card['card_id']), command.CardAction(action='confirm'), actor))
    _background.add(task)
    def finished(done):
        _background.discard(done)
        if not done.cancelled():
            done.exception()  # Retrieve failures after the caller's wait ended.
    task.add_done_callback(finished)
    try:
        confirmed = await asyncio.wait_for(asyncio.shield(task), timeout=READ_WAIT_SECONDS)
    except TimeoutError:
        payload = {**pending, 'status': 'running', 'message': 'The call is still running.'}
        return {'content': [{'type': 'text', 'text': command.dump(payload)}]}
    async with command.db().acquire() as conn:
        owned = await command.owned(conn, thread, actor)
    state = command.unpack(owned['state'])
    receipts = [command.public_receipt(item, actor.founder) for item in state['receipts'] if item['step_id'] == card['card_id']]
    payload = {**pending, 'result': confirmed.get('result'), 'receipt_status': confirmed.get('receipt_status'),
               'receipts': receipts, 'totals': command.totals(receipts) if receipts else None}
    failed = confirmed['status'] != 'confirmed'
    if failed:
        payload['error'] = confirmed.get('error')
    return {'content': [{'type': 'text', 'text': command.dump(payload)}], 'isError': failed}


@router.api_route('/mcp', methods=['POST', 'GET', 'DELETE'])
async def endpoint(request: Request):
    east_only()
    if 'origin' in request.headers and request.headers['origin'] not in ORIGINS:
        return Response(status_code=403, headers=device.NO_STORE)
    try:
        actor, credential = await pat.bearer_identity(request, audience='mcp')
    except HTTPException:
        return Response(status_code=401, headers={**device.NO_STORE,
            'WWW-Authenticate': f'Bearer resource_metadata="{PROTECTED_METADATA}"'})
    if not credential.get('client_id'):
        return Response(status_code=401, headers={**device.NO_STORE,
            'WWW-Authenticate': f'Bearer resource_metadata="{PROTECTED_METADATA}"'})
    with tenant_scope(actor.tenant_id):
        if request.method != 'POST':
            if request.headers.get('mcp-protocol-version') != PROTOCOL:
                return Response(status_code=400, headers=device.NO_STORE)
            session, row = await session_row(request, actor, credential)
            if request.method == 'GET':
                # No unsolicited server messages in G1; permitted by the transport.
                return Response(status_code=405, headers={**device.NO_STORE, 'Allow': 'POST, DELETE'})
            async with command.db().acquire() as conn:
                await conn.execute('DELETE FROM tank.mcp_sessions WHERE tenant_id=$1 AND token_id=$2 AND session_hash=$3',
                                   actor.tenant_id, credential['token_id'], pat.digest(session))
            return Response(status_code=204, headers=device.NO_STORE)
        accept = {part.split(';')[0].strip().lower() for part in request.headers.get('accept', '').split(',')}
        if not all(media in accept for media in ('application/json', 'text/event-stream')):
            return Response(status_code=406, headers=device.NO_STORE)
        if request.headers.get('content-type', '').split(';')[0].lower() != 'application/json':
            return Response(status_code=415, headers=device.NO_STORE)
        try:
            async with asyncio.timeout(15):
                message = json.loads(await bounded_body(request))
        except (ValueError, UnicodeError, TimeoutError):
            return rpc_error(None, -32700, 'Parse error', 400)
        if (not isinstance(message, dict) or message.get('jsonrpc') != '2.0'
                or ('id' in message and (isinstance(message['id'], bool) or not isinstance(message['id'], (str, int, type(None)))))):
            return rpc_error(None, -32600, 'Invalid Request', 400)
        ident, method = message.get('id'), message.get('method')
        params = message.get('params', {})
        if not isinstance(params, dict):
            return rpc_error(ident, -32602, 'Invalid params')
        if method == 'initialize':
            if ('id' not in message or request.headers.get('mcp-session-id')
                    or not isinstance(params.get('protocolVersion'), str)
                    or not isinstance(params.get('capabilities'), dict)
                    or not isinstance(params.get('clientInfo'), dict)
                    or not all(isinstance(params['clientInfo'].get(key), str) for key in ('name', 'version'))):
                return rpc_error(ident, -32602, 'Invalid params')
            session = secrets.token_urlsafe(32)
            thread = await command.create_thread(command.NewThread(title=credential['name']), actor)
            async with command.db().acquire() as conn:
                await conn.execute('INSERT INTO tank.mcp_sessions(session_hash,tenant_id,user_id,token_id,client_id,thread_id) VALUES($1,$2,$3,$4,$5,$6)',
                    pat.digest(session), actor.tenant_id, actor.user_id, credential['token_id'], credential['client_id'], uuid.UUID(thread['thread_id']))
            return rpc_result(ident, {'protocolVersion': PROTOCOL, 'capabilities': {'tools': {}},
                                    'serverInfo': {'name': 'Tervory', 'version': '1.0.0'}}, session=session)
        if request.headers.get('mcp-protocol-version') != PROTOCOL:
            return rpc_error(ident, -32600, 'Unsupported protocol version', 400)
        session, row = await session_row(request, actor, credential)
        if method == 'notifications/initialized' and 'id' not in message:
            async with command.db().acquire() as conn:
                await conn.execute('UPDATE tank.mcp_sessions SET initialized=true WHERE tenant_id=$1 AND token_id=$2 AND session_hash=$3',
                                   actor.tenant_id, credential['token_id'], pat.digest(session))
            return Response(status_code=202, headers=device.NO_STORE)
        if method is None and ('result' in message or 'error' in message):
            return Response(status_code=202, headers=device.NO_STORE)
        if 'id' not in message:
            if isinstance(method, str) and method.startswith('notifications/') or method is None and ('result' in message or 'error' in message):
                return Response(status_code=202, headers=device.NO_STORE)
            return rpc_error(None, -32600, 'Invalid Request', 400)
        if method == 'ping':
            return rpc_result(ident, {})
        if not row['initialized']:
            return rpc_error(ident, -32600, 'Session not initialized', 400)
        if method == 'tools/list':
            if params.keys() - {'cursor', '_meta'}:
                return rpc_error(ident, -32602, 'Invalid params')
            return rpc_result(ident, tools(actor))
        if method == 'tools/call':
            try:
                result = await call_tool(params, actor, row)
            except HTTPException as error:
                result = {'content': [{'type': 'text', 'text': error.detail if isinstance(error.detail, str) else 'Tool call refused'}], 'isError': True}
            except Exception:
                # Never expose transport credentials, SQL or internal exception data.
                return rpc_error(ident, -32603, 'Tool call failed')
            return rpc_result(ident, result)
        return rpc_error(ident, -32601, 'Method not found')


def register(app):
    from mcp_oauth import router as oauth_router
    app.include_router(oauth_router)
    app.include_router(router)
