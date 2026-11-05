"""MCP 2025-11-25 client over Tank's pinned public HTTPS corridor.

No SDK-owned HTTP session: initialization, discovery and calls all reuse
access.public_stream, including SSE, so no alternate DNS/proxy/redirect path
can escape its checks. Server messages are untrusted data, never dispatched.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

VERSION = '2025-11-25'
SUPPORTED_VERSIONS = frozenset({VERSION, '2025-06-18', '2025-03-26'})
LIMIT = 2_000_000
TOOL_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


class Refused(ValueError):
    """Deliberately contains no remote body, headers, arguments or credentials."""


class ResponseTooLarge(Refused):
    pass


class SSEDecoder:
    """Consume each byte once, retaining only the current line/event tail."""

    def __init__(self):
        self.line = bytearray()
        self.data: list[bytes] = []
        self.after_cr = False

    def feed(self, chunk: bytes):
        for byte in chunk:
            if self.after_cr:
                self.after_cr = False
                if byte == 10:
                    continue
            if byte not in {10, 13}:
                self.line.append(byte)
                continue
            self.after_cr = byte == 13
            line = bytes(self.line)
            self.line.clear()
            if not line:
                if self.data:
                    yield b'\n'.join(self.data)
                    self.data.clear()
            elif line.startswith(b'data:'):
                value = line[5:]
                self.data.append(value[1:] if value.startswith(b' ') else value)


class SignInRequired(Refused):
    def __init__(self, challenge: str = ''):
        super().__init__('MCP sign-in required')
        self.challenge = challenge[:8000]


async def request(url: str, *, method='GET', headers=None, content=None) -> tuple[int, bytes, dict]:
    from access import public_stream
    try:
        async with asyncio.timeout(25):
            async with public_stream(url, method=method, headers=headers, content=content) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > LIMIT:
                        raise Refused('MCP response too large')
                return response.status_code, bytes(data), dict(response.headers)
    except (SignInRequired, Refused):
        raise
    except Exception:
        raise Refused('MCP request refused') from None


def document(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise Refused('MCP response invalid') from None


class Client:
    def __init__(self, url: str, approved: Callable[[], Awaitable[None]], token: str | None = None):
        self.url, self.approved, self.token = url, approved, token
        self.session: str | None = None
        self.version = VERSION
        self.received_bytes = 0

    async def rpc(self, method: str, params: dict | None = None, *, notification=False) -> dict:
        from access import public_stream
        await self.approved()  # Recheck revocation before every outside MCP request.
        ident = str(uuid.uuid4())
        message: dict[str, Any] = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        if not notification:
            message['id'] = ident
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        if method != 'initialize':
            headers['MCP-Protocol-Version'] = self.version
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        try:
            async with asyncio.timeout(25):
                async with public_stream(self.url, method='POST', headers=headers,
                                         content=json.dumps(message).encode()) as response:
                    if response.status_code == 401:
                        raise SignInRequired(response.headers.get('www-authenticate', ''))
                    if notification:
                        if response.status_code != 202:
                            raise Refused('MCP notification refused')
                        return {}
                    if response.status_code != 200:
                        raise Refused('MCP request refused')
                    session = response.headers.get('mcp-session-id')
                    if session is not None:
                        if not re.fullmatch(r'[\x21-\x7e]{1,1024}', session):
                            raise Refused('MCP session invalid')
                        self.session = session
                    media = response.headers.get('content-type', '').split(';')[0].strip().lower()
                    if media not in {'application/json', 'text/event-stream'}:
                        raise Refused('MCP content type refused')
                    raw = bytearray()
                    decoder = SSEDecoder()
                    response_bytes = 0
                    async for chunk in response.aiter_bytes():
                        response_bytes += len(chunk)
                        self.received_bytes += len(chunk)
                        if response_bytes > LIMIT or self.received_bytes > LIMIT:
                            raise ResponseTooLarge('MCP response too large')
                        if media == 'text/event-stream':
                            for event in decoder.feed(chunk):
                                if not event.strip():
                                    continue
                                value = document(event)
                                if value.get('id') == ident:
                                    return self.result(value, ident)
                            # Requests/notifications from the server are intentionally not executed.
                        else:
                            raw.extend(chunk)
                    if media == 'application/json':
                        return self.result(document(bytes(raw)), ident)
                    raise Refused('MCP response incomplete')
        except (SignInRequired, Refused):
            raise
        except Exception:
            raise Refused('MCP request refused') from None

    @staticmethod
    def result(value: dict, ident: str) -> dict:
        if value.get('jsonrpc') != '2.0' or value.get('id') != ident or 'error' in value or not isinstance(value.get('result'), dict):
            raise Refused('MCP response invalid')
        return value['result']

    async def initialize(self):
        result = await self.rpc('initialize', {'protocolVersion': VERSION, 'capabilities': {},
                                             'clientInfo': {'name': 'Tervory Tank', 'version': '1.0.0'}})
        if result.get('protocolVersion') not in SUPPORTED_VERSIONS:
            raise Refused('MCP protocol refused')
        self.version = result['protocolVersion']
        await self.rpc('notifications/initialized', notification=True)

    async def tools(self, allowed: dict[str, str]) -> list[dict]:
        tools: dict[str, dict] = {}
        cursor = None
        seen = set()
        for _ in range(100):
            result = await self.rpc('tools/list', {'cursor': cursor} if cursor else {})
            if not isinstance(result.get('tools'), list):
                raise Refused('MCP tools invalid')
            for tool in result['tools']:
                if not isinstance(tool, dict):
                    raise Refused('MCP tools invalid')
                name = tool.get('name')
                if not isinstance(name, str) or not TOOL_NAME.fullmatch(name) or name not in allowed:
                    continue
                schema = tool.get('inputSchema')
                if not isinstance(schema, dict) or len(json.dumps(schema).encode()) > 20_000:
                    continue
                import jsonschema
                try:
                    jsonschema.Draft202012Validator.check_schema(schema)
                except jsonschema.exceptions.SchemaError:
                    continue
                # Remote references could create a second network path in validation.
                if any(key in json.dumps(schema) for key in ('"$ref"', '"$dynamicRef"')):
                    continue
                description = tool.get('description', '')
                tools[name] = {'name': name, 'description': description[:1000] if isinstance(description, str) else '',
                               'inputSchema': schema, 'access': allowed[name]}
                if len(tools) > 1000:
                    raise Refused('MCP tools too large')
            cursor = result.get('nextCursor')
            if not cursor:
                return list(tools.values())
            if not isinstance(cursor, str) or len(cursor) > 2000 or cursor in seen:
                raise Refused('MCP cursor invalid')
            seen.add(cursor)
        raise Refused('MCP tools too large')

    async def call(self, name: str, arguments: dict) -> dict:
        return await self.rpc('tools/call', {'name': name, 'arguments': arguments})
