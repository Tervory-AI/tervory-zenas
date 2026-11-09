"""Pinned LiveKit API. Browser grants cannot administer, record, or send data."""
from datetime import timedelta
from contextlib import asynccontextmanager
import ssl
import aiohttp
import json
from livekit import api
from .contracts import authorize_projection, MediaError


class Rooms:
    def __init__(self, url, public_url, key, secret, *, tls_context=None):
        self.url, self.public_url, self.key, self.secret = url, public_url, key, secret
        self.tls_context = tls_context or ssl.create_default_context()

    @asynccontextmanager
    async def client(self):
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=self.tls_context), trust_env=False
        ) as session:
            async with api.LiveKitAPI(self.url, self.key, self.secret, session=session) as client:
                yield client

    async def create(self, scope):
        async with self.client() as client:
            return await client.room.create_room(api.CreateRoomRequest(
                name=scope.room, empty_timeout=120, max_participants=8))

    def grant(self, scope, user, seat, projection):
        identity = authorize_projection(scope, user, seat, projection)
        token = (api.AccessToken(self.key, self.secret).with_identity(identity)
            .with_ttl(timedelta(seconds=60)).with_metadata(json.dumps({'seat': seat,
                'epoch': projection['seats'][seat]['epoch']}))
            .with_grants(api.VideoGrants(room_join=True, room=scope.room,
                can_publish=True, can_subscribe=True, can_publish_data=False,
                can_publish_sources=['camera', 'microphone'], can_update_own_metadata=False)))
        return {'url': self.public_url, 'token': token.to_jwt(), 'identity': identity,
                'epoch': projection['seats'][seat]['epoch'], 'expires_in': 60}

    async def revoke(self, scope, identity):
        # OSS removal closes the current connection; it does not revoke its JWT.
        # The signaling gateway checks epoch/membership on every reconnect.
        async with self.client() as client:
            await client.room.remove_participant(api.RoomParticipantIdentity(
                room=scope.room, identity=identity))

    async def record(self, scope, *, consented, request):
        if not consented:
            raise MediaError('recording_consent_required')
        if request.room_name != scope.room:
            raise MediaError('not_found')
        async with self.client() as client:
            return await client.egress.start_room_composite_egress(request)

    async def stop_recording(self, egress_id):
        async with self.client() as client:
            return await client.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))

    def worker_grant(self, scope, seat, projection):
        if projection.get('id') != scope.hearing or projection.get('region') != scope.region:
            raise MediaError('not_found')
        controller = projection.get('seats', {}).get(seat, {})
        if seat not in {'applicant', 'respondent', 'judge'} or controller.get('kind') != 'ai' or projection.get('phase') != 'hearing':
            raise MediaError('ai_seat_required')
        identity = f"ai:{seat}:{controller['epoch']}"
        return (api.AccessToken(self.key, self.secret).with_identity(identity)
            .with_ttl(timedelta(seconds=60)).with_grants(api.VideoGrants(
                room_join=True, room=scope.room, can_publish=True,
                can_subscribe=True, can_publish_data=False,
                can_publish_sources=['microphone'], can_update_own_metadata=False,
                agent=True)).to_jwt())
