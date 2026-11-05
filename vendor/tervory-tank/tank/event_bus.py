"""Tenant/event Redis Streams. No model calls, client text or room Redis access."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from datetime import date as SourceDate
import json
import logging
from typing import Literal
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from redis.asyncio import Redis
from redis.exceptions import ResponseError

EVENTS = {
    "base.record.enriched": "base", "tiger.price.changed": "tiger",
    "marketlab.signal.scored": "market_lab", "zenas.regulation.changed": "zenas",
    "radix.finding": "radix", "tisan.build.ready": "tisan",
}
LIMIT = 10000
MAX_TRIES = 5
RETRY_MS = 30000
DEDUP_SECONDS = 7 * 86400
log = logging.getLogger(__name__)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BaseFact(Strict):
    record_id: UUID
    task_id: UUID
    confidence: int = Field(ge=0, le=100)
    observed_at: AwareDatetime


class TigerFact(Strict):
    watch_id: UUID
    observation_id: UUID
    sku: str | None = Field(default=None, min_length=1, max_length=120)
    page_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous_price: Decimal = Field(ge=0)
    price: Decimal = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    observed_at: AwareDatetime


class MarketFact(Strict):
    signal_id: UUID
    score: Decimal
    rank: int = Field(ge=1)
    observed_at: AwareDatetime


class ZenasFact(Strict):
    document_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    version_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class WatchZenasFact(Strict):
    source: Literal["federal-register", "ecfr", "congress", "legiscan", "eur-lex", "laws-africa"]
    document_id: str = Field(min_length=1, max_length=512)
    date: SourceDate
    link: str = Field(max_length=4096)

    @field_validator("link")
    @classmethod
    def official_link(cls, value, info):
        from urllib.parse import urlsplit, parse_qsl
        hosts = {
            "federal-register": {"www.federalregister.gov", "federalregister.gov"},
            "ecfr": {"www.ecfr.gov", "ecfr.gov"}, "congress": {"www.congress.gov", "congress.gov"},
            "legiscan": {"legiscan.com"}, "eur-lex": {"eur-lex.europa.eu"},
            "laws-africa": {"api.laws.africa", "edit.laws.africa", "lawlibrary.org.za", "laws.africa"},
        }
        p = urlsplit(value)
        if (p.scheme != "https" or p.hostname not in hosts.get(info.data.get("source"), set())
                or p.username or p.password or p.port not in (None,443)
                or any(c.isspace() for c in value)
                or any(k.lower() in {"key","api_key","token","password","secret"} for k,_ in parse_qsl(p.query))):
            raise ValueError("Invalid source link")
        return value


class _ZenasFacts:
    @classmethod
    def model_validate(cls, facts):
        return TypeAdapter(ZenasFact | WatchZenasFact).validate_python(facts)


class RadixFact(Strict):
    scan_id: UUID
    ordinal: int = Field(ge=0)
    severity: Literal["critical", "high", "medium", "low", "info", "unknown"]


class TisanFact(Strict):
    job_id: UUID
    site_id: str = Field(pattern=r"^[a-f0-9]{64}$")


FACTS = dict(zip(EVENTS, (BaseFact, TigerFact, MarketFact, _ZenasFacts, RadixFact, TisanFact)))


@dataclass(frozen=True)
class Scope:
    tenant: UUID
    agent: str
    region: str

    def stream(self, event: str) -> str:
        if event not in EVENTS:
            raise ValueError("Unknown event")
        return f"tervory:{self.tenant}:{event}"


# Atomic producer deduplication; a lost HTTP response can safely be retried.
PUBLISH = """
local old=redis.call('GET',KEYS[2])
if old then return old end
local id=redis.call('XADD',KEYS[1],'MAXLEN','=',ARGV[1],'*',
    'event_id',ARGV[2],'region',ARGV[3],'facts',ARGV[4])
redis.call('SET',KEYS[2],id,'EX',ARGV[5])
return id
"""
# Only the consumer owning this tenant/group's pending delivery can settle it.
# XPENDING deliveries include abandoned claims, so crashes cannot retry forever.
SETTLE = """
local p=redis.call('XPENDING',KEYS[1],ARGV[1],ARGV[3],ARGV[3],1)
if #p==0 or p[1][2]~=ARGV[2] then return 0 end
if ARGV[4]=='ack' then return redis.call('XACK',KEYS[1],ARGV[1],ARGV[3]) end
if tonumber(p[1][4])<tonumber(ARGV[5]) then return 1 end
local entry=redis.call('XRANGE',KEYS[1],ARGV[3],ARGV[3])
if #entry>0 then
  redis.call('XADD',KEYS[2],'MAXLEN','=',ARGV[6],'*','group',ARGV[1],
    'original_id',ARGV[3],'attempts',p[1][4],unpack(entry[1][2]))
