"""Bucket-local R2 storage. No ambient AWS credentials or caller-selected bucket.

R2 uses S3 Signature V4 (region auto). Credentials occur only in signed headers;
no presigned URLs, redirects, environment proxies, response bodies or SDK errors
are logged. One client belongs to one room, with tenant/region key prefixes.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx

ROOMS = frozenset({'base', 'tiger', 'market_lab', 'radix', 'tisan', 'rig', 'zenas', 'ghost'})
CATEGORIES = frozenset({'datasets', 'work', 'screenshots', 'gold', 'bench', 'training', 'backups', 'uploads'})
CAPACITY_BYTES = 1_000_000_000_000  # Display reference, not a provisioned quota per bucket.
MAX_OBJECT_BYTES = 32 * 1024 * 1024
PLATFORM_TENANT = '00000000-0000-0000-0000-000000000001'
ROOM_PROXY = 'http://egress-proxy:8080'


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
    client and Radix can dial only the baked room proxy.
    """
    def __init__(self, owner: str, *, environ: Mapping[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        env = os.environ if environ is None else environ
        if owner not in ROOMS | {'uploads'}:
            raise StorageError('Unknown storage owner')
        expected_agent = 'tank' if owner == 'uploads' else owner
        if env.get('AGENT_NAME', expected_agent).replace('-', '_') != expected_agent:
            raise StorageError('Storage credential owner does not match this process')
        self._owner = owner
        self.region = env.get('TANK_REGION', 'us-east')
        if self.region not in {'us-east', 'za'}:
            raise StorageError('Unknown storage region')
        self._bucket = 'tervory-' + owner.replace('_', '-')
        # ZA Zenas uses a region bucket. Founder creates tervory-zenas-za and sets
        # R2_BUCKET_ZENAS_ZA; US Zenas keeps tervory-zenas unchanged.
        za_bucket = env.get('R2_BUCKET_ZENAS_ZA', '')
        if owner == 'zenas' and self.region == 'za':
            if not za_bucket:
                raise StorageError('R2_BUCKET_ZENAS_ZA is required for ZA Zenas storage')
            if za_bucket != 'tervory-zenas-za':
                raise StorageError('R2_BUCKET_ZENAS_ZA must be tervory-zenas-za')
            self._bucket = za_bucket
        elif za_bucket:
            raise StorageError('R2_BUCKET_ZENAS_ZA is only valid for ZA Zenas')
        if env.get('R2_BUCKET', self.bucket) != self.bucket:
            raise StorageError('Storage bucket does not match this room')
        self._host = account_host(env)
        suffix = owner.upper()
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
        proxy = None
        if owner == 'radix':
            if env.get('RADIX_EGRESS_PROXY') != ROOM_PROXY:
                raise StorageError('Radix storage requires the room egress proxy')
            proxy = ROOM_PROXY
        # Proxy byte/time caps apply per tunnel; never reuse a Radix tunnel.
        limits = httpx.Limits(max_keepalive_connections=0) if owner == 'radix' else httpx.Limits()
        self._http = httpx.AsyncClient(transport=transport, proxy=proxy if transport is None else None, limits=limits,
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

    async def delete(self, tenant_id: str, category: str, name: str, *, public: bool = False) -> None:
        key = self._prefix(tenant_id, category, public=public) + safe_name(name)
        await self._request('DELETE', key)

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
