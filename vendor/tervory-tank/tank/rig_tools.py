"""Command/MCP adapters reuse Rig's authenticated product actions and receipts."""
import json
from fastapi import APIRouter, Depends, HTTPException, Request

from command import Actor, identity
from command_tools.rig import MAIN, ADMIN


def build_router(tank):
    router = APIRouter()

    @router.api_route('/v1/rig/tools/{operation}', methods=['GET', 'POST'], dependencies=[Depends(tank.require_key)])
    async def call(operation: str, request: Request, actor: Actor = Depends(identity)):
        import command
        tool = 'rig.' + operation
        if tool not in MAIN:
            raise HTTPException(404, 'This action is not available.')
        command.require_tool(actor, tool)
        if tool in ADMIN and not (actor.founder or actor.tenant_admin):
            raise HTTPException(403, 'Administrator authorization is required.')
        adapter = command.ADAPTERS[tool]
        if request.method != adapter.method:
            raise HTTPException(405)
        raw = dict(request.query_params) if request.method == 'GET' else await tank._rig_body(request)
        action = command.validate_action(command.PlannedAction(tool=tool, arguments=raw), actor)
        payload = {'tenant_id': str(actor.tenant_id), 'actor': actor.user_id,
                   'administrator': actor.founder or actor.tenant_admin, 'arguments': action.arguments}
        response = await tank._rig_call('POST', '/v1/rig/internal/tools/' + operation,
            [], json.dumps(payload).encode(), timeout=35)
        result = tank._rig_response(response, actor.tenant_id)
        result.headers['Cache-Control'] = 'private, no-store'
        return result

    return router
