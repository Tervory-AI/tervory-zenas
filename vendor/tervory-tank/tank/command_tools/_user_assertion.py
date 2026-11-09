"""Shared product verifier. Bad identity disables memory, never the plain task."""
from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import math
import re
import time
import uuid

HEADER = "X-Tervory-User-Assertion"
DOMAIN = b"tervory-user-v1\n"
TTL = 60


@dataclass(frozen=True)
class SignedUser:
    tenant_id: str
    user_id: str
    role: str


def sign_assertion(product: str, user: SignedUser, method: str, target: str, body: bytes, *,
                   key: str, now: int | None = None, nonce: str | None = None) -> str:
    """The existing Tank v1 assertion wire format, shared by shipped clients."""
    if not key:
        raise ValueError("Product key unavailable")
    payload = {"v": 1, "aud": product, "tenant_id": str(uuid.UUID(user.tenant_id)),
               "user_id": user.user_id, "role": user.role, "iat": int(time.time()) if now is None else now,
               "nonce": nonce or uuid.uuid4().hex, "method": method.upper(), "target": target,
               "body_sha256": hashlib.sha256(body).hexdigest()}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).rstrip(b"=")
    digest = hmac.new(key.encode(), DOMAIN + encoded, hashlib.sha256).hexdigest()
    return encoded.decode() + "." + digest


def bind_personal_headers(key: str, method: str, path: str, *, tenant: str, user: str,
                          body: bytes = b"") -> dict[str, str]:
    """Bind the full encoded path/query and exact body; every call gets a nonce."""
    tenant = str(uuid.UUID(str(tenant)))
    if not isinstance(user, str) or not user.strip() or len(user) > 200:
        raise ValueError("Personal memory requires user")
    actor = SignedUser(tenant, user, "member")
    return {HEADER: sign_assertion("hermes", actor, method, path, body, key=key),
            "X-Tervory-Tenant": tenant, "X-Tervory-User": user}


