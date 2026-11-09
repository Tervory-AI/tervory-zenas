"""Durable assertion replay protection on an existing private service volume."""
import asyncio
from contextlib import closing
import hashlib
import math
from pathlib import Path
import sqlite3
import time


class SQLiteNonces:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    async def claim(self, tenant: str, nonce: str, expires: float) -> bool:
        return await asyncio.to_thread(self._claim, tenant, nonce, expires)

    def _claim(self, tenant: str, nonce: str, expires: float) -> bool:
        if not math.isfinite(expires) or expires <= time.time():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest = hashlib.sha256(nonce.encode()).hexdigest()
        with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""CREATE TABLE IF NOT EXISTS assertion_nonces (
                tenant TEXT NOT NULL, nonce_hash TEXT NOT NULL, expires REAL NOT NULL,
                PRIMARY KEY(tenant, nonce_hash))""")
            conn.execute("DELETE FROM assertion_nonces WHERE expires<=?", (time.time(),))
            result = conn.execute("INSERT OR IGNORE INTO assertion_nonces VALUES (?,?,?)",
                                  (tenant, digest, expires))
            return result.rowcount == 1
