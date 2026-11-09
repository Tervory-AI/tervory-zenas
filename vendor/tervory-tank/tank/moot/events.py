"""Signed LiveKit lifecycle ingest; only media metadata enters the shared journal."""
from livekit import api
from .contracts import Scope, MediaError


class LiveKitEvents:
    def __init__(self, journal, key, secret, region):
        self.journal, self.region = journal, region
        self.receiver = api.WebhookReceiver(api.TokenVerifier(key, secret))

    def receive(self, raw, authorization):
        if len(raw) > 1024 * 1024:
            raise MediaError('invalid_webhook')
        try:
            event = self.receiver.receive(raw.decode(), authorization.removeprefix('Bearer '))
            name = event.room.name or event.egress_info.room_name
            product, region, tenant, hearing = name.split(':')
            scope = Scope(tenant, region, hearing)
            if product != 'moot' or region != self.region or not event.id:
                raise ValueError()
        except Exception:
            raise MediaError('invalid_webhook') from None
        kinds = {'egress_started': 'recording_started', 'egress_updated': 'recording_updated',
            'egress_ended': 'recording_ended', 'room_finished': 'room_ended'}
        if event.event not in kinds:
            return False
        # No error strings, RTMP destinations, filenames, participant identities,
        # token payloads or source data are exposed in events.
        payload = {'kind': kinds[event.event], 'egress_id': event.egress_info.egress_id,
            'status': int(event.egress_info.status)}
        with self.journal.connect() as db:
            revision = db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE room=?', (scope.room,)).fetchone()[0]
        return self.journal.append(scope, 'livekit:' + event.id, revision, payload)
