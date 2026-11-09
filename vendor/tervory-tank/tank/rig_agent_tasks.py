"""Rig-only adapter at the existing signed Tank task and SDK boundaries."""
import json
import os
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from agent_skills import Chat, local_region, room_key
from command_tools._user_assertion import HEADER, PostgresNonces, signed_user
from product_identity import actor_scope, product_extensions
from task_contract import check_receipt, origin, paid_identity


async def actor_for(tank, body, request):
    room_key('rig', request.headers.get('authorization', ''), request.headers.get('x-tank-key', ''))
    if os.environ.get('RIG_BRAIN_ENABLED', 'false').lower() not in {'true', '1', 'yes', 'on'}:
        raise HTTPException(409, 'Rig brain is switched off')
    local_region(tank, request.headers.get('x-home-region'), room='rig')
    actor = await signed_user(request, tenant_id=str(body.tenant_id), product='rig', required=True,
                              nonces=PostgresNonces(tank.pool(), 'tank'))
    if actor.user_id != body.user_id:
        raise HTTPException(403, 'Actor mismatch')
    return actor


async def delegate_task(tank, body, request):
    actor = await actor_for(tank, body, request)
    if body.task_key not in {'rig.ask', 'rig.build', 'rig.quote', 'rig.fit'}:
        raise HTTPException(422, 'Invalid brain task')
    if set(body.input) != {'messages'}:
        raise HTTPException(422, 'Invalid brain task')
    # Apply the existing SDK text/tool validation before starting any work.
    from agent_skills import validate_chat
    try:
        chat = Chat(messages=body.input['messages'])
    except (ValueError, TypeError):
        raise HTTPException(422, 'Invalid brain task') from None
    validate_chat(chat)
    question = json.dumps({'task_type': body.task_key, 'messages': chat.messages}, separators=(',', ':'))
    if len(question) > 8000:
        raise HTTPException(413, 'Task is too large')
    try:
        base = origin(os.environ.get('RIG_BRAIN_URL') or 'http://rig-brain:8001')
        parsed = urlsplit(base)
        if (parsed.scheme, parsed.hostname, parsed.port) != ('http', 'rig-brain', 8001):
            raise ValueError('Invalid private origin')
    except ValueError:
        raise HTTPException(503, 'Rig is unavailable') from None
    content = json.dumps({'tenant_id': actor.tenant_id,
        'task_id': paid_identity(actor.tenant_id, actor.user_id, str(body.run_id)),
        'home_region': 'us-east', 'question': question}, separators=(',', ':')).encode()
    try:
        with actor_scope(actor):
            response = await tank._http.post(base + '/delegate', content=content,
                headers={'Content-Type': 'application/json', 'X-Tank-Key': os.environ['TANK_KEY_RIG']},
                extensions=product_extensions('rig', base), timeout=70, follow_redirects=False)
        response.raise_for_status()
        data = response.json()
        check_receipt(data, actor.tenant_id, actor.user_id, str(body.run_id), 'rig')
        return data
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise HTTPException(status if 400 <= status < 600 else 502, 'Rig is unavailable') from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
        raise HTTPException(502, 'Rig is unavailable') from None


async def sdk_completion(tank, body, request, tenant, task, region):
    from rig_brain_routing import complete
    # Preserve the reviewed room-key SDK interface for existing service/CLI
    # tasks, including their existing paid admission and receipts.
    if HEADER not in request.headers and 'task_id' not in request.query_params:
        from agent_skills import complete as legacy_complete
        return await legacy_complete(tank, body, tenant, task, region, room='rig')
    actor = await signed_user(request, tenant_id=str(tenant), product='rig', required=True,
                              nonces=PostgresNonces(tank.pool(), 'tank'))
    if request.query_params.get('task_id') != str(task):
        raise HTTPException(403, 'Rig task scope mismatch')
    # Signed worker requests additionally bind owner and task to each turn.
    with actor_scope(actor):
        return await complete(tank, body, tenant, task, region, user_id=actor.user_id)
