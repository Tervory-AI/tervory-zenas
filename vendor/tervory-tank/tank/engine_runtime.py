"""Explicit runtime opt-in for the services in the engine compose profile."""
import os

LOCAL = ('local', 'local-large')


def local_url(model):
    # DECISION URLs, not an enabled profile alone, opt serving into local models.
    # No implicit Boston/ZA endpoint. Small is the master switch for both rungs.
    if not os.environ.get('ENGINE_LOCAL_URL', '').strip():
        return ''
    key = 'ENGINE_LOCAL_URL' if model == 'local' else 'ENGINE_LARGE_URL'
    return os.environ.get(key, '').strip().rstrip('/')


def available(model):
    return model not in LOCAL or bool(local_url(model))


def local_status():
    return 'local rung online' if all(available(m) for m in LOCAL) else 'local rung offline'


def traces_enabled():
    # DECISION missing Langfuse disables tracing without blocking paid serving.
    return bool(os.environ.get('LANGFUSE_HOST', '').strip()) and os.environ.get('LANGFUSE_ENABLED', 'true') == 'true'
