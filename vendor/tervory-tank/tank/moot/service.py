"""Internal Moot media API. Only a trusted server may forward actor identity.

Run separately from Zenas. Browser access must use the authenticated Workspace/
Tank boundary; this application never trusts browser-supplied organization roles.
"""
import asyncio
import hmac
from contextlib import asynccontextmanager, suppress
from uuid import UUID
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from .contracts import Scope, MediaError


class GrantInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seat: str


def create_app(*, bridge, rooms, journal, internal_key, region, mux=None, livekit_events=None):
    leases = {(s.room, identity): (s, user, seat, epoch, identity)
        for s, user, seat, epoch, identity in journal.leases()}

    async def check_leases():
        while True:
            await asyncio.sleep(0.5)
            for key, lease in list(leases.items()):
                scope, user, seat, epoch, identity = lease
                try:
                    state = await bridge.projection(scope, user, seat)
                    if seat not in state.get('my_seats', []) or state['seats'][seat]['epoch'] != epoch:
                        raise MediaError('seat_revoked')
                except Exception:
                    try:
                        await rooms.revoke(scope, identity)
                    except Exception:
                        continue  # retry revocation; never issue a replacement grant.
                    journal.drop_lease(scope, identity)
                    leases.pop(key, None)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(check_leases())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(lifespan=lifespan)

    def actor(tenant, user, key):
        if not internal_key or not hmac.compare_digest(key.encode(), internal_key.encode()):
            raise HTTPException(401, 'internal_auth_required')
        try:
            UUID(tenant)
            if not user or len(user) > 200:
                raise ValueError()
        except ValueError:
            raise HTTPException(401, 'actor_required') from None
        return tenant, user

    @app.get('/healthz')
    async def health():
        return {'product': 'moot', 'region': region, 'speech': 'unavailable'}

    @app.post('/internal/hearings/{hearing}/grants')
    async def grant(hearing: UUID, body: GrantInput, x_moot_key: str = Header(default=''),
        x_moot_tenant: str = Header(default=''), x_moot_actor: str = Header(default='')):
        tenant, user = actor(x_moot_tenant, x_moot_actor, x_moot_key)
        scope = Scope(tenant, region, str(hearing))
        try:
            state = await bridge.projection(scope, user, body.seat)
            result = rooms.grant(scope, user, body.seat, state)
            # Explicit room creation keeps LiveKit auto_create disabled.
            await rooms.create(scope)
            fresh = await bridge.projection(scope, user, body.seat)
            if fresh['seats'][body.seat]['epoch'] != result['epoch']:
                raise MediaError('seat_revoked')
            journal.save_lease(scope, user, body.seat, result['epoch'], result['identity'])
            leases[(scope.room, result['identity'])] = (scope, user, body.seat, result['epoch'], result['identity'])
            return result
        except MediaError as exc:
            raise HTTPException(403, str(exc)) from None
        except Exception:
            raise HTTPException(503, 'media_unavailable') from None

    @app.get('/internal/hearings/{hearing}/resume')
    async def resume(hearing: UUID, seat: str, after: int = 0, x_moot_key: str = Header(default=''),
        x_moot_tenant: str = Header(default=''), x_moot_actor: str = Header(default='')):
        tenant, user = actor(x_moot_tenant, x_moot_actor, x_moot_key)
        scope = Scope(tenant, region, str(hearing))
        if after < 0:
            raise HTTPException(422, 'invalid_cursor')
        try:
            projection = await bridge.projection(scope, user, seat)
            # The role-specific legacy projection remains authoritative. Never read
            # its raw SQL state or expose a judge projection to another seat.
            return {'hearing': projection, 'events': journal.events(scope, after), 'speech': 'unavailable'}
        except MediaError:
            raise HTTPException(403, 'not_found') from None

    @app.post('/webhooks/mux')
    async def mux_webhook(request: Request,
                          mux_signature: str = Header(default='')):
        if mux is None or not mux.webhook_secret:
            raise HTTPException(503, 'mux_webhook_unavailable')
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1024 * 1024:
                raise HTTPException(413, 'webhook_too_large')
            chunks.append(chunk)
        raw = b''.join(chunks)
        try:
            accepted = mux.webhook(raw, mux_signature)
            return {'accepted': accepted}
        except MediaError:
            raise HTTPException(400, 'invalid_webhook') from None

    @app.post('/webhooks/livekit')
    async def livekit_webhook(request: Request, authorization: str = Header(default='')):
        if livekit_events is None:
            raise HTTPException(503, 'livekit_events_unavailable')
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1024 * 1024:
                raise HTTPException(413, 'webhook_too_large')
            chunks.append(chunk)
        try:
            return {'accepted': bool(livekit_events.receive(b''.join(chunks), authorization))}
        except MediaError:
            raise HTTPException(400, 'invalid_webhook') from None

    @app.websocket('/events')
    async def events(websocket: WebSocket):
        await websocket.accept()
        try:
            import jwt
            import time
            first = await asyncio.wait_for(websocket.receive_json(), timeout=3)
            claims = jwt.decode(first.get('token', ''), rooms.secret, algorithms=['HS256'], issuer=rooms.key)
            room = claims.get('video', {}).get('room', '')
            _, home, tenant, hearing = room.split(':')
            scope = Scope(tenant, home, hearing)
            if home != region:
                raise MediaError('wrong_region')
            lease = next((lease for lease in journal.leases()
                if lease[0] == scope and lease[4] == claims['sub']), None)
            if lease is None:
                raise MediaError('seat_revoked')
            _, user, seat, epoch, _ = lease
            after = first.get('after', 0)
            if type(after) is not int or after < 0:
                raise MediaError('invalid_cursor')
            while time.time() < claims['exp']:
                state = await bridge.projection(scope, user, seat)
                if seat not in state.get('my_seats', []) or state['seats'][seat]['epoch'] != epoch:
                    raise MediaError('seat_revoked')
                rows = journal.events(scope, after)
                for event in rows:
                    after = event['seq']
                    if event.get('kind') == 'caption':
                        # Positive list only; no actor identity, receipt, source,
                        # internal speech prompt or private legal context.
                        await websocket.send_json({key: event[key] for key in
                            ('seq', 'seat', 'text', 'final') if key in event})
                    else:
                        await websocket.send_json({'seq': event['seq'], 'kind': 'cursor'})
                await asyncio.sleep(0.2)
            await websocket.close(code=4401)
        except WebSocketDisconnect:
            pass
        except Exception:
            with suppress(Exception):
                await websocket.close(code=4403)

    from .gateway import SignalingGate
    SignalingGate(bridge, rooms, journal, region).install(app)
    return app
