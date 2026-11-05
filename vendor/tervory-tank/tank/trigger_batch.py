"""Resolve a bounded batch from real owned attachment rows or existing Rig GETs."""
import csv
import io
from urllib.parse import quote

import httpx
from fastapi import HTTPException


def csv_products(text: str) -> list[dict]:
    try:
        reader = csv.DictReader(io.StringIO(text), strict=True)
        fields = reader.fieldnames or []
        if 'name' not in fields or len(set(fields)) != len(fields) or any(not f for f in fields):
            raise ValueError
        products = []
        for row in reader:
            if None in row or any(v is None for v in row.values()) or not row['name'].strip():
                raise ValueError
            products.append(dict(row))
            if len(products) > 25:
                raise ValueError
        if not products:
            raise ValueError
        return products
    except (ValueError, csv.Error):
        raise HTTPException(422, 'Invalid Trigger request') from None


async def products(actor, args: dict, state: dict) -> list[dict]:
    if args.get('attachment'):
        last = next((m for m in reversed(state['messages']) if m['role'] == 'user'), {})
        matches = [a for a in last.get('attachments', []) if a.get('name') == args['attachment'] and isinstance(a.get('text'), str)]
        if len(matches) != 1:
            raise HTTPException(422, 'Invalid Trigger request')
        return csv_products(matches[0]['text'])
    from command import db
    from main import app, TANK_API_KEY
    async with db().acquire() as conn:
        enabled = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM tank.tenant_squad WHERE tenant_id=$1 AND agent='rig')", actor.tenant_id)
    if not enabled:
        raise HTTPException(403, 'This tool is not available to this user')
    result = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://tank.internal') as client:
        for sku in args['skus']:
            response = await client.get('/v1/rig/products/' + quote(sku, safe=''),
                params={'tenant_id': str(actor.tenant_id)}, headers={'X-Tank-Key': TANK_API_KEY})
            if response.status_code != 200:
                raise HTTPException(response.status_code, 'Writing record unavailable')
            row = response.json()
            if row.get('id') != sku or not row.get('name'):
                raise HTTPException(422, 'Invalid Trigger response')
            # Drop graph/fit/recommendation fields that can describe other products.
            result.append({k: row[k] for k in ('id', 'name', 'brand', 'category', 'price', 'attributes') if k in row})
    return result


def item_body(product: dict) -> dict:
    return {'text': 'Write a product description using only this product record.',
            'format': 'product description', 'product_facts': product}
