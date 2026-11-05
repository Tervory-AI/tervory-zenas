"""Tank storage, self-contained for the isolated tank/ Docker build context.

The S3 protocol methods mirror chassis.storage, covered by parity tests. Tank
accepts only the UPLOADS owner and never reads a room's R2 pair. Room operations
use authenticated HTTP on the existing local corridors.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import hmac
import os
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import date, datetime, timezone
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from admin import require_admin

ROOMS = frozenset({'base', 'tiger', 'market_lab', 'radix', 'tisan', 'rig', 'zenas', 'ghost'})
CATEGORIES = frozenset({'datasets', 'work', 'screenshots', 'gold', 'bench', 'training', 'backups', 'uploads'})
CAPACITY_BYTES = 1_000_000_000_000  # Display reference, not a provisioned quota per bucket.
MAX_OBJECT_BYTES = 32 * 1024 * 1024
PLATFORM_TENANT = '00000000-0000-0000-0000-000000000001'


class StorageError(RuntimeError):
    """Safe to display: never includes provider responses or credential values."""


def account_host(environ: Mapping[str, str]) -> str:
    account = environ.get('R2_ACCOUNT_ID', '')
    if re.fullmatch(r'[a-f0-9]{32}', account) is None:
        raise StorageError('R2_ACCOUNT_ID is missing or invalid')
    return f'{account}.r2.cloudflarestorage.com'


def safe_name(name: str) -> str:
    if (not isinstance(name, str) or not name or len(name) > 512
            or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', part) for part in name.split('/'))):
        raise StorageError('Invalid storage object name')
    return name


class Storage:
    """Construct before side effects. Only the owning pair is ever read.

    transport is an explicit test seam. Production always builds its own
    client. The constructor refuses every owner except uploads.
    """
    def __init__(self, owner: str, *, environ: Mapping[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        env = os.environ if environ is None else environ
        if owner != 'uploads':
            raise StorageError('Tank storage accepts only UPLOADS')
        expected_agent = 'tank' if owner == 'uploads' else owner
        if env.get('AGENT_NAME', expected_agent).replace('-', '_') != expected_agent:
            raise StorageError('Storage credential owner does not match this process')
        self._owner = owner
        self._bucket = 'tervory-' + owner.replace('_', '-')
        if env.get('R2_BUCKET', self.bucket) != self.bucket:
            raise StorageError('Storage bucket does not match this room')
        self._host = account_host(env)
        suffix = 'UPLOADS'
        self._access = env.get(f'R2_ACCESS_KEY_ID_{suffix}', '')
        self._secret = env.get(f'R2_SECRET_ACCESS_KEY_{suffix}', '')
        if not self._access or not self._secret:
            raise StorageError(f'R2 credentials for {suffix} are missing')
        own_keys = {f'R2_ACCESS_KEY_ID_{suffix}', f'R2_SECRET_ACCESS_KEY_{suffix}'}
        if any(name.startswith(('R2_ACCESS_KEY_ID_', 'R2_SECRET_ACCESS_KEY_'))
               and name not in own_keys for name in env):
            raise StorageError('Foreign R2 credentials are present in this process')
        if any(ord(c) < 33 or ord(c) > 126 for c in self._access + self._secret):
            raise StorageError('R2 credentials are invalid')
        self.region = env.get('TANK_REGION', 'us-east')
        if self.region not in {'us-east', 'za'}:
            raise StorageError('Unknown storage region')
        self._http = httpx.AsyncClient(transport=transport, proxy=None,
                                      timeout=180, trust_env=False, follow_redirects=False)

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def host(self) -> str:
        return self._host

    async def close(self) -> None:
        await self._http.aclose()

    def _prefix(self, tenant_id: str, category: str, *, public: bool) -> str:
        try:
            tenant = str(uuid.UUID(str(tenant_id)))
        except ValueError:
            raise StorageError('A valid tenant_id is required') from None
        if category not in CATEGORIES:
            raise StorageError('Unknown storage category')
        # Public law/datasets may leave ZA, client data may not. No config bypass.
        if self.region == 'za' and not (public and tenant == PLATFORM_TENANT):
            raise StorageError('ZA client data must remain on the ZA box')
        return f'{self.region}/{tenant}/{category}/'

    def _sign(self, method: str, path: str, query: str, data: bytes) -> dict[str, str]:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        digest = hashlib.sha256(data).hexdigest()
        headers = {'host': self.host, 'x-amz-content-sha256': digest, 'x-amz-date': stamp}
        names = ';'.join(sorted(headers))
        canonical_headers = ''.join(f'{key}:{headers[key]}\n' for key in sorted(headers))
        canonical = '\n'.join((method, path, query, canonical_headers, names, digest))
        scope = f'{stamp[:8]}/auto/s3/aws4_request'
        message = f'AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}'
        key = ('AWS4' + self._secret).encode()
        for part in (stamp[:8], 'auto', 's3', 'aws4_request'):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
        headers['authorization'] = f'AWS4-HMAC-SHA256 Credential={self._access}/{scope}, SignedHeaders={names}, Signature={signature}'
        return headers

    async def _request(self, method: str, key: str = '', *, params: dict[str, str] | None = None,
                       data: bytes = b'') -> bytes:
        path = '/' + self.bucket + ('/' + quote(key, safe='/~') if key else '')
        query = '&'.join(f'{quote(k, safe="~")}={quote(v, safe="~")}' for k, v in sorted((params or {}).items()))
        url = f'https://{self.host}{path}' + ('?' + query if query else '')
        headers = self._sign(method, path, query, data)
        try:
            async with self._http.stream(method, url, headers=headers, content=data) as response:
                if response.status_code not in {200, 204}:
                    raise StorageError('R2 request failed; check bucket access and service availability')
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    result.extend(chunk)
                    if len(result) > MAX_OBJECT_BYTES:
                        raise StorageError('R2 response exceeds the storage object limit')
                return bytes(result)
        except httpx.HTTPError:
            raise StorageError('R2 is unreachable; no successful operation was confirmed') from None

    async def put(self, tenant_id: str, category: str, name: str, data: bytes, *, public: bool = False) -> str:
        key = self._prefix(tenant_id, category, public=public) + safe_name(name)
        if not isinstance(data, bytes) or len(data) > MAX_OBJECT_BYTES:
            raise StorageError('Storage object exceeds 32 MiB; split it into parts')
        await self._request('PUT', key, data=data)
        return key

    async def get(self, tenant_id: str, category: str, name: str, *, public: bool = False) -> bytes:
        key = self._prefix(tenant_id, category, public=public) + safe_name(name)
        return await self._request('GET', key)

    async def _objects(self, prefix: str) -> AsyncIterator[dict]:
        token = ''
        seen = set()
        while True:
            params = {'list-type': '2', 'prefix': prefix, 'max-keys': '1000'}
            if token:
                params['continuation-token'] = token
            payload = await self._request('GET', params=params)
            try:
                if b'<!DOCTYPE' in payload or b'<!ENTITY' in payload:
                    raise ValueError('DTD forbidden')
                root = ET.fromstring(payload)
                if root.tag.rsplit('}', 1)[-1] != 'ListBucketResult':
                    raise ValueError('wrong listing')
                for item in root.findall('{*}Contents'):
                    key = item.findtext('{*}Key')
                    size = int(item.findtext('{*}Size', '-1'))
                    if not key or not key.startswith(prefix) or size < 0:
                        raise ValueError('invalid object')
                    yield {'key': key, 'size_bytes': size}
                truncated = root.findtext('{*}IsTruncated')
                if truncated == 'false':
                    return
                token = root.findtext('{*}NextContinuationToken', '')
                if truncated != 'true' or not token or token in seen:
                    raise ValueError('invalid pagination')
                seen.add(token)
            except (ET.ParseError, ValueError):
                raise StorageError('R2 returned an invalid object listing') from None

    async def objects(self, tenant_id: str, category: str, *, public: bool = False) -> list[dict]:
        prefix = self._prefix(tenant_id, category, public=public)
        return [item async for item in self._objects(prefix)]

    async def usage(self) -> dict:
        """Only call from a room-authenticated/founder route; counts, no filenames."""
        total = count = 0
        async for item in self._objects(''):
            total += item['size_bytes']
            count += 1
        return {'bucket': self.bucket, 'used_bytes': total, 'objects': count,
                'reference_capacity_bytes': CAPACITY_BYTES,
                'measured_at': datetime.now(timezone.utc).isoformat()}


router = APIRouter(prefix='/v1/admin/storage', dependencies=[Depends(require_admin)])


def uploads(**kwargs) -> Storage:
    return Storage('uploads', **kwargs)




def room_key(room: str) -> str:
    if room not in ROOMS:
        raise StorageError('Unknown storage room')
    value = os.environ.get(f'TANK_KEY_{room.upper()}', '')
    if not value:
        raise StorageError('Room storage is not configured')
    return value


async def room_usage(http: httpx.AsyncClient, room: str) -> dict:
    bucket = 'tervory-' + room.replace('_', '-')
    try:
        key = room_key(room)
        response = await http.get(f'http://agent-{room.replace("_", "-")}:8000/storage/usage',
                                  headers={'X-Tank-Key': key})
        response.raise_for_status()
        measured = response.json()
        if (not isinstance(measured, dict) or measured.get('bucket') != bucket
                or type(measured.get('used_bytes')) is not int or measured['used_bytes'] < 0
                or type(measured.get('objects')) is not int or measured['objects'] < 0
                or not isinstance(measured.get('measured_at'), str)):
            raise ValueError('Invalid measurement')
        return {k: measured[k] for k in ('bucket', 'used_bytes', 'objects', 'measured_at')}
    except (StorageError, httpx.HTTPError, ValueError):
        return {'bucket': bucket, 'error': 'Room storage unavailable'}


@router.get('/usage')
async def usage():
    """Founder-only account measurements. Missing buckets never become zeroes."""
    async def uploads_usage():
        store = None
        try:
            store = uploads()
            return await store.usage()
        except StorageError:
            return {'bucket': 'tervory-uploads', 'error': 'Storage unavailable'}
        finally:
            if store is not None:
                await store.close()

    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as http:
        buckets = await asyncio.gather(uploads_usage(), *(room_usage(http, room) for room in sorted(ROOMS)))
    result = {'buckets': buckets, 'reference_capacity_bytes': CAPACITY_BYTES,
              'complete': not any('error' in row for row in buckets)}
    if all('error' in row for row in buckets):
        return JSONResponse(status_code=503, content=result)
    return result


async def archive_training_snapshot(agent: str, day: str, data: bytes, *, region: str) -> None:
    """Archive the exact HF payload in its owner's bucket before any HF call.

    Each run writes parts followed by a completion manifest, then reads that
    marker back. A failed or uncertain operation prevents the HF export. The HF
    bearer-token client is never reused for room or R2 traffic.
    """
    if agent not in ROOMS | {'tank'}:
        raise StorageError('Unknown snapshot owner')
    if region not in {'us-east', 'za'} or region != os.environ.get('TANK_REGION', 'us-east'):
        raise StorageError('Snapshot must stay in its own region')
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError('noncanonical date')
    except ValueError:
        raise StorageError('Invalid snapshot date') from None
    name = f'{day}/{uuid.uuid4().hex}'
    parts = []

    async def archive(put, get):
        for offset in range(0, len(data), MAX_OBJECT_BYTES):
            chunk = data[offset:offset + MAX_OBJECT_BYTES]
            part_name = f'{name}/part-{len(parts):06d}'
            await put(part_name, chunk)
            parts.append({'name': part_name, 'size_bytes': len(chunk), 'sha256': hashlib.sha256(chunk).hexdigest()})
        manifest = json.dumps({'version': 1, 'parts': parts, 'size_bytes': len(data),
                               'sha256': hashlib.sha256(data).hexdigest()}, sort_keys=True).encode()
        manifest_name = f'{name}/manifest.json'
        await put(manifest_name, manifest)
        if await get(manifest_name) != manifest:
            raise StorageError('Snapshot completion could not be verified')

    if agent == 'tank':
        store = uploads()
        try:
            async def put(name, content):
                await store.put(PLATFORM_TENANT, 'training', name, content, public=True)
            async def get(name):
                return await store.get(PLATFORM_TENANT, 'training', name, public=True)
            await archive(put, get)
        finally:
            await store.close()
        return

    key = room_key(agent)
    base = f'http://agent-{agent.replace("_", "-")}:8000/storage/objects/training/'
    # Region is cross-checked by the room before any write, even for public rows.
    params = {'tenant_id': PLATFORM_TENANT, 'public': 'true', 'region': region}
    async with httpx.AsyncClient(timeout=180, trust_env=False, follow_redirects=False,
                                 headers={'X-Tank-Key': key}) as http:
        async def put(name, content):
            response = await http.put(base + name, params=params, content=content)
            if response.status_code != 200:
                raise StorageError('Room snapshot archive failed')
        async def get(name):
            response = await http.get(base + name, params=params)
            if response.status_code != 200:
                raise StorageError('Room snapshot verification failed')
            return response.content
        try:
            await archive(put, get)
        except httpx.HTTPError:
            raise StorageError('Room snapshot archive is unreachable') from None
