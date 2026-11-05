"""Single-store binding, retained snapshots and the existing host's room corridor."""
from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi.responses import JSONResponse, Response, StreamingResponse
from rigbox.service import RigError
from chassis.storage import Storage

PREFIX = '/v1/rig/rooms/'
SNAPSHOT_CHUNK_BYTES = 16 * 1024 * 1024


def owner():
    value = os.environ.get('RIG_ROOM_TENANT', '')
    return str(UUID(value)) if value else None


def require_owner(tenant):
    bound = owner()
    if bound and str(UUID(str(tenant))) != bound:
        raise RigError('forbidden', 'This store has its own Rig room.', 403)


def api_base():
    bound = owner()
    return 'https://rig.tervory.com' + (PREFIX + bound if bound else '')


def room_host(tenant):
    return 'http://rig-room-' + str(UUID(str(tenant)))


async def bind_database(pool):
    """An existing volume cannot be rebound; every tenant-bearing table is constrained."""
    bound = owner()
    if not bound:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('CREATE TABLE IF NOT EXISTS agent_rig.room_owner (singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), tenant_id uuid NOT NULL)')
            await conn.execute('SELECT pg_advisory_xact_lock(724409)')
            saved = await conn.fetchval('SELECT tenant_id FROM agent_rig.room_owner')
            if saved is None:
                await conn.execute('INSERT INTO agent_rig.room_owner(tenant_id) VALUES($1)', UUID(bound))
            await conn.execute('ALTER TABLE agent_rig.room_owner ADD COLUMN IF NOT EXISTS frozen boolean NOT NULL DEFAULT false')
            saved = await conn.fetchval('SELECT tenant_id FROM agent_rig.room_owner')
            if str(saved) != bound:
                raise RuntimeError('Rig room volume belongs to another store')
            await conn.execute('''CREATE OR REPLACE FUNCTION agent_rig.room_write_guard() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN
                PERFORM pg_advisory_xact_lock(724409);
                IF (SELECT frozen FROM agent_rig.room_owner) THEN
                  RAISE EXCEPTION 'Rig room is archived';
                END IF;
                RETURN NULL;
              END $$''')
            # PostgreSQL quotes its own catalogue identifiers and the saved UUID.
            # Keep the driver query static; no identifier is interpolated into SQL
            # in Python, and no caller-supplied identifier reaches this block.
            await conn.execute("""DO $room_binding$
              DECLARE item record; room_tenant uuid; relation regclass;
              BEGIN
                SELECT tenant_id INTO STRICT room_tenant FROM agent_rig.room_owner;
                FOR item IN SELECT table_name FROM information_schema.columns
                  WHERE table_schema='agent_rig' AND column_name='tenant_id'
                LOOP
                  relation := format('%I.%I', 'agent_rig', item.table_name)::regclass;
                  IF item.table_name <> 'room_owner' AND NOT EXISTS (
                    SELECT 1 FROM pg_trigger WHERE tgrelid=relation AND tgname='rig_room_freeze'
                  ) THEN
                    EXECUTE format('CREATE TRIGGER rig_room_freeze BEFORE INSERT OR UPDATE OR DELETE ON %I.%I FOR EACH STATEMENT EXECUTE FUNCTION agent_rig.room_write_guard()', 'agent_rig', item.table_name);
                  END IF;
                  IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conrelid=relation AND conname='rig_room_owner'
                  ) THEN
                    EXECUTE format('ALTER TABLE %I.%I ADD CONSTRAINT rig_room_owner CHECK (tenant_id = %L::uuid)', 'agent_rig', item.table_name, room_tenant);
                  END IF;
                END LOOP;
              END $room_binding$""")


def quiesced():
    return owner() and (Path(os.environ.get('DATA_DIR', '/data')) / 'room-quiesced').exists()


