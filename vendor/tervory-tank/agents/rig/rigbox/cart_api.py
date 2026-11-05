"""Backend-only cart routes. No new page, control or visible error wording."""
import json
import secrets
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from chassis.tenant_db import activate_tenant
from rigbox.api import _admin, _auth, _ok, _service, _tenant
from rigbox.commerce import CommerceError

router = APIRouter(prefix='/v1/rig')


def cart(request):
    result = getattr(_service(request), 'cart', None)
    if result is None:
        raise CommerceError('cart_unavailable')
    return result


async def body(request, limit=32000):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise CommerceError('invalid')
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        raise CommerceError('invalid') from None


async def failure(request, exc):
    # API state codes only. Figma owns any later rendering of these states.
    response = JSONResponse({'error': {'code': str(exc)}}, status_code=409)
    from rigbox.api import _cors, _origin
    return _cors(response, _origin(request), bool(getattr(request.state, 'rig_origin_ok', False)))


@router.post('/cart/{operation}')
async def shopper_cart(operation: str, request: Request):
    await _auth(request)
    try:
        if not request.headers.get('origin'):
            raise CommerceError('origin')
        service = cart(request)
        tenant = _tenant(request)
        await service.config(tenant)
        data = await body(request)
        allowed = {'session': set(), 'view': {'session', 'product_id', 'recheck'}, 'add': {'session', 'product_id', 'quantity', 'back_order', 'line_id'},
                   'remove': {'session', 'line_id'}, 'checkout': {'session'}, 'order': {'session', 'checkout_id'}}
        if operation not in allowed or set(data) - allowed[operation]:
            raise CommerceError('invalid')
        token = data.get('session')
        if operation == 'session':
            result = {'session': secrets.token_urlsafe(32), 'mode': 'cart',
                      'store_name': (await service.store.retailer(tenant) or {}).get('name', '')}
        elif operation == 'view':
            result = await service.view(tenant, token, product_id=data.get('product_id'), recheck=data.get('recheck') is True)
        elif operation == 'add':
            result = await service.add(tenant, token, data.get('product_id'), data.get('quantity'),
                                        back_order=data.get('back_order') is True, line_id=data.get('line_id'))
        elif operation == 'remove':
            result = await service.remove(tenant, token, data.get('line_id'))
        elif operation == 'checkout':
            result = await service.checkout(tenant, token)
        else:
            result = await service.order(tenant, token, data.get('checkout_id'))
        response = _ok(request, result)
        response.headers['Cache-Control'] = 'no-store'
        return response
    except CommerceError as exc:
        return await failure(request, exc)


async def commerce_operation(service, tenant, operation, data):
    if operation == 'connect' and not data:
        config = await service.store.commerce_config(tenant)
        return {key: config[key] for key in ('platform', 'site', 'store_hash', 'delivery') if key in config}
    if operation == 'connect':
        data = {'mode': 'cart', 'region': 'US', 'vault_name': 'rig-commerce-' + str(data.get('platform', '')), **data}
        return await service.connect(tenant, data)
    if operation == 'orders' and not data:
        from rigbox.cart import public_checkout, public_line
        return {'orders': [{**public_checkout(row), 'lines': [public_line(x) for x in row['lines']]}
                           for row in await service.rows.checkouts(tenant)]}
    if operation == 'store-delivery':
        if set(data) != {'min_days', 'max_days'} or any(
                type(data[key]) is not int or not 0 <= data[key] <= 60 for key in data):
            raise CommerceError('delivery')
        if data['min_days'] > data['max_days']:
            raise CommerceError('delivery')
        async with service.rows.lock(tenant, 'connect'):
            config = await service.store.commerce_config(tenant)
            await service.store.save_commerce_config(tenant, {**config, 'delivery': dict(data)})
        return {'saved': True}
    raise CommerceError('invalid')


@router.post('/admin/commerce/{operation}')
async def admin_commerce(operation: str, request: Request):
    await _admin(request)
    try:
        service, tenant = cart(request), _tenant(request)
        data = await body(request)
        result = await commerce_operation(service, tenant, operation, data)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    except CommerceError as exc:
        return await failure(request, exc)


@router.post('/commerce/webhooks/{tenant_id}')
async def platform_webhook(tenant_id: UUID, request: Request):
    from rigbox.rooms import require_owner
    require_owner(tenant_id)
    activate_tenant(tenant_id)
    try:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 256000:
                raise CommerceError('signature')
        return JSONResponse(await cart(request).webhook(str(tenant_id), bytes(raw), request.headers),
                            headers={'Cache-Control': 'no-store'})
    except CommerceError:
        return JSONResponse({'error': {'code': 'signature'}}, status_code=400)


def mount(app):
    app.include_router(router)
