"""SQL record and founder publication routes. Internal authenticated service only."""

import os
import uuid
from datetime import datetime
from typing import Literal

from fastapi import Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from hermes.auth import identify
from hermes.publishing import Publisher
from hermes.references import reference_corpora
from hermes.records import ClaimTooLong


def claim_validation_error(field: str, error: ClaimTooLong) -> RequestValidationError:
    return RequestValidationError([
        {"type": "value_error", "loc": ("body", field),
         "msg": "Value error, " + str(error), "ctx": {"error": str(error)}}
    ])


class Scoped(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    tenant_id: uuid.UUID
    agent: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")


class FounderMemory(Scoped):
    id: uuid.UUID
    claim: str = Field(min_length=1, max_length=8000)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=0.5, ge=0, le=1)
    last_verified: datetime | None = None


class IdentityInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed: bool = False
    names: list[str] = Field(max_length=500)
    emails: list[str] = Field(max_length=500)
    phones: list[str] = Field(max_length=500)
    addresses: list[str] = Field(max_length=500)
    client_domains: list[str] = Field(max_length=500)


class Policy(Scoped):
    enabled: bool = False
    identifiers: IdentityInventory | None = None


class Publication(Scoped):
    source_row: uuid.UUID


def founder(key: str, user: str) -> str:
    allowed = {
        v.strip()
        for v in os.environ.get("TERVORY_ADMIN_USER_IDS", "").split(",")
        if v.strip()
    }
    try:
        permitted = identify(key) is None and user in allowed
    except HTTPException:
        permitted = False
    if not permitted:
        raise HTTPException(404, "not found")
    return user


