"""Trigger room. Only authenticated same-box Tank calls; no public page."""
from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import asyncpg
import httpx
from chassis.tank import TankClient
from fastapi import Depends, FastAPI, Header, HTTPException

from trigger.service import CheckIn, CheckerIn, WriteIn, check_text, write
from trigger.voice import VoiceStore


async def authorized(x_tank_key: str = Header(default="")):
    expected = os.environ.get("TANK_KEY", "")
    if not expected or not hmac.compare_digest(expected.encode(), x_tank_key.encode()):
        raise HTTPException(401, "Room authentication required")


@asynccontextmanager
async def lifespan(app):
    app.state.pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    await app.state.pool.execute(Path(__file__).with_name("schema.sql").read_text())
    app.state.http = httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False)
    app.state.tank = TankClient("http://tank:8000", os.environ.get("TANK_KEY", ""), "trigger", app.state.http)
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    if not getattr(app.state, "pool", None):
        raise HTTPException(503, "Database unavailable")
    await app.state.pool.fetchval("SELECT 1")
    return {"ok": True, "agent": "trigger"}


@app.post("/check", dependencies=[Depends(authorized)])
async def post_check(body: CheckIn):
    return await check_text(body, VoiceStore(app.state.pool), app.state.http, app.state.tank)


@app.post("/write", dependencies=[Depends(authorized)])
async def post_write(body: WriteIn):
    try:
        return await write(body, VoiceStore(app.state.pool), app.state.tank, app.state.http, app.state.tank)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.post("/skala/languagetool", dependencies=[Depends(authorized)])
async def skala_languagetool(body: CheckerIn):
    """Transport only: the approved package owns rule/result interpretation."""
    import hashlib
    import json
    response = await app.state.http.post(
        "http://trigger-languagetool:8081/v2/check",
        data={"text": body.text, "language": "en-US"}, timeout=30,
    )
    response.raise_for_status()
    config = Path(__file__).with_name("languagetool.properties").read_bytes()
    pins = json.loads((Path(__file__).parent / "sources/pins.json").read_text())
    return {"result": response.json(), "config_sha256": hashlib.sha256(config).hexdigest(),
            "image": pins["languagetool-image"]}

from pydantic import BaseModel, ConfigDict, Field
from trigger.voice_history import VoiceHistory


class VoiceOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    text: str = Field(default="", max_length=12000)
    expected_revision: int = Field(default=0, ge=0)
    preview_hash: str = ""
    operation_id: UUID | None = None
    rules: list[str] | None = Field(default=None, max_length=25)
    target: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)


@app.post("/voice/{operation}", dependencies=[Depends(authorized)])
async def voice_operation(operation: str, body: VoiceOperation):
    store = VoiceHistory(app.state.pool)
    try:
        if operation == "show":
            return await store.show(body.tenant_id, body.offset, body.target)
        if operation == "preview":
            return await store.preview(body.tenant_id, body.text, body.target, body.rules)
        if operation == "commit" and body.operation_id:
            return {"voice": await store.commit(body.tenant_id, text=body.text,
                expected_revision=body.expected_revision, digest=body.preview_hash,
                operation_id=body.operation_id, target=body.target, rules=body.rules)}
        raise HTTPException(404, "Voice operation unavailable")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
