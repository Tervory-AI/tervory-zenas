"""Serialize scheduled and manually started copies using PostgreSQL session locks."""
from contextlib import asynccontextmanager
from functools import wraps
import hashlib


@asynccontextmanager
async def claim(conn, *, region, name):
    lock = int.from_bytes(hashlib.sha256(f'zenas:{region}:{name}'.encode()).digest()[:8], 'big', signed=True)
    acquired = await conn.fetchval('SELECT pg_try_advisory_lock($1)', lock)
    if acquired is not True:
        yield False
        return
    try:
        yield True
    finally:
        await conn.fetchval('SELECT pg_advisory_unlock($1)', lock)


def exclusive_job(name):
    def decorate(function):
        @wraps(function)
        async def run(conn, *, region):
            async def held(connection):
                async with claim(connection, region=region, name=name) as acquired:
                    if not acquired:
                        return {'ok': True, 'status': 'already running', 'split': 0, 'excluded': 0}
                    return await function(connection, region=region)
            if hasattr(conn, 'acquire'):
                async with conn.acquire() as connection:
                    return await held(connection)
            return await held(conn)
        return run
    return decorate
