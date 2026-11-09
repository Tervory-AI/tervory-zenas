"""Epoch-aware signaling boundary for self-hosted LiveKit.

LiveKit OSS does not revoke an existing JWT when a participant is removed. All
public signaling MUST terminate here; the SFU HTTP port stays internal/loopback.
RTC packets still go directly to the regional SFU over configured media ports.
"""
import asyncio
import ssl
from urllib.parse import urlsplit
from contextlib import suppress
import aiohttp
import jwt
from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from .contracts import Scope, MediaError


class SignalingGate:
    def __init__(self, bridge, rooms, journal, region):
        self.bridge, self.rooms, self.journal, self.region = bridge, rooms, journal, region

    async def authorize(self, token):
        try:
            claims = jwt.decode(token, self.rooms.secret, algorithms=['HS256'], issuer=self.rooms.key)
            room = claims.get('video', {}).get('room', '')
            product, region, tenant, hearing = room.split(':')
            scope = Scope(tenant, region, hearing)
            if product != 'moot' or region != self.region or claims.get('video', {}).get('roomJoin') is not True:
                raise ValueError()
            lease = next((row for row in self.journal.leases() if row[0] == scope and row[4] == claims['sub']), None)
            if lease is None:
                raise ValueError()
            await self.current(lease)
            return lease
        except Exception:
            raise MediaError('seat_revoked') from None

    async def current(self, lease):
        scope, user, seat, epoch, identity = lease
        state = await self.bridge.projection(scope, user, seat)
        controller = state.get('seats', {}).get(seat, {})
        if seat not in state.get('my_seats', []) or controller.get('kind') != 'human' or controller.get('epoch') != epoch:
            raise MediaError('seat_revoked')

    def upstream_url(self, path):
        endpoint = urlsplit(self.rooms.url)
        if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path not in ('', '/') or path not in ('/rtc', '/rtc/v1')):
            raise MediaError('secure_signaling_required')
        context = self.rooms.tls_context
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise MediaError('tls_verification_required')
        # #784 terminates verified WSS at livekit-tls:8443. There is no
        # plaintext signaling fallback, including for a misconfigured caller.
        return f'wss://{endpoint.netloc}{path}'

    def install(self, app):
        @app.get('/rtc/v1/validate')
        @app.get('/rtc/validate')
        async def validate(request: Request):
            try:
                tokens = request.query_params.getlist('access_token')
                if len(tokens) != 1:
                    raise MediaError('seat_revoked')
                await self.authorize(tokens[0])
            except MediaError:
                return JSONResponse({'error': 'seat_revoked'}, status_code=403)
            return JSONResponse({})

        @app.websocket('/rtc/v1')
        @app.websocket('/rtc')
        async def signaling(websocket: WebSocket):
            try:
                tokens = websocket.query_params.getlist('access_token')
                if len(tokens) != 1:
                    raise MediaError('seat_revoked')
                lease = await self.authorize(tokens[0])
            except MediaError:
                await websocket.close(code=4403)
                return
            tasks = []
            try:
                endpoint = self.upstream_url(websocket.url.path)
                # Only this fixed internal SFU URL is reachable. No client target,
                # redirect, arbitrary WebSocket service or browser cookie is forwarded.
                async with aiohttp.ClientSession(trust_env=False,
                    connector=aiohttp.TCPConnector(ssl=self.rooms.tls_context)) as client:
                    async with client.ws_connect(endpoint,
                        params=list(websocket.query_params.multi_items()), max_msg_size=1024*1024,
                        timeout=aiohttp.ClientWSTimeout(ws_close=2)) as upstream:
                        await websocket.accept()
                        async def from_browser():
                            while True:
                                message = await websocket.receive()
                                if message['type'] == 'websocket.disconnect':
                                    return
                                if message.get('bytes') is not None:
                                    if len(message['bytes']) > 1024*1024:
                                        raise MediaError('signaling_limit')
                                    await upstream.send_bytes(message['bytes'])
                                elif message.get('text') is not None:
                                    if len(message['text']) > 1024*1024:
                                        raise MediaError('signaling_limit')
                                    await upstream.send_str(message['text'])
                        async def from_server():
                            async for message in upstream:
                                if message.type == aiohttp.WSMsgType.BINARY:
                                    await websocket.send_bytes(message.data)
                                elif message.type == aiohttp.WSMsgType.TEXT:
                                    await websocket.send_text(message.data)
                                elif message.type in {aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE}:
                                    return
                        async def revalidate():
                            while True:
                                await asyncio.sleep(0.25)
                                await self.current(lease)
                        tasks = [asyncio.create_task(call()) for call in (from_browser, from_server, revalidate)]
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
            except (Exception, WebSocketDisconnect):
                pass  # No token-bearing exception messages, URLs or frames.
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with suppress(asyncio.CancelledError, Exception):
                        await task
                with suppress(Exception):
                    await self.rooms.revoke(lease[0], lease[4])
                with suppress(Exception):
                    await websocket.close(code=4403)
