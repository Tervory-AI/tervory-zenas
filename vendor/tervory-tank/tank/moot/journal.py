"""Single-host durable media journal/outbox; no hearing/source/bench-note storage.

SQLite WAL is separate from legacy hearing data. Multi-host rollout requires a
shared transactional store before enabling multiple orchestration replicas.
"""
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from .contracts import MediaError


class Journal:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS leases (
              room TEXT NOT NULL, identity TEXT NOT NULL, user TEXT NOT NULL,
              seat TEXT NOT NULL, epoch INTEGER NOT NULL, PRIMARY KEY(room,identity));
            CREATE TABLE IF NOT EXISTS events (
              room TEXT NOT NULL, seq INTEGER NOT NULL, request TEXT NOT NULL,
              digest TEXT NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(room,seq), UNIQUE(room,request));
            CREATE TABLE IF NOT EXISTS effects (
              room TEXT NOT NULL, request TEXT NOT NULL, digest TEXT NOT NULL,
              status TEXT NOT NULL, result TEXT, PRIMARY KEY(room,request));
            CREATE TABLE IF NOT EXISTS mux_bindings (
              resource TEXT PRIMARY KEY, room TEXT NOT NULL, kind TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mux_state (
              resource TEXT PRIMARY KEY, room TEXT NOT NULL, state TEXT NOT NULL,
              created REAL NOT NULL DEFAULT 0, playback TEXT, published INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS mux_events (
              event TEXT PRIMARY KEY, digest TEXT NOT NULL, room TEXT NOT NULL,
              created REAL NOT NULL, payload TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        if self.path != ":memory:":
            os.chmod(self.path, 0o600)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def encoded(value):
        data = json.dumps(value, sort_keys=True, separators=(',', ':'))
        return data, hashlib.sha256(data.encode()).hexdigest()

    def append(self, scope, request, expected, payload):
        data, digest = self.encoded(payload)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM events WHERE room=? AND request=?', (scope.room, request)).fetchone()
            if prior:
                if prior['digest'] != digest:
                    raise MediaError('idempotency_conflict')
                return prior['seq']
            seq = db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE room=?', (scope.room,)).fetchone()[0]
            if seq != expected:
                raise MediaError('stale_sequence')
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (scope.room, seq+1, request, digest, data))
            return seq+1

    def events(self, scope, after=0):
        with self.connect() as db:
            return [{'seq': r['seq'], **json.loads(r['payload'])} for r in db.execute(
                'SELECT seq,payload FROM events WHERE room=? AND seq>? ORDER BY seq LIMIT 500', (scope.room, after))]

    def claim(self, scope, request, payload):
        _, digest = self.encoded(payload)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM effects WHERE room=? AND request=?', (scope.room, request)).fetchone()
            if row:
                if row['digest'] != digest:
                    raise MediaError('idempotency_conflict')
                return {'status': row['status'], 'result': json.loads(row['result']) if row['result'] else None}
            db.execute('INSERT INTO effects VALUES (?,?,?,?,NULL)', (scope.room, request, digest, 'started'))
            return None

    def finish(self, scope, request, result):
        data, _ = self.encoded(result)
        with self.connect() as db:
            changed = db.execute("UPDATE effects SET status='completed',result=? WHERE room=? AND request=? AND status='started'",
                (data, scope.room, request)).rowcount
            if changed != 1:
                raise MediaError('effect_not_started')

    def bind(self, scope, resource, kind):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO mux_bindings VALUES (?,?,?)', (resource, scope.room, kind))
            row = db.execute('SELECT * FROM mux_bindings WHERE resource=?', (resource,)).fetchone()
            if row['room'] != scope.room or row['kind'] != kind:
                raise MediaError('resource_binding_conflict')

    def webhook(self, event):
        # Ignore unbound account resources rather than trusting passthrough metadata.
        data, digest = self.encoded(event)
        resource = event.get('data', {}).get('id')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            binding = db.execute('SELECT * FROM mux_bindings WHERE resource=?', (resource,)).fetchone()
            if not binding:
                raise MediaError('unbound_resource')
            old = db.execute('SELECT digest FROM mux_events WHERE event=?', (event['id'],)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise MediaError('idempotency_conflict')
                return False
            from datetime import datetime
            created = datetime.fromisoformat(event['created_at'].replace('Z', '+00:00')).timestamp()
            safe = {key: event[key] for key in ('id', 'type', 'created_at')}
            safe['data'] = {key: event['data'][key] for key in ('id', 'live_stream_id', 'status', 'playback_ids') if key in event['data']}
            db.execute('INSERT INTO mux_events VALUES (?,?,?,?,?)', (event['id'], digest, binding['room'], created, json.dumps(safe)))
            states = {'video.live_stream.active': 'live', 'video.live_stream.idle': 'paused',
                'video.live_stream.disabled': 'ended', 'video.live_stream.deleted': 'deleted',
                'video.asset.ready': 'ready', 'video.asset.errored': 'failed', 'video.asset.deleted': 'deleted'}
            state = states.get(event.get('type'))
            if state:
                playback = next((p.get('id') for p in event['data'].get('playback_ids', [])
                    if p.get('policy') == 'signed' and isinstance(p.get('id'), str)), None)
                db.execute("""INSERT INTO mux_state (resource,room,state,created,playback) VALUES (?,?,?,?,?)
                    ON CONFLICT(resource) DO UPDATE SET state=excluded.state,created=excluded.created,
                    playback=COALESCE(excluded.playback,mux_state.playback)
                    WHERE excluded.created > mux_state.created AND mux_state.state <> 'deleted'""",
                    (resource, binding['room'], state, created, playback))
            return True

    def resource(self, scope, resource):
        with self.connect() as db:
            row = db.execute('SELECT * FROM mux_state WHERE resource=? AND room=?', (resource, scope.room)).fetchone()
            if not row:
                raise MediaError('not_found')
            return dict(row)

    def publish(self, scope, resource, *, founder, hearing):
        if not founder or hearing.get('id') != scope.hearing or hearing.get('region') != scope.region:
            raise MediaError('operator_required')
        if hearing.get('phase') != 'closed' or hearing.get('published') is not True:
            raise MediaError('independent_published_ruling_required')
        with self.connect() as db:
            changed = db.execute("UPDATE mux_state SET published=1 WHERE resource=? AND room=? AND state IN ('ready','live','paused','ended')",
                (resource, scope.room)).rowcount
            if not changed:
                raise MediaError('media_not_ready')

    def save_lease(self, scope, user, seat, epoch, identity):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO leases VALUES (?,?,?,?,?)', (scope.room, identity, user, seat, epoch))

    def leases(self):
        from .contracts import Scope
        with self.connect() as db:
            rows = db.execute('SELECT * FROM leases').fetchall()
        result = []
        for row in rows:
            _, region, tenant, hearing = row['room'].split(':')
            result.append((Scope(tenant, region, hearing), row['user'], row['seat'], row['epoch'], row['identity']))
        return result

    def drop_lease(self, scope, identity):
        with self.connect() as db:
            db.execute('DELETE FROM leases WHERE room=? AND identity=?', (scope.room, identity))