end
redis.call('XACK',KEYS[1],ARGV[1],ARGV[3])
return 2
"""


class Bus:
    def __init__(self, redis, *, maxlen=LIMIT, retry_ms=RETRY_MS):
        self.redis, self.maxlen, self.retry_ms = redis, maxlen, retry_ms

    async def publish(self, scope: Scope, event: str, source_id: UUID, facts: dict) -> str:
        if EVENTS.get(event) != scope.agent:
            raise PermissionError
        safe = FACTS[event].model_validate(facts).model_dump(mode="json")
        event_id = str(uuid5(NAMESPACE_URL, f"{scope.tenant}:{event}:{source_id}"))
        key = scope.stream(event)
        await self.redis.eval(PUBLISH, 2, key, key + ":seen:" + event_id,
                              self.maxlen, event_id, scope.region, json.dumps(safe), DEDUP_SECONDS)
        return event_id

    async def receive(self, scope: Scope, event: str, consumer: str) -> list[dict]:
        if (scope.agent, event) not in {("rig", "tiger.price.changed"), ("radix", "zenas.regulation.changed")}:
            raise PermissionError
        key = scope.stream(event)
        try:
            await self.redis.xgroup_create(key, scope.agent, id="0-0", mkstream=True)
        except ResponseError as exc:
            if not str(exc).startswith("BUSYGROUP"):
                raise
        cursor = "0-0"
        while True:
            claimed = await self.redis.xautoclaim(key, scope.agent, consumer, self.retry_ms, cursor, count=20)
            cursor, rows = claimed[:2]
            # Redis scans at most COUNT * 10 pending entries per call. An
            # empty page can precede abandoned work owned by another worker.
            if rows or cursor == "0-0":
                break
        if not rows:
            fresh = await self.redis.xreadgroup(scope.agent, consumer, {key: ">"}, count=20)
            rows = fresh[0][1] if fresh else []
        messages = []
        for message_id, data in rows:
            pending = await self.redis.xpending_range(key, scope.agent, message_id, message_id, 1)
            attempts = pending[0]["times_delivered"] if pending else 0
            if attempts > MAX_TRIES or data.get("region") != scope.region:
                await self.settle(scope, event, consumer, message_id, "fail")
                continue
            messages.append({"id": message_id, "event_id": data["event_id"], "attempt": attempts,
                             "facts": json.loads(data["facts"])})
        return messages

    async def settle(self, scope: Scope, event: str, consumer: str, message_id: str, action: str) -> int:
        if (scope.agent, event) not in {("rig", "tiger.price.changed"), ("radix", "zenas.regulation.changed")} or action not in {"ack", "fail"}:
            raise PermissionError
        key = scope.stream(event)
        return await self.redis.eval(SETTLE, 2, key, key + ":dead", scope.agent,
                                     consumer, message_id, action, MAX_TRIES, self.maxlen)


_redis = None


def bus() -> Bus:
    global _redis
    if _redis is None:
        from main import REDIS_URL
        # Same Redis as Tank. This pool reconnects even if cache startup failed.
        _redis = Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=1, socket_connect_timeout=1)
    return Bus(_redis)


@asynccontextmanager
async def lifespan(app):
    yield
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


router = APIRouter(prefix="/v1/events", lifespan=lifespan)


async def identity(request: Request) -> Scope:
    from main import box_region, require_key
    agent = await require_key(request, request.headers.get("x-tank-key", ""))
    # This is a private room corridor. Workspace/client keys cannot consume.
    if agent not in {*EVENTS.values(), "rig"}:
        raise HTTPException(403)
    try:
        tenant = UUID(request.headers["x-tervory-tenant"])
    except (KeyError, ValueError):
        raise HTTPException(401) from None
    region = request.headers.get("x-tervory-region")
    if region != box_region():
        raise HTTPException(403)
    # Trusted room header comes from its stored job or own catalogue tenant,
    # never event facts, query strings, source IDs or a browser's message.
    return Scope(tenant, agent, region)


class Publication(Strict):
    event: str
    source_id: UUID
    facts: dict


class Delivery(Strict):
    event: Literal["tiger.price.changed", "zenas.regulation.changed"]
    consumer: UUID


class Settlement(Delivery):
    id: str = Field(pattern=r"^\d{1,20}-\d{1,20}$")
    action: Literal["ack", "fail"]


@router.post("/publish")
async def publish(body: Publication, actor: Scope = Depends(identity)):
    if EVENTS.get(body.event) != actor.agent:
        raise HTTPException(403)
    try:
        safe = FACTS[body.event].model_validate(body.facts).model_dump(mode="json")
    except ValueError:
        raise HTTPException(422) from None
    try:
        return {"published": True, "event_id": await bus().publish(actor, body.event, body.source_id, safe)}
    except Exception:
        log.warning("Event publication unavailable; product work continues")
        return {"published": False}


@router.post("/receive")
async def receive(body: Delivery, actor: Scope = Depends(identity)):
    if (actor.agent, body.event) not in {("rig", "tiger.price.changed"), ("radix", "zenas.regulation.changed")}:
        raise HTTPException(403)
    try:
        return {"messages": await bus().receive(actor, body.event, str(body.consumer))}
    except Exception:
        raise HTTPException(503) from None


@router.post("/settle")
async def settle(body: Settlement, actor: Scope = Depends(identity)):
    if (actor.agent, body.event) not in {("rig", "tiger.price.changed"), ("radix", "zenas.regulation.changed")}:
        raise HTTPException(403)
    try:
        return {"settled": await bus().settle(actor, body.event, str(body.consumer), body.id, body.action)}
    except Exception:
        raise HTTPException(503) from None
