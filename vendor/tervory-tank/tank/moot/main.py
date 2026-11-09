"""Standalone Tank-owned Moot runtime. No resources or model calls at startup."""
import os
import ssl
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI
from .bridge import LegacyHearingBridge
from .journal import Journal
from .livekit import Rooms
from .mux import Mux
from .events import LiveKitEvents
from .service import create_app


def build(*, mux_account=None):
    required = ('MOOT_REGION', 'MOOT_JOURNAL_PATH', 'MOOT_INTERNAL_API_KEY',
        'MOOT_LIVEKIT_URL', 'MOOT_LIVEKIT_PUBLIC_URL', 'MOOT_LIVEKIT_API_KEY',
        'MOOT_LIVEKIT_API_SECRET', 'MOOT_LEGACY_URL', 'MOOT_LEGACY_API_KEY', 'MOOT_TLS_CA_FILE')
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        app = FastAPI()
        @app.get('/healthz')
        async def health():
            return {'product': 'moot', 'state': 'unavailable', 'missing_settings': missing}
        @app.get('/readyz', status_code=503)
        async def ready():
            return {'state': 'unavailable', 'missing_settings': missing}
        return app
    region = os.environ['MOOT_REGION']
    if region not in {'us-east', 'za'}:
        raise RuntimeError('invalid MOOT_REGION')
    # Standalone deployment requires verified TLS. Explicit local test harnesses
    # instantiate Rooms directly and never relax this deployment boundary.
    for name, scheme in (('MOOT_LIVEKIT_URL', 'https'), ('MOOT_LIVEKIT_PUBLIC_URL', 'wss')):
        parsed = urlsplit(os.environ[name])
        if parsed.scheme != scheme or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError('Moot signaling requires secure TLS URLs')
    try:
        tls = ssl.create_default_context()
        tls.load_verify_locations(cafile=os.environ['MOOT_TLS_CA_FILE'])
    except (OSError, ssl.SSLError):
        raise RuntimeError('Moot TLS CA is unavailable or invalid') from None
    internal = httpx.AsyncClient(verify=tls, trust_env=False, follow_redirects=False)
    journal = Journal(Path(os.environ['MOOT_JOURNAL_PATH']))
    rooms = Rooms(*(os.environ[name] for name in ('MOOT_LIVEKIT_URL', 'MOOT_LIVEKIT_PUBLIC_URL',
        'MOOT_LIVEKIT_API_KEY', 'MOOT_LIVEKIT_API_SECRET')), tls_context=tls)
    bridge = LegacyHearingBridge(internal, os.environ['MOOT_LEGACY_URL'], os.environ['MOOT_LEGACY_API_KEY'], region)
    # The coordinator supplies Tank's shared Mux account adapter. Standalone
    # startup never creates a second account/key path or guesses an endpoint.
    mux = Mux(journal, account=mux_account)
    app = create_app(bridge=bridge, rooms=rooms, journal=journal,
        internal_key=os.environ['MOOT_INTERNAL_API_KEY'], region=region, mux=mux,
        livekit_events=LiveKitEvents(journal, os.environ['MOOT_LIVEKIT_API_KEY'],
            os.environ['MOOT_LIVEKIT_API_SECRET'], region))
    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            try:
                yield
            finally:
                await internal.aclose()
    app.router.lifespan_context = lifespan
    @app.get('/readyz')
    async def ready():
        from fastapi import HTTPException
        try:
            response = await internal.get(os.environ['MOOT_LIVEKIT_URL'].rstrip('/') + '/', timeout=2)
            response.raise_for_status()
            response = await internal.get(os.environ['MOOT_LEGACY_URL'].rstrip('/') + '/health', timeout=2)
            response.raise_for_status()
        except Exception:
            raise HTTPException(503, 'runtime_dependency_unavailable') from None
        return {'product': 'moot', 'region': region, 'speech': 'unavailable'}
    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(build(), host='0.0.0.0', port=8091, access_log=False)
