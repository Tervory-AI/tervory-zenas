"""Explicit storage operations; scans and ordinary file IO remain offline."""
from __future__ import annotations

import hmac
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from chassis.storage import MAX_OBJECT_BYTES, Storage, StorageError


def storage_router(agent: str) -> APIRouter:
    async def require_room(x_tank_key: str = Header(default='')) -> None:
        expected = os.environ.get('TANK_KEY', '')
        if not expected or not hmac.compare_digest(expected.encode(), x_tank_key.encode()):
            raise HTTPException(404, 'Not found')

    router = APIRouter(prefix='/storage', dependencies=[Depends(require_room)])

    @asynccontextmanager
    async def client(region: str | None = None):
        store = None
        try:
            store = Storage(agent)
            if region is not None and region != store.region:
                raise HTTPException(409, "Storage region does not match this room")
            yield store
        except StorageError as exc:
            raise HTTPException(503, str(exc)) from None
        finally:
            if store is not None:
                await store.close()

    @router.get('/usage')
    async def usage():
        async with client() as store:
            return await store.usage()

    @router.get('/objects/{category}')
    async def objects(category: str, tenant_id: uuid.UUID, public: bool = False, region: str | None = None):
        async with client(region) as store:
            return await store.objects(str(tenant_id), category, public=public)

    @router.put('/objects/{category}/{name:path}')
    async def put(category: str, name: str, tenant_id: uuid.UUID, request: Request, public: bool = False, region: str | None = None):
        # Validate credentials before reading the request or making any changes.
        async with client(region) as store:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > MAX_OBJECT_BYTES:
                    raise HTTPException(413, 'Storage object exceeds 32 MiB')
            key = await store.put(str(tenant_id), category, name, bytes(data), public=public)
            return {'bucket': store.bucket, 'key': key, 'size_bytes': len(data)}

    @router.get('/objects/{category}/{name:path}')
    async def get(category: str, name: str, tenant_id: uuid.UUID, public: bool = False, region: str | None = None):
        async with client(region) as store:
            data = await store.get(str(tenant_id), category, name, public=public)
            return Response(data, media_type='application/octet-stream')

    @router.delete('/objects/{category}/{name:path}')
    async def delete(category: str, name: str, tenant_id: uuid.UUID, public: bool = False, region: str | None = None):
        async with client(region) as store:
            await store.delete(str(tenant_id), category, name, public=public)
            return {'ok': True}

    return router
