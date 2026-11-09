"""Sign authenticated user context only for configured product origins."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
import uuid
from urllib.parse import urlsplit

HEADER = "X-Tervory-User-Assertion"
DOMAIN = b"tervory-user-v1\n"
PRODUCTS = frozenset({"autumbra", "base", "market_lab", "radix", "rig", "skala", "tiger", "tisan", "workspace", "zenas", "trigger", "opli", "ghost"})


@dataclass(frozen=True)
class User:
    tenant_id: str
    user_id: str
    role: str


current: ContextVar[User | None] = ContextVar("product_signed_user", default=None)


def user_from_actor(actor) -> User | None:
    if actor is None:
        return None
    tenant = getattr(actor, "tenant_id", getattr(actor, "tenant", None))
    user = getattr(actor, "user_id", getattr(actor, "user", None))
    if not isinstance(user, str) or not user.strip() or len(user) > 200:
        return None
    role = "founder" if getattr(actor, "founder", False) else "org:admin" if getattr(actor, "tenant_admin", False) else "member"
    try:
        tenant = str(uuid.UUID(str(tenant)))
    except (ValueError, TypeError):
        return None
    return User(tenant, user, getattr(actor, "role", None) or role)


@contextmanager
def actor_scope(actor):
    """Only authentication middleware/dependencies may supply actor."""
    token = current.set(user_from_actor(actor) if actor is not None else current.get())
    try:
        yield
    finally:
        current.reset(token)


def key_setting(product: str) -> str:
    if product not in PRODUCTS:
        raise ValueError("Unknown product")
    return "TANK_KEY_" + product.upper()


def sign(product: str, user: User, method: str, target: str, body: bytes, *, key: str,
         now: int | None = None, nonce: str | None = None) -> str:
    from command_tools._user_assertion import sign_assertion
    return sign_assertion(product, user, method, target, body, key=key, now=now, nonce=nonce)


def product_for_url(url) -> str | None:
    """Exact origins only; never send an assertion to a provider or redirect."""
    origin = (url.scheme, url.host, url.port or (443 if url.scheme == "https" else 80))
    matches = set()
    for product in PRODUCTS:
        defaults = ["http://agent-" + product.replace("_", "-") + ":8000"]
        for name in (product.upper() + "_AGENT_URL", product.upper() + "_URL"):
            value = os.environ.get(name)
            if value:
                defaults.append(value)
        for base in defaults:
            try:
                parsed = urlsplit(base)
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
            except ValueError:
                continue
            if origin == (parsed.scheme, parsed.hostname, port):
                matches.add(product)
    return next(iter(matches)) if len(matches) == 1 else None


def attach(request):
    # Do not forward any previously supplied assertion through a redirect.
    request.headers.pop(HEADER, None)
    product, user = product_for_url(request.url), current.get()
    hint = request.extensions.get("tervory_product_origin")
    if hint and hint[0] in PRODUCTS:
        origin = (request.url.scheme, request.url.host, request.url.port or (443 if request.url.scheme == "https" else 80))
        if origin == hint[1]:
            product = hint[0]
    if product is None or user is None:
        return
    key = os.environ.get(key_setting(product), "")
    if not key:
        return  # Memory stays off; the product's ordinary auth still applies.
    try:
        body = request.content
    except Exception:
        return  # A streaming upload has no replayable body to bind.
    request.headers[HEADER] = sign(product, user, request.method, request.url.raw_path.decode("ascii"), body, key=key)


async def request_hook(request):
    attach(request)


def bind_actor(function):
    """Preserve verified command actors across queue and in-process dispatch."""
    from functools import wraps
    from inspect import signature
    parameters = signature(function)
    @wraps(function)
    async def bound(*args, **kwargs):
        actor = parameters.bind(*args, **kwargs).arguments.get("actor")
        from command import current_actor
        actor = current_actor(actor)
        with actor_scope(actor):
            return await function(*args, **kwargs)
    return bound


def product_extensions(product: str, base: str) -> dict:
    """For a tenant-specific origin already resolved by Tank's room registry.

    This is an HTTP client extension, never a header accepted from callers.
    A redirect cannot carry identity to a different origin.
    """
    key_setting(product)
    parsed = urlsplit(base)
    return {"tervory_product_origin": (product, (parsed.scheme, parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80)))}
