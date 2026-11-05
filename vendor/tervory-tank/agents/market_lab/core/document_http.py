"""Authenticated room upload/delete; the original uses only this room's bucket."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Header, HTTPException, Request
from chassis.storage import Storage, StorageError, MAX_OBJECT_BYTES
from core.document_read import extract, ReadError
from core.documents import accept, forget


def build_router(*, key_ok, ledger_context):
    router = APIRouter()

    async def check(key, request):
        if not key_ok(key):
            raise HTTPException(401, 'Room key required')
        store = Storage('market_lab')
        if request.query_params.get('region', store.region) != store.region:
            await store.close()
            raise HTTPException(403, 'File must remain on its home server.')
        return store

    @router.put('/market/files/{ident}')
    async def upload(ident: uuid.UUID, tenant_id: uuid.UUID, name: str, request: Request, x_tank_key: str = Header(default='')):
        store = None
        try:
            store = await check(x_tank_key, request)
            if len(name) > 240 or '/' in name or '\\' in name or not name.lower().endswith(('.pdf', '.xlsx', '.csv')):
                raise HTTPException(422, 'Choose a PDF, XLSX or CSV file.')
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > MAX_OBJECT_BYTES:
                    raise HTTPException(413, 'File is too large.')
            try:
                parsed = await asyncio.to_thread(extract, name, bytes(data))
            except ReadError as exc:
                await store.put(str(tenant_id), 'uploads', str(ident), bytes(data))
                return {'status': 'failed', 'reason': str(exc), 'pages': None, 'rows': None}
            async with ledger_context() as ledger, ledger.conn.transaction():
                await ledger.conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', f'upload:{tenant_id}:{ident}')
                await store.put(str(tenant_id), 'uploads', str(ident), bytes(data))
                result = await accept(ledger, str(tenant_id), str(ident), name, parsed, region=store.region)
            return {key: result[key] for key in ('status', 'reason', 'pages', 'rows')}
        except StorageError:
            raise HTTPException(503, 'File storage is unavailable. Check the room storage settings.') from None
        finally:
            if store is not None:
                await store.close()

    @router.delete('/market/files/{ident}')
    async def delete(ident: uuid.UUID, tenant_id: uuid.UUID, request: Request, x_tank_key: str = Header(default='')):
        store = None
        try:
            store = await check(x_tank_key, request)
            async with ledger_context() as ledger, ledger.conn.transaction():
                await ledger.conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', f'upload:{tenant_id}:{ident}')
                await forget(ledger, str(tenant_id), str(ident))
                await store.delete(str(tenant_id), 'uploads', str(ident))
            return {'ok': True}
        except StorageError:
            raise HTTPException(503, 'File could not be removed. Please try again.') from None
        finally:
            if store is not None:
                await store.close()
    return router
