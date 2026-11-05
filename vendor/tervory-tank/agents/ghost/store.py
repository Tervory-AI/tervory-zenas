"""Ghost's own volume only. No production database credential or SQL client."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path: Path, region: str = "us-east"):
        if region not in {"us-east", "za"}:
            raise ValueError("Invalid Autumbra Watch region.")
        self.region = region
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS region (id INTEGER PRIMARY KEY CHECK(id=1), home TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS active_incident ON incidents(tenant, fingerprint)
                WHERE state != 'solved';
            CREATE TABLE IF NOT EXISTS memories (
                tenant TEXT NOT NULL, incident TEXT NOT NULL, fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(tenant, incident)
            );
            -- GHOST-2: one row per app, component and version, stamped when seen.
            CREATE TABLE IF NOT EXISTS ghost_inventory (
                tenant TEXT NOT NULL, app TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
                version TEXT NOT NULL, ecosystem TEXT NOT NULL, source TEXT NOT NULL,
                first_seen TEXT NOT NULL, seen_at TEXT NOT NULL, current INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(tenant, app, kind, name, version, source)
            );
            CREATE TABLE IF NOT EXISTS ghost_advisories (
                tenant TEXT NOT NULL, id TEXT NOT NULL, source TEXT NOT NULL, published TEXT NOT NULL,
                payload TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(tenant, id)
            );
            CREATE TABLE IF NOT EXISTS ghost_kev (
                tenant TEXT NOT NULL, cve TEXT NOT NULL, date_added TEXT NOT NULL, PRIMARY KEY(tenant, cve)
            );
            CREATE TABLE IF NOT EXISTS ghost_matches (
                tenant TEXT NOT NULL, id TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL, flagged_at TEXT NOT NULL, PRIMARY KEY(tenant, id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS open_match ON ghost_matches(tenant, fingerprint)
                WHERE state = 'open';
            -- GHOST-2 D3: one row per proposed shield; the payload carries its plan and receipts.
            CREATE TABLE IF NOT EXISTS ghost_shields (
                tenant TEXT NOT NULL, id TEXT NOT NULL, site TEXT NOT NULL, kind TEXT NOT NULL,
                path TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, proposed_at TEXT NOT NULL,
                PRIMARY KEY(tenant, id)
            );
            CREATE TABLE IF NOT EXISTS ghost_state (
                tenant TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(tenant, key)
            );
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO region VALUES (1,?)", (region,))
        if self.db.execute("SELECT home FROM region WHERE id=1").fetchone()[0] != region:
            self.db.close()
            raise ValueError("Autumbra Watch's volume belongs to another region.")

    def open(self, tenant: str, target: str, signal: str, origin: str) -> dict:
        fingerprint = f"{target}:{signal}"
        existing = self.db.execute(
            "SELECT payload FROM incidents WHERE tenant=? AND fingerprint=? AND state!='solved'",
            (tenant, fingerprint),
        ).fetchone()
        if existing:
            return json.loads(existing[0])
        item = {"id": str(uuid.uuid4()), "target": target, "signal": signal, "origin": origin,
                "state": "open", "steps": [], "cost_usd": 0,
                "created_at": datetime.now(timezone.utc).isoformat()}
        with self.db:
            self.db.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?)",
                            (item["id"], tenant, fingerprint, item["state"], json.dumps(item), item["created_at"]))
        return item

    def get(self, tenant: str, incident: str) -> dict:
        row = self.db.execute("SELECT payload FROM incidents WHERE tenant=? AND id=?", (tenant, incident)).fetchone()
        if row is None:
            raise LookupError("Issue not found.")
        return json.loads(row[0])

    def save(self, tenant: str, item: dict) -> None:
        with self.db:
            self.db.execute("UPDATE incidents SET state=?,payload=? WHERE tenant=? AND id=?",
                            (item["state"], json.dumps(item), tenant, item["id"]))

    def list(self, tenant: str) -> list[dict]:
        rows = self.db.execute("SELECT payload FROM incidents WHERE tenant=? ORDER BY created_at DESC LIMIT 100", (tenant,))
        return [json.loads(row[0]) for row in rows]

    async def recall(self, tenant: str, fingerprint: str) -> list[dict]:
        rows = self.db.execute("SELECT payload FROM memories WHERE tenant=? AND fingerprint=? LIMIT 5", (tenant, fingerprint))
        return [json.loads(row[0]) for row in rows]

    async def remember(self, tenant: str, item: dict) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO memories VALUES (?,?,?,?)",
                            (tenant, item["id"], f"{item['target']}:{item['signal']}", json.dumps(item)))

    def gold(self, tenant: str, limit: int) -> list[dict]:
        rows = self.db.execute("SELECT payload FROM memories WHERE tenant=? ORDER BY rowid DESC LIMIT ?", (tenant, limit))
        return [json.loads(row[0]) for row in rows]

    # ---------- GHOST-2 inventory ----------

    def replace_inventory(self, tenant: str, source: str, components: list[dict], seen_at: str) -> dict:
        """Upsert this source's current snapshot; rows it no longer reports stay as history (current=0)."""
        keys = set()
        with self.db:
            for c in components:
                key = (c["app"], c["kind"], c["name"], c["version"])
                keys.add(key)
                self.db.execute("""INSERT INTO ghost_inventory VALUES (?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(tenant, app, kind, name, version, source)
                    DO UPDATE SET seen_at=excluded.seen_at, current=1, ecosystem=excluded.ecosystem""",
                                (tenant, *key, c.get("ecosystem", "generic"), source, seen_at, seen_at))
            stale = [row for row in self.db.execute(
                "SELECT app, kind, name, version FROM ghost_inventory WHERE tenant=? AND source=? AND current=1",
                (tenant, source)) if tuple(row) not in keys]
            for row in stale:
                self.db.execute("""UPDATE ghost_inventory SET current=0 WHERE tenant=? AND source=?
                    AND app=? AND kind=? AND name=? AND version=?""", (tenant, source, *tuple(row)))
        return {"source": source, "current": len(keys), "retired": len(stale), "seen_at": seen_at}

    def inventory(self, tenant: str, current_only: bool = True) -> list[dict]:
        sql = "SELECT app, kind, name, version, ecosystem, source, first_seen, seen_at, current FROM ghost_inventory WHERE tenant=?"
        if current_only:
            sql += " AND current=1"
        rows = self.db.execute(sql + " ORDER BY app, kind, name, version", (tenant,))
        return [dict(row) for row in rows]

    # ---------- GHOST-2 advisories and matches ----------

    def save_advisory(self, tenant: str, advisory: dict, fetched_at: str) -> None:
        with self.db:
            self.db.execute("""INSERT INTO ghost_advisories VALUES (?,?,?,?,?,?)
                ON CONFLICT(tenant, id) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at,
                published=excluded.published""",
                            (tenant, advisory["id"], advisory["source"], advisory["published"],
                             json.dumps(advisory), fetched_at))

    def advisories(self, tenant: str) -> list[dict]:
        rows = self.db.execute("SELECT payload FROM ghost_advisories WHERE tenant=? ORDER BY published, id", (tenant,))
        return [json.loads(row[0]) for row in rows]

    def advisory_ids(self, tenant: str) -> dict[str, str]:
        rows = self.db.execute("SELECT id, payload FROM ghost_advisories WHERE tenant=?", (tenant,))
        return {row[0]: json.loads(row[1]).get("modified", "") for row in rows}

    def replace_kev(self, tenant: str, entries: dict[str, str]) -> None:
        with self.db:
            for cve, added in entries.items():
                self.db.execute("INSERT OR REPLACE INTO ghost_kev VALUES (?,?,?)", (tenant, cve, added))

    def kev(self, tenant: str) -> dict[str, str]:
        return {row[0]: row[1] for row in self.db.execute("SELECT cve, date_added FROM ghost_kev WHERE tenant=?", (tenant,))}

    def open_match(self, tenant: str, fingerprint: str, item: dict) -> tuple[dict, bool]:
        existing = self.db.execute(
            "SELECT payload FROM ghost_matches WHERE tenant=? AND fingerprint=? AND state='open'",
            (tenant, fingerprint)).fetchone()
        if existing:
            return json.loads(existing[0]), False
        item = {**item, "id": str(uuid.uuid4()), "fingerprint": fingerprint, "state": "open"}
        with self.db:
            self.db.execute("INSERT INTO ghost_matches VALUES (?,?,?,?,?,?)",
                            (tenant, item["id"], fingerprint, "open", json.dumps(item), item["flagged_at"]))
        return item, True

    def save_match(self, tenant: str, item: dict) -> None:
        with self.db:
            self.db.execute("UPDATE ghost_matches SET state=?, payload=? WHERE tenant=? AND id=?",
                            (item["state"], json.dumps(item), tenant, item["id"]))

    def matches(self, tenant: str, state: str | None = None, limit: int = 500) -> list[dict]:
        sql, args = "SELECT payload FROM ghost_matches WHERE tenant=?", [tenant]
        if state:
            sql, args = sql + " AND state=?", [*args, state]
        rows = self.db.execute(sql + " ORDER BY flagged_at DESC LIMIT ?", (*args, limit))
        return [json.loads(row[0]) for row in rows]

    def save_shield(self, tenant: str, shield: dict) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO ghost_shields VALUES (?,?,?,?,?,?,?,?)",
                            (tenant, shield["id"], shield["site"], shield["kind"], shield["path"], shield["state"],
                             json.dumps(shield), shield["proposed_at"]))

    def shield(self, tenant: str, shield_id: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM ghost_shields WHERE tenant=? AND id=?", (tenant, shield_id)).fetchone()
        return json.loads(row[0]) if row else None

    def shields(self, tenant: str, limit: int = 500) -> list[dict]:
        rows = self.db.execute("SELECT payload FROM ghost_shields WHERE tenant=? ORDER BY proposed_at DESC LIMIT ?",
                               (tenant, limit))
        return [json.loads(row[0]) for row in rows]

    def open_shield(self, tenant: str, site: str, kind: str, path: str) -> dict | None:
        row = self.db.execute(
            "SELECT payload FROM ghost_shields WHERE tenant=? AND site=? AND kind=? AND path=?"
            " AND state IN ('proposed','dry_run','brief','up','undo_failed') ORDER BY proposed_at DESC LIMIT 1",
            (tenant, site, kind, path)).fetchone()
        return json.loads(row[0]) if row else None

    def get_state(self, tenant: str, key: str, default=None):
        row = self.db.execute("SELECT value FROM ghost_state WHERE tenant=? AND key=?", (tenant, key)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, tenant: str, key: str, value) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO ghost_state VALUES (?,?,?)", (tenant, key, json.dumps(value)))

    def close(self) -> None:
        self.db.close()
