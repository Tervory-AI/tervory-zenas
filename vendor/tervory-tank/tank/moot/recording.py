"""Consent and local object retention. No recording is started implicitly."""
from datetime import datetime, timezone
from pathlib import Path
from .contracts import MediaError


class Recording:
    def __init__(self, journal):
        self.journal = journal

    @staticmethod
    def consent_key(seat, epoch):
        return f'{seat}:{epoch}'

    def consent(self, scope, request, revision, *, seat, user, projection, accepted):
        if seat not in projection.get('my_seats', []):
            raise MediaError('seat_not_owned')
        controller = projection['seats'][seat]
        if controller['kind'] != 'human':
            raise MediaError('seat_not_owned')
        return self.journal.append(scope, request, revision, {'kind': 'recording_consent',
            'seat': seat, 'epoch': controller['epoch'], 'accepted': accepted is True})

    def allowed(self, scope, projection):
        if projection.get('id') != scope.hearing or projection.get('region') != scope.region:
            raise MediaError('not_found')
        with self.journal.connect() as db:
            for seat, controller in projection['seats'].items():
                if controller['kind'] == 'ai':
                    continue
                row = db.execute("""SELECT payload FROM events WHERE room=?
                    AND json_extract(payload,'$.kind')='recording_consent'
                    AND json_extract(payload,'$.seat')=? AND json_extract(payload,'$.epoch')=?
                    ORDER BY seq DESC LIMIT 1""", (scope.room, seat, controller['epoch'])).fetchone()
                import json
                if not row or json.loads(row['payload']).get('accepted') is not True:
                    return False
        return True

    @staticmethod
    def local_delete(root, relative, *, due_at, now=None):
        now = datetime.now(timezone.utc) if now is None else now
        if now < due_at:
            raise MediaError('retention_not_due')
        base = Path(root).resolve(strict=True)
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or path == base:
            raise MediaError('invalid_recording_path')
        if path.is_file():
            path.unlink()

    async def start(self, scope, request_id, *, founder, projection, rooms, output):
        if not founder:
            raise MediaError('operator_required')
        if not self.allowed(scope, projection):
            raise MediaError('recording_consent_required')
        prior = self.journal.claim(scope, request_id, {'operation': 'record', 'revision': projection['revision']})
        if prior:
            return prior
        result = await rooms.record(scope, consented=True, request=output)
        value = {'egress_id': result.egress_id, 'state': 'starting'}
        self.journal.finish(scope, request_id, value)
        return value

    async def stop(self, scope, request_id, *, rooms, egress_id):
        # The id must have been returned by this hearing's recording effect.
        with self.journal.connect() as db:
            row = db.execute("SELECT 1 FROM effects WHERE room=? AND status='completed' AND json_extract(result,'$.egress_id')=?",
                (scope.room, egress_id)).fetchone()
        if row is None:
            raise MediaError('not_found')
        prior = self.journal.claim(scope, request_id, {'operation': 'stop_recording', 'egress_id': egress_id})
        if prior:
            return prior
        await rooms.stop_recording(egress_id)
        value = {'egress_id': egress_id, 'state': 'stopping'}
        self.journal.finish(scope, request_id, value)
        return value