def register_record_routes(app, records, resolve_agent):
    async def enforce_founder_personal(
        request: Request,
        *,
        method: str,
        path: str,
        key: str,
        tenant,
        user: str,
    ) -> None:
        """Founder personal ops: workspace key is not end-user proof; bind admin actor."""
        from hermes.signed_identity import verify_personal_identity
        await verify_personal_identity(
            request=request, records=records(), key=key,
            body_tenant=str(tenant) if tenant is not None else None, body_user=str(user).strip(),
        )

    def ready(agent: str):
        r = records()
        if r is None:
            raise HTTPException(503, "memory store unreachable")
        if agent not in r.agents:
            raise HTTPException(404, "Unknown agent.")
        return r

    @app.get("/records")
    async def listing(
        request: Request,
        tenant_id: uuid.UUID,
        agent: str | None = None,
        tier: Literal["client", "platform"] = "client",
        user_id: str | None = Query(default=None, min_length=1, max_length=200),
        x_tank_key: str = Header(default=""),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        selected = resolve_agent(identify(x_tank_key), agent, write=False)
        if user_id is not None and str(user_id).strip():
            from hermes.signed_identity import verify_personal_identity
            await verify_personal_identity(
                request=request, records=records(), key=x_tank_key,
                body_tenant=str(tenant_id), body_user=str(user_id).strip(),
            )
        return {"memories": await ready(selected).list(selected, str(tenant_id), tier, limit, offset, user_id=user_id)}

    @app.get("/records/{id}")
    async def get_record(
        request: Request,
        id: uuid.UUID,
        tenant_id: uuid.UUID,
        agent: str | None = None,
        user_id: str | None = Query(default=None, min_length=1, max_length=200),
        x_tank_key: str = Header(default=""),
    ):
        selected = resolve_agent(identify(x_tank_key), agent, write=False)
        if user_id is not None and str(user_id).strip():
            from hermes.signed_identity import verify_personal_identity
            await verify_personal_identity(
                request=request, records=records(), key=x_tank_key,
                body_tenant=str(tenant_id), body_user=str(user_id).strip(),
            )
        row = await ready(selected).get(selected, str(tenant_id), str(id), user_id=user_id)
        if row is None:
            raise HTTPException(404, "not found")
        return row

    @app.get("/reference-corpora")
    async def references(
        tenant_id: uuid.UUID,
        agent: str | None = None,
        x_tank_key: str = Header(default=""),
    ):
        selected = resolve_agent(identify(x_tank_key), agent, write=False)
        return dict(
            tenant_id=str(tenant_id),
            corpora=reference_corpora(selected, os.environ.get("TANK_REGION", "")),
        )

    @app.get("/founder/records")
    async def founder_records(
        request: Request,
        tenant_id: uuid.UUID,
        agent: str,
        tier: Literal["client", "platform"] = "client",
        x_tank_key: str = Header(default=""),
        x_tervory_admin: str = Header(default=""),
    ):
        actor = founder(x_tank_key, x_tervory_admin)
        await enforce_founder_personal(
            request, method="GET", path="/founder/records", key=x_tank_key,
            tenant=tenant_id, user=actor,
        )
        return {"memories": await ready(agent).list(agent, str(tenant_id), tier, user_id=actor)}

    @app.get("/founder/records/{id}")
    async def founder_record(
        request: Request,
        id: uuid.UUID,
        x_tank_key: str = Header(default=""),
        x_tervory_admin: str = Header(default=""),
    ):
        actor = founder(x_tank_key, x_tervory_admin)
        # founder_get scopes by actor; bind before lookup. Tenant comes from signed header.
        await enforce_founder_personal(
            request, method="GET", path=f"/founder/records/{id}", key=x_tank_key,
            tenant=request.headers.get("X-Tervory-Tenant") or None, user=actor,
        )
        r = records()
        if r is None:
            raise HTTPException(503, "memory store unreachable")
        try:
            row = await r.founder_get(id, user_id=actor)
        except ValueError:
            raise HTTPException(409, "Memory record conflict") from None
        if row is None:
            raise HTTPException(404, "not found")
        return row

    @app.post("/founder/memories")
    async def remember(
        request: Request,
        body: FounderMemory,
        x_tank_key: str = Header(default=""),
        x_tervory_admin: str = Header(default=""),
    ):
        actor = founder(x_tank_key, x_tervory_admin)
        await enforce_founder_personal(
            request, method="POST", path="/founder/memories", key=x_tank_key,
            tenant=body.tenant_id, user=actor,
        )
        try:
            return await ready(body.agent).save(
                body.agent,
                str(body.tenant_id),
                body.claim,
                body.evidence,
                confidence=body.confidence,
                last_verified=body.last_verified,
                row_id=str(body.id),
                meta={"user_id": actor},
            )
        except ClaimTooLong as exc:
            raise claim_validation_error("claim", exc) from None
        except ValueError:
            raise HTTPException(409, "Memory record conflict") from None
        except RuntimeError:
            raise HTTPException(503, "memory store unreachable") from None

    @app.post("/founder/records/{id}/withdraw")
    async def withdraw(
        request: Request,
        id: uuid.UUID, body: Scoped,
        x_tank_key: str = Header(default=""),
        x_tervory_admin: str = Header(default=""),
    ):
        actor = founder(x_tank_key, x_tervory_admin)
        await enforce_founder_personal(
            request, method="POST", path=f"/founder/records/{id}/withdraw", key=x_tank_key,
            tenant=body.tenant_id, user=actor,
        )
        try:
            return await Publisher(ready(body.agent)).withdraw(body.agent, str(body.tenant_id), str(id), actor)
        except ValueError:
            raise HTTPException(409, "Memory record conflict") from None

    @app.post("/founder/publish-policy")
    async def policy(
        request: Request,
        body: Policy,
        x_tank_key: str = Header(default=""),
        x_tervory_admin: str = Header(default=""),
    ):
        actor = founder(x_tank_key, x_tervory_admin)
        await enforce_founder_personal(
            request, method="POST", path="/founder/publish-policy", key=x_tank_key,
            tenant=body.tenant_id, user=actor,
        )
        try:
            return await Publisher(ready(body.agent)).set_policy(
                body.agent,
                str(body.tenant_id),
                actor,
                body.enabled,
                body.identifiers.model_dump() if body.identifiers else {},
            )
        except ValueError:
            raise HTTPException(422, "Reviewed identity inventory required") from None

    @app.post("/founder/publish")
    async def publish(
        request: Request,
        body: Publication,
        x_tank_key: str = Header(default=""),
        x_tervory_admin: str = Header(default=""),
    ):
        actor = founder(x_tank_key, x_tervory_admin)
        # Bind before ownership lookup/state change — admin header alone is not enough.
        await enforce_founder_personal(
            request, method="POST", path="/founder/publish", key=x_tank_key,
            tenant=body.tenant_id, user=actor,
        )
        result = await Publisher(ready(body.agent)).publish(
            body.agent, str(body.tenant_id), str(body.source_row), actor
        )
        if result["result"] not in ("published", "already_published"):
            raise HTTPException(403 if result["result"] == "disabled" else 409, result)
        return result
