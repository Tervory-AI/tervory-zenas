"""Safe readiness reporting for the shared Skala runtime and its jobs."""
from contextlib import asynccontextmanager
import logging
import os

CORE_SETTINGS = ('DATABASE_URL', 'TANK_API_KEY', 'LITELLM_MASTER_KEY')
EXPORT_SETTINGS = ('HF_TOKEN', 'HF_ORG')


def missing_settings(names, values=None):
    values = os.environ if values is None else values
    return [name for name in names if not str(values.get(name) or '').strip()]


def unavailable(names, values=None):
    missing = missing_settings(names, values)
    if not missing:
        return None
    return {'status': 'unconfigured', 'missing_settings': missing,
            'message': 'Missing settings: ' + ', '.join(missing)}


def install(app, settings):
    """Start a diagnostic-only app when configuration is incomplete.

    No router lifespan or startup worker is entered before this check. Both
    startup logging and responses contain setting names, never their values.
    """
    from starlette.responses import JSONResponse
    ordinary_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        problem = unavailable(CORE_SETTINGS, settings())
        if problem:
            logging.getLogger('tank.skala').warning(problem['message'])
            yield
            return
        async with ordinary_lifespan(application) as state:
            yield state

    app.router.lifespan_context = lifespan

    @app.middleware('http')
    async def configured(request, call_next):
        problem = unavailable(CORE_SETTINGS, settings())
        if problem:
            return JSONResponse(problem, status_code=503)
        from skala_jobs import request_key
        token = request_key.set(request.headers.get('idempotency-key', '') if request.url.path.startswith('/v1/skala/') and request.url.path.endswith('/evals') else '')
        try:
            return await call_next(request)
        finally:
            request_key.reset(token)