async def verify_assertion(assertion: str, *, key: str, product: str, tenant_id: str,
                           method: str, target: str, body: bytes, consume_nonce,
                           now: float | None = None) -> SignedUser:
    """consume_nonce must atomically claim (tenant, nonce) in shared storage."""
    try:
        if not key or not assertion or len(assertion) > 8192 or consume_nonce is None:
            raise ValueError
        encoded, signature = assertion.split(".")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or not re.fullmatch(r"[a-f0-9]{64}", signature):
            raise ValueError
        expected = hmac.new(key.encode(), DOMAIN + encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("aud") != product:
            raise ValueError
        issued, clock = payload["iat"], time.time() if now is None else now
        if type(issued) is not int or not math.isfinite(clock) or not 0 <= clock - issued < TTL:
            raise ValueError
        tenant = str(uuid.UUID(payload["tenant_id"]))
        user, role, nonce = payload["user_id"], payload["role"], payload["nonce"]
        if tenant != str(uuid.UUID(str(tenant_id))) or not isinstance(user, str) or not user.strip() or len(user) > 200:
            raise ValueError
        if not isinstance(role, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:.-]{0,80}", role) or not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce):
            raise ValueError
        if payload["method"] != method.upper() or payload["target"] != target:
            raise ValueError
        if payload["body_sha256"] != hashlib.sha256(body).hexdigest():
            raise ValueError
        if not await consume_nonce(tenant, nonce, issued + TTL):
            raise ValueError
        return SignedUser(tenant, user, role)
    except Exception:
        # Store outage, malformed input and replay all fail closed. Never echo
        # token, key, claim or driver errors into a product response/log.
        raise PermissionError("Signed user required for memory") from None


class PostgresNonces:
    """Durable atomic replay wall shared by all workers of a product."""
    def __init__(self, pool, product: str):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", product):
            raise ValueError("Invalid product")
        self.pool, self.schema = pool, "agent_" + product
        self.ready = False

    async def setup(self):
        async with self.pool.acquire() as conn, conn.transaction():
            # Parameterize the validated product schema, keeping all SQL static.
            # SET LOCAL restores the connection's search path at transaction end.
            # Put temporary relations last so they cannot shadow the nonce table.
            await conn.execute("SELECT set_config('search_path',quote_ident($1)||',pg_temp',true)", self.schema)
            if await conn.fetchval("SELECT current_schema()") != self.schema:
                raise ValueError("Product schema unavailable")
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1 || ':user-assertions',0))", self.schema)
            await conn.execute("""CREATE TABLE IF NOT EXISTS user_assertion_nonces (
                tenant_id uuid NOT NULL, nonce_hash text NOT NULL, expires_at timestamptz NOT NULL,
                PRIMARY KEY(nonce_hash))""")
            # Match the existing staged tenant-RLS rollout, never enable it early.
            guarded = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass(quote_ident($1) || '.jobs')", self.schema)
            if self.schema == "agent_tank":
                guarded = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass('tank.command_threads')")
            if guarded:
                await conn.execute("ALTER TABLE user_assertion_nonces ENABLE ROW LEVEL SECURITY")
                policy = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname=$1 AND tablename='user_assertion_nonces' AND policyname='user_assertion_tenant')", self.schema)
                if not policy:
                    await conn.execute("""CREATE POLICY user_assertion_tenant ON user_assertion_nonces
                        USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                        WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)""")
        self.ready = True

    async def claim(self, tenant: str, nonce: str, expires: int) -> bool:
        if not self.ready:
            await self.setup()
        # Store only a nonce hash. Cleanup cannot evict any still-valid assertion.
        digest = hashlib.sha256(nonce.encode()).hexdigest()
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id',$1,true), set_config('search_path',quote_ident($2)||',pg_temp',true)", tenant, self.schema)
            await conn.execute("DELETE FROM user_assertion_nonces WHERE tenant_id=$1 AND expires_at<=now()", uuid.UUID(tenant))
            claimed = await conn.fetchval("""INSERT INTO user_assertion_nonces(tenant_id,nonce_hash,expires_at)
                VALUES($1,$2,to_timestamp($3)) ON CONFLICT DO NOTHING RETURNING true""", uuid.UUID(tenant), digest, float(expires))
        return claimed is True


async def signed_user(request, *, tenant_id: str, product: str, required: bool = False,
                      key: str | None = None, nonces=None) -> SignedUser | None:
    """Products call after tenant validation; body user_id is never identity.

    Use required=True for memory controls; ordinary tasks use None to skip memory.
    """
    import os
    from fastapi import HTTPException
    cached = getattr(request.state, "verified_product_user", None)
    if cached is not None:
        if cached.tenant_id == str(tenant_id).lower() and getattr(request.state, "verified_product", None) == product:
            return cached
        if required:
            raise HTTPException(401, "Signed user required for memory")
        return None
    try:
        if nonces is None:
            pool = getattr(request.app.state, "pool", None)
            if pool is None:
                raise PermissionError
            stores = getattr(request.app.state, "product_nonce_stores", None)
            if stores is None:
                stores = request.app.state.product_nonce_stores = {}
            nonces = stores.get(product)
            if nonces is None or nonces.pool is not pool:
                nonces = stores[product] = PostgresNonces(pool, product)
        user = await verify_assertion(request.headers.get(HEADER, ""), key=key if key is not None else (os.environ.get("TANK_KEY_" + product.upper()) or os.environ.get("TANK_KEY", "")),
            product=product, tenant_id=tenant_id, method=request.method,
            target=request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
                   + ("?" + request.scope["query_string"].decode("ascii") if request.scope.get("query_string") else ""),
            body=await request.body(), consume_nonce=nonces.claim)
    except (PermissionError, ValueError):
        if required:
            raise HTTPException(401, "Signed user required for memory") from None
        return None
    request.state.verified_product_user = user
    request.state.verified_product = product
    return user