async def archive(service, tenant):
    """Freeze before snapshot; DELETE cannot lose writes arriving after the snapshot."""
    require_owner(tenant)
    if not owner():
        raise RigError('forbidden', 'Only a store room can be archived.', 403)
    marker = Path(os.environ.get('DATA_DIR', '/data')) / 'room-quiesced'
    marker.touch()
    async with service.store.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(724409)')
            await conn.execute('UPDATE agent_rig.room_owner SET frozen=true')
    storage = RoomStorage('rig')
    snapshot_id = uuid4().hex
    snapshot = {'tenant_id': tenant, 'snapshot_id': snapshot_id, 'tables': {}}
    try:
        async with service.store.pool.acquire() as conn:
            async with conn.transaction(isolation='serializable'):
                tables = await conn.fetch("SELECT table_name FROM information_schema.columns WHERE table_schema='agent_rig' AND column_name='tenant_id' ORDER BY table_name")
                for row in tables:
                    name = row['table_name']
                    if not re.fullmatch('[a-z][a-z0-9_]*', name):
                        raise RigError('unavailable', 'Room snapshot could not be completed.', 503)
                    snapshot['tables'][name] = parts = []
                    batch, size = [], 2

                    async def flush():
                        data = b'[' + b','.join(batch) + b']'
                        key = await storage.put(tenant, 'backups', f'room-{snapshot_id}-{name}-{len(parts)}.json', data)
                        parts.append({'key': key, 'sha256': hashlib.sha256(data).hexdigest(), 'rows': len(batch)})

                    async for record in conn.cursor(f'SELECT row_to_json(t)::text AS value FROM agent_rig.{name} t WHERE tenant_id=$1', UUID(tenant), prefetch=100):
                        encoded = record['value'].encode()
                        if len(encoded) + 2 > SNAPSHOT_CHUNK_BYTES:
                            raise RigError('unavailable', 'A room record exceeds the backup part limit.', 503)
                        if batch and size + len(encoded) + 1 > SNAPSHOT_CHUNK_BYTES:
                            await flush()
                            batch, size = [], 2
                        batch.append(encoded)
                        size += len(encoded) + 1
                    if batch:
                        await flush()
        # Publish the manifest only after every immutable part is acknowledged.
        key = await storage.put(tenant, 'backups', 'room-retained.json', json.dumps(snapshot).encode())
    finally:
        await storage.close()
    return {'archived': True, 'storage_key': key, 'tenant_id': tenant}


class RoomStorage(Storage):
    def _prefix(self, tenant_id, category, *, public):
        require_owner(tenant_id)
        return super()._prefix(tenant_id, category, public=public)


class RoomGateway:
    """Explicit room paths only; old issued embeds retain their original runtime."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] == 'http' and quiesced() and path != '/health':
            return await JSONResponse({'error': {'code': 'unavailable'}}, status_code=503)(scope, receive, send)
        if scope['type'] != 'http' or not path.startswith(PREFIX):
            return await self.app(scope, receive, send)
        try:
            if owner():  # Never bounce a room request through another room.
                raise ValueError()
            tenant, tail = path[len(PREFIX):].split('/', 1)
            if str(UUID(tenant)) != tenant or not tail.startswith(('v1/rig/', 'widget/')):
                raise ValueError()
            if tail.startswith('v1/rig/rooms/'):
                raise ValueError()
        except ValueError:
            return await JSONResponse({'error': {'code': 'not_found'}}, status_code=404)(scope, receive, send)
        from starlette.requests import Request
        request = Request(scope, receive)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 12_000_000:
                return await Response(status_code=413)(scope, receive, send)
        # Hop-by-hop/forwarding headers supplied by a shopper cannot name another target.
        headers = {key: value for key, value in request.headers.items() if key.lower() not in {
            'host', 'connection', 'transfer-encoding', 'content-length', 'x-forwarded-for', 'x-forwarded-host', 'forwarded'}}
        from rigbox.quotes.limits import client_address
        headers["X-Forwarded-For"] = client_address(request)
        started = False
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
                async with http.stream(request.method, room_host(tenant) + ':8000/' + tail,
                        params=request.query_params.multi_items(), content=bytes(raw), headers=headers, timeout=90) as result:
                    response = StreamingResponse(result.aiter_bytes(), status_code=result.status_code,
                        headers={key: value for key, value in result.headers.items() if key.lower() not in {
                            'connection', 'transfer-encoding', 'content-length', 'content-encoding'}})
                    started = True
                    await response(scope, receive, send)
        except httpx.HTTPError:
            if started:
                raise
            response = JSONResponse({'error': {'code': 'unavailable', 'message': 'This store room is unavailable.'}}, status_code=503)
            await response(scope, receive, send)
