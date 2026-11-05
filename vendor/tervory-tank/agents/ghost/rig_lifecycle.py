"""Rig profile of Autumbra's isolated lifecycle; no second Coolify client."""
import os
from pathlib import Path
from uuid import UUID

from .enforce_lifecycle import CoolifyEnforceBackend, now_iso

SETTINGS = tuple('RIG_COOLIFY_' + suffix for suffix in (
    'URL', 'TOKEN', 'PROJECT_UUID', 'SERVER_UUID', 'ENVIRONMENT', 'GITHUB_APP_UUID'))
COMPOSE = '/agents/rig/docker-compose.room.yml'


def tenant(value):
    return str(UUID(str(value)))


def room_name(value):
    return 'rig-room-' + tenant(value)


def plan_create(value, *, existing_count=0, cap=None):
    owner = tenant(value)
    return {'action': 'create_room', 'tenant_id': owner, 'project': room_name(owner),
            'storage_prefix': 'us-east/' + owner + '/',
            'volume': room_name(owner) + '-data', 'database_volume': room_name(owner) + '-postgres',
            'network': {'name': room_name(owner) + '-private', 'internal': True},
            'compose': COMPOSE, 'shared_services': ['brain', 'browser', '3D'],
            'data_retained_on_destroy': True}


def plan_destroy(value):
    return {**plan_create(value), 'action': 'destroy_room'}


def receipt_for(plan, *, outcome='planned'):
    return {**plan, 'at': now_iso(), 'outcome': outcome, 'product': 'Rig',
            'api_base': 'https://rig.tervory.com/v1/rig/rooms/' + plan['tenant_id'],
            'secret_value_present': False}


class CoolifyRigBackend(CoolifyEnforceBackend):
    prefix = 'rig-room'
    create_options = {'is_raw_compose_deployment_enabled': True}
    delete_connected_networks = 'false'  # Never remove the shared Rig corridor.
    compose_location = COMPOSE
    description_prefix = 'rig-room:'
    compose_path = (Path(__file__).parents[1] / 'rig/docker-compose.room.yml'
                    if (Path(__file__).parents[1] / 'rig/docker-compose.room.yml').exists()
                    else Path('/app/ghost/docker-compose.room.yml'))
    settings_names = SETTINGS
    project_name = staticmethod(room_name)
    plan_create = staticmethod(plan_create)
    plan_destroy = staticmethod(plan_destroy)
    receipt_for = staticmethod(receipt_for)

    @staticmethod
    def settings_for(plan):
        return {'RIG_ROOM_TENANT': plan['tenant_id'], 'RIG_ROOM_NAME': plan['project'],
                'RIG_ROOM_DATA_VOLUME': plan['volume'],
                'RIG_ROOM_DB_VOLUME': plan['database_volume'],
                'RIG_ROOM_NETWORK': plan['network']['name']}


def register(app, authenticate):
    """Register on the existing one-worker, no-ingress lifecycle service only."""
    import httpx
    from fastapi import Depends, HTTPException, Request
    from .enforce_lifecycle import Refused

    @app.api_route('/rig/rooms/{action}', methods=['POST'], dependencies=[Depends(authenticate)])
    async def lifecycle(action: str, request: Request):
        if action not in {'create', 'list', 'destroy'}:
            raise HTTPException(404, 'Unknown room operation.')
        try:
            data = await request.json()
            if set(data) != {'tenant_id'}:
                raise ValueError()
            owner = tenant(data['tenant_id'])
            if not all(os.environ.get(name, '').strip() for name in SETTINGS):
                raise HTTPException(503, 'Rig Coolify settings are not configured.')
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
                backend = CoolifyRigBackend.from_env(http)
                if action == 'list':
                    return {'applications': [row for row in await backend.list() if row['name'] == room_name(owner)]}
                plan = plan_create(owner) if action == 'create' else plan_destroy(owner)
                receipt = await getattr(backend, action)(plan)
                return {'plan': plan, 'receipt': receipt}
        except (ValueError, TypeError, KeyError, Refused):
            raise HTTPException(422, 'Rig room operation refused; check Coolify before repeating.') from None
