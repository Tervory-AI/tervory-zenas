"""Rig-authenticated review and shopper routes. Mount on the Rig host."""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from rigbox.api import _admin, _auth, _ok as rig_ok, _tenant, _service
from .video_engine import Engine, Repository, MAX_BYTES
from .video_media import download
from .video_models import ModelsUnavailable

router = APIRouter(prefix='/v1/rig/video-shop')


def _ok(request, data):
    response = rig_ok(request, data)
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def engine(request):
    if not hasattr(request.app.state, 'video_shop'):
        request.app.state.video_shop = Engine(_service(request), Repository(request.app.state.video_shop_root))
    return request.app.state.video_shop


async def body(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BYTES * 2:
            raise HTTPException(413, 'media_limit')
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise HTTPException(422, 'invalid') from None


@router.post('/admin/videos')
async def create(request: Request):
    await _admin(request)
    data = await body(request)
    try:
        if data.get('rights_confirmed') is not True:
            raise ValueError('permission_required')
        if bool(data.get('video_base64')) == bool(data.get('url')):
            raise ValueError('one_source')
        video = (await asyncio.to_thread(download, data['url']) if data.get('url')
                 else base64.b64decode(data['video_base64'], validate=True))
        if not isinstance(data.get('references', {}), dict):
            raise ValueError('references')
        references = {sku: base64.b64decode(value, validate=True)
                      for sku, value in data.get('references', {}).items()}
        result = await asyncio.wait_for(
            engine(request).ingest(_tenant(request), video, data.get('transcript', []), references,
                                   permission={'confirmed': True, 'at': datetime.now(timezone.utc).isoformat()}), 1800)
        return _ok(request, result)
    except ModelsUnavailable:
        raise HTTPException(503, 'video_processing_unavailable') from None
    except (ValueError, KeyError, TypeError, IndexError, OSError, TimeoutError):
        raise HTTPException(422, 'invalid_media') from None


@router.get('/admin/videos/{identifier}')
async def draft(identifier: str, request: Request):
    await _admin(request)
    try:
        return _ok(request, engine(request).repository.get(_tenant(request), identifier))
    except KeyError:
        raise HTTPException(404, 'video') from None


@router.put('/admin/videos/{identifier}')
async def review(identifier: str, request: Request):
    await _admin(request)
    data = await body(request)
    try:
        if type(data['revision']) is not int or not 1 <= data['revision'] <= 2147483647:
            raise ValueError('revision')
        return _ok(request, await engine(request).review(_tenant(request), identifier,
                                                       data['revision'], data['moments']))
    except KeyError:
        raise HTTPException(404, 'video_or_field') from None
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from None


def mount(app, root):
    app.state.video_shop_root = str(root)
    app.include_router(router)


@router.get('/videos/{identifier}')
async def timeline(identifier: str, request: Request):
    from rigbox.logic import public_product
    from rigbox.shopper_attributes import shopper_row
    await _auth(request)
    try:
        saved = engine(request).repository.get(_tenant(request), identifier)
    except KeyError:
        raise HTTPException(404, 'video') from None
    if saved['status'] != 'reviewed':
        raise HTTPException(404, 'video')
    products = {str(p.get('sku') or p.get('id')): p for p in
                await _service(request).store.products(_tenant(request)) if p.get('active', True)}
    moments = [m for m in saved['moments'] if m['approved'] and m['product_id'] in products]
    return _ok(request, {'id': identifier, 'duration': saved.get('duration'), 'revision': saved['revision'], 'moments': moments,
                        'products': {m['product_id']: public_product(shopper_row(products[m['product_id']]))
                                     for m in moments}})


@router.get('/videos/{identifier}/media')
async def media(identifier: str, request: Request):
    from fastapi.responses import FileResponse
    await timeline(identifier, request)
    # Derive the file name from the saved UUID, never a caller-controlled path.
    saved = engine(request).repository.get(_tenant(request), identifier)
    path = engine(request).repository.directory(_tenant(request)) / (saved['id'] + '.mp4')
    headers = dict(_ok(request, {}).headers)
    headers.pop('content-length', None)
    headers.pop('content-type', None)
    headers.update({'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    return FileResponse(path, media_type=saved.get('media_type', 'video/mp4'), headers=headers)


@router.get('/widget.js')
async def widget():
    from fastapi.responses import FileResponse
    return FileResponse(Path(__file__).with_name('widget.js'), media_type='text/javascript',
                        headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


@router.get('/admin/videos')
async def videos(request: Request):
    await _admin(request)
    return _ok(request, {'videos': engine(request).repository.list(_tenant(request))})


@router.get('/admin/videos/{identifier}/media')
async def admin_media(identifier: str, request: Request):
    from fastapi.responses import FileResponse
    await _admin(request)
    try:
        saved = engine(request).repository.get(_tenant(request), identifier)
    except KeyError:
        raise HTTPException(404, 'video') from None
    path = engine(request).repository.directory(_tenant(request)) / (saved['id'] + '.mp4')
    return FileResponse(path, media_type=saved.get('media_type', 'video/mp4'),
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
