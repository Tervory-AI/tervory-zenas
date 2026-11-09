"""Region-bound object deletion schedule; no uploads or bucket creation."""
from datetime import datetime, timezone
from .contracts import MediaError


class Retention:
    def __init__(self, journal, store, buckets):
        self.journal, self.store, self.buckets = journal, store, buckets
        with journal.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS media_objects (
                room TEXT NOT NULL, bucket TEXT NOT NULL, object_key TEXT NOT NULL,
                expires REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(bucket,object_key))''')

    def bind(self, scope, key, expires):
        if scope.region not in self.buckets or not key.startswith(scope.room + '/') or '..' in key.split('/'):
            raise MediaError('invalid_recording_object')
        if expires.tzinfo is None:
            raise MediaError('invalid_retention')
        bucket = self.buckets[scope.region]
        with self.journal.connect() as db:
            db.execute('INSERT OR IGNORE INTO media_objects VALUES (?,?,?,?,0)',
                (scope.room, bucket, key, expires.timestamp()))
            row = db.execute('SELECT room FROM media_objects WHERE bucket=? AND object_key=?', (bucket, key)).fetchone()
            if row['room'] != scope.room:
                raise MediaError('resource_binding_conflict')

    async def run_once(self, scope, *, now=None):
        if self.store is None:
            raise MediaError('recording_storage_unavailable')
        now = datetime.now(timezone.utc) if now is None else now
        with self.journal.connect() as db:
            rows = db.execute('SELECT * FROM media_objects WHERE room=? AND expires<=? AND deleted=0 LIMIT 100',
                (scope.room, now.timestamp())).fetchall()
        deleted = 0
        for row in rows:
            if self.buckets.get(scope.region) != row['bucket']:
                raise MediaError('wrong_region')
            # The existing Tank object adapter must treat missing objects as a
            # successful delete. A crash can repeat a delete, never recreate data.
            await self.store.delete(bucket=row['bucket'], key=row['object_key'])
            with self.journal.connect() as db:
                db.execute('UPDATE media_objects SET deleted=1 WHERE room=? AND bucket=? AND object_key=?',
                    (scope.room, row['bucket'], row['object_key']))
            deleted += 1
        return deleted
