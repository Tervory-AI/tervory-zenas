"""Zenas — drafts and research for a licensed lawyer to review. No criminal law."""

from __future__ import annotations

import logging
import hmac
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

_HERE = Path(__file__).resolve().parent
if (_HERE / "agents" / "zenas" / "pool").is_dir():
    _ROOT = _HERE
elif (_HERE.parent.parent / "agents" / "zenas" / "pool").is_dir():
    _ROOT = _HERE.parent.parent
else:
    _ROOT = _HERE
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_CHASSIS = _ROOT / "agents" / "_chassis"
if _CHASSIS.is_dir() and str(_CHASSIS) not in sys.path:
    sys.path.insert(0, str(_CHASSIS))

from agents.zenas.pool.api import interrupt_abandoned_loads  # noqa: E402
from agents.zenas.pool.api import register_routes as register_pool_routes  # noqa: E402
from agents.zenas.pool.catalogue import CATALOGUE  # noqa: E402
from agents.zenas.pool.region import RegionError, assert_box_region  # noqa: E402
from agents.zenas.pool.store import PostgresStore  # noqa: E402
from chassis.app import create_app  # noqa: E402
from chassis.jobs import JobContext  # noqa: E402
from fastapi import HTTPException, Header, Query  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agents.zenas.core.ladder_store import PgLadder  # noqa: E402
from agents.zenas.core.matters import (  # noqa: E402
    open_matter,
    record_consent,
    record_review,
    upload_document,
)
from agents.zenas.core.seal import key_from_env  # noqa: E402
from agents.zenas.core.service import (  # noqa: E402
    add_approved_clause,
    add_playbook,
    answer_question,
    apply_law,
    approve_rule_card,
    build_rule_card,
    check_citations,
    draft_contract,
    BODY_TEXT_REFUSED,
    extract_case_facts,
    judgment_from_pool_row,
    ladder_metrics,
    mark_stale,
    match_matters,
    plain_language,
    read_rule_card,
    release_matter,
    retrieve,
    review_contract,
    review_queue,
    write_memo,
)
from agents.zenas.core.convert import begin_convert, conversion_report, convert_cases, convert_running  # noqa: E402
from agents.zenas.core.selftest import list_scores, run_selftest  # noqa: E402
from agents.zenas.core.store import PgStore, register_home  # noqa: E402
from agents.zenas.gold_sources import candidates as gold_candidates  # noqa: E402

AGENT = os.environ.get("AGENT_NAME", "zenas")
HEARTBEAT_TENANT = "00000000-0000-0000-0000-000000000001"

log = logging.getLogger("agent")


async def ping(ctx: JobContext) -> dict:
    result: dict = {"pong": True, "agent": ctx.agent}
    if ctx.input.get("heartbeat"):
        try:
            saved = await ctx.memory.remember(
                ctx.tenant_id,
                f"{ctx.agent} heartbeat",
                "heartbeat",
                "schedule:*/5",
            )
            result["heartbeats_seen"] = saved.get("seen")
        except Exception as exc:  # noqa: BLE001 — class name only, never the message
            log.warning("heartbeat memory failed (%s)", type(exc).__name__)
            result["memory"] = "unreachable"
    return result


def _box_region() -> str:
    """This room's region. Missing or unknown is refused. There is no us-east default."""
    raw = os.environ.get("TANK_REGION", "").strip().lower()
    try:
        return assert_box_region(raw or None)
    except RegionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _store() -> PgStore:
    return PgStore(app.state.pool)


def _ladder() -> PgLadder:
    return PgLadder(app.state.pool)


class _TankModel:
    """Model calls go through Tank. This box never holds a provider key."""

    def __init__(self, tank: Any, tenant_id: str) -> None:
        self.tank = tank
        self.tenant_id = tenant_id

    async def complete(self, messages: list[dict[str, str]], *, task_type: str, provider: str) -> dict[str, Any]:
        del provider
        data = await self.tank.route(self.tenant_id, task_type, messages)
        return {
            "content": str(data.get("content") or ""),
            "cost_usd": float(data.get("cost_usd") or 0),
            "model": str(data.get("model") or ""),
        }


def _model(tenant_id: str) -> _TankModel:
    return _TankModel(app.state.tank, tenant_id)


def _fail(result: dict[str, Any]) -> None:
    if result.get("ok"):
        return
    reason = str(result.get("reason") or "refused")
    status = 403 if "region" in reason else 422
    raise HTTPException(status_code=status, detail=reason)


async def open_matter_job(ctx: JobContext) -> dict:
    store = _store()
    await store.apply_schema()
    result = await open_matter(
        store,
        ctx.tank,
        tenant_id=ctx.tenant_id,
        home_region=str(ctx.input.get("home_region") or ""),
        jurisdiction=str(ctx.input.get("jurisdiction") or ""),
        title=str(ctx.input.get("title") or ""),
        created_by=str(ctx.input.get("created_by") or ""),
        user_id=str(ctx.input.get("user_id") or ""),
        box_region=_box_region(),
        parties=ctx.input.get("parties") if isinstance(ctx.input.get("parties"), list) else [],
        key=key_from_env(),
    )
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason")}
    matter = result["matter"]
    return {"ok": True, "matter_id": matter["id"], "area": matter["area"]}


async def upload_job(ctx: JobContext) -> dict:
    store = _store()
    await store.apply_schema()
    result = await upload_document(
        store,
        ctx.tank,
        tenant_id=ctx.tenant_id,
        matter_id=str(ctx.input.get("matter_id") or ""),
        kind=str(ctx.input.get("kind") or ""),
        body=str(ctx.input.get("body") or ""),
        box_region=_box_region(),
        home_region=str(ctx.input.get("home_region") or ""),
        key=key_from_env(),
        sources=ctx.input.get("sources") if isinstance(ctx.input.get("sources"), list) else [],
    )
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason")}
    document = result["document"]
    return {"ok": True, "document_id": document["id"], "version": document["version"]}


app = create_app(
    agent=AGENT,
    handlers={"ping": ping, "open_matter": open_matter_job, "upload": upload_job},
    schedules=[
        {
            "tenant_id": HEARTBEAT_TENANT,
            "cron": "*/5 * * * *",
            "kind": "ping",
            "input": {"heartbeat": True},
        }
    ],
)


@app.get("/gold/candidates")
async def get_gold_candidates(
    limit: int = Query(default=200, ge=0, le=200),
    x_tank_key: str = Header(default=""),
) -> list[dict[str, Any]]:
    expected = os.environ.get("TANK_KEY", "")
    if not expected or not hmac.compare_digest(x_tank_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="room key required")
    return await gold_candidates(app.state.pool, region=_box_region(), limit=limit)


class ConsentIn(BaseModel):
    tenant_id: UUID
    home_region: str
    user_id: str
    jurisdiction: str
    wording: str
    ip: str


class MatterIn(BaseModel):
    tenant_id: UUID
    home_region: str
    jurisdiction: str
    title: str
    created_by: str
    user_id: str = ""
    parties: list[Any] = Field(default_factory=list)


class DocumentIn(BaseModel):
    tenant_id: UUID
    home_region: str
    kind: str
    body: str
    sources: list[Any] = Field(default_factory=list)


class ReviewIn(BaseModel):
    tenant_id: UUID
    reviewer: str
    jurisdiction: str
    admission_no: str
    decision: str
    changes: str = ""


@app.get("/pool")
async def pool_import() -> dict[str, Any]:
    return {"imported": True, "sources": len(CATALOGUE)}


_pool_schema_ready = False


@asynccontextmanager
async def _pool_store():
    """One pooled connection per request, bound to this box. Schema applied once."""
    global _pool_schema_ready
    async with app.state.pool.acquire() as conn:
        store = PostgresStore(conn, home_region=_box_region())
        if not _pool_schema_ready:
            await store.apply_schema()
            _pool_schema_ready = True
        yield store


# Feeds switchboard: pool status and "Load now" per source (Tank proxies /v1/zenas/pool/*).
register_pool_routes(app, _pool_store, _box_region)

_inner_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _room_lifespan(application):
    """After the chassis pool is up, a load still marked running was cut off."""
    async with _inner_lifespan(application):
        raw = os.environ.get("TANK_REGION", "").strip().lower()
        try:
            assert_box_region(raw or None)
        except RegionError:
            yield
            return
        try:
            async with _pool_store() as store:
                await interrupt_abandoned_loads(store)
        except Exception as exc:
            log.warning("could not mark interrupted loads: %s", type(exc).__name__)
        yield


app.router.lifespan_context = _room_lifespan


@app.post("/consents", status_code=201)
async def post_consent(body: ConsentIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await record_consent(
        store,
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        user_id=body.user_id,
        jurisdiction=body.jurisdiction,
        wording=body.wording,
        ip=body.ip,
        box_region=_box_region(),
    )
    _fail(result)
    return result


@app.post("/matters", status_code=201)
async def post_matter(body: MatterIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await open_matter(
        store,
        app.state.tank,
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        jurisdiction=body.jurisdiction,
        title=body.title,
        created_by=body.created_by,
        user_id=body.user_id,
        box_region=_box_region(),
        parties=body.parties,
        key=key_from_env(),
    )
    _fail(result)
    return result


@app.post("/matters/{matter_id}/documents", status_code=201)
async def post_document(matter_id: UUID, body: DocumentIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await upload_document(
        store,
        app.state.tank,
        tenant_id=str(body.tenant_id),
        matter_id=str(matter_id),
        kind=body.kind,
        body=body.body,
        box_region=_box_region(),
        home_region=body.home_region,
        key=key_from_env(),
        sources=body.sources,
    )
    _fail(result)
    public = dict(result)
    public.pop("prompt", None)
    return public


@app.post("/documents/{document_id}/reviews", status_code=201)
async def post_review(document_id: UUID, body: ReviewIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await record_review(
        store,
        tenant_id=str(body.tenant_id),
        document_id=str(document_id),
        reviewer=body.reviewer,
        jurisdiction=body.jurisdiction,
        admission_no=body.admission_no,
        decision=body.decision,
        changes=body.changes,
    )
    _fail(result)
    return result


class RetrieveIn(BaseModel):
    tenant_id: UUID
    home_region: str
    jurisdiction: str = ""
    query: str = ""
    citation: str = ""
    as_at: str | None = None
    level: str | None = None
    kind: str | None = None
    corpus: list[Any] = Field(default_factory=list)


class CiteIn(BaseModel):
    tenant_id: UUID
    home_region: str
    jurisdiction: str = ""
    statements: list[Any] = Field(default_factory=list)
    passages: list[Any] = Field(default_factory=list)


class RuleCardIn(BaseModel):
    tenant_id: UUID
    home_region: str
    jurisdiction: str
    question: str
    as_at: str | None = None
    client_facts: str = ""
    passages: list[Any] = Field(default_factory=list)
    provider: str = "luna"


class ApproveCardIn(BaseModel):
    tenant_id: UUID
    home_region: str
    reviewer: str
    admission_no: str
    jurisdiction: str
    passages: list[Any] = Field(default_factory=list)
    changes: str = ""


class StaleIn(BaseModel):
    doc_id: str
    version_id: str | None = None
    event: str = "version"


class CaseFactsIn(BaseModel):
    tenant_id: UUID
    home_region: str
    jurisdiction: str
    pool_doc_id: str
    pool_version_id: str = ""
    citation: str = ""
    source_id: str = ""
    judgment: str
    rule_card_ids: list[str] = Field(default_factory=list)
    as_at: str | None = None
    provider: str = "luna"


class MatchIn(BaseModel):
    tenant_id: UUID
    home_region: str
    jurisdiction: str
    rule_card_ids: list[str] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    narrative: str = ""
    question: str = ""
    sharing: str | None = None


class ApplyIn(BaseModel):
    tenant_id: UUID
    home_region: str
    card_id: UUID
    features: dict[str, Any] = Field(default_factory=dict)
    narrative: str = ""
    question: str = ""
    sharing: str | None = None
    provider: str = "luna"
    smooth: bool = True


class PlainIn(BaseModel):
    tenant_id: UUID
    home_region: str
    text: str
    citations: list[str] = Field(default_factory=list)
    jurisdiction: str = ""
    provider: str = "luna"


class ReleaseIn(BaseModel):
    tenant_id: UUID
    home_region: str
    document_id: UUID
    reviewer: str
    admission_no: str
    jurisdiction: str
    rule_card_ids: list[str] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    narrative: str = ""
    element_findings: list[Any] = Field(default_factory=list)
    outcome: str = ""
    memo: str = ""
    lawyer_edits: str = ""
    as_at: str | None = None
    area: str = ""


async def _ready() -> PgLadder:
    await _store().apply_schema()
    return _ladder()


@app.post("/ladder/retrieve")
async def post_retrieve(body: RetrieveIn) -> dict[str, Any]:
    result = await retrieve(
        await _ready(),
        app.state.tank,
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        jurisdiction=body.jurisdiction,
        query=body.query,
        citation=body.citation,
        as_at=body.as_at,
        level=body.level,
        kind=body.kind,
        corpus=body.corpus or None,
        pool=getattr(app.state, "law_pool", None),
    )
    _fail(result)
    return result


@app.post("/ladder/cite-check")
async def post_cite_check(body: CiteIn) -> dict[str, Any]:
    result = await check_citations(
        await _ready(),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        statements=body.statements,
        passages=body.passages,
        tank=app.state.tank,
        jurisdiction=body.jurisdiction or None,
    )
    _fail(result)
    return result


@app.post("/rule-cards", status_code=201)
async def post_rule_card(body: RuleCardIn) -> dict[str, Any]:
    result = await build_rule_card(
        await _ready(),
        app.state.tank,
        _model(str(body.tenant_id)),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        jurisdiction=body.jurisdiction,
        question=body.question,
        passages=body.passages,
        as_at=body.as_at,
        client_facts=body.client_facts,
        provider=body.provider,
    )
    _fail(result)
    return result


@app.get("/rule-cards/{card_id}")
async def get_rule_card(card_id: UUID, tenant_id: UUID, home_region: str) -> dict[str, Any]:
    result = await read_rule_card(
        await _ready(),
        tenant_id=str(tenant_id),
        home_region=home_region,
        box_region=_box_region(),
        card_id=str(card_id),
    )
    _fail(result)
    return result


@app.post("/rule-cards/{card_id}/approve")
async def post_approve_card(card_id: UUID, body: ApproveCardIn) -> dict[str, Any]:
    result = await approve_rule_card(
        await _ready(),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        card_id=str(card_id),
        reviewer=body.reviewer,
        admission_no=body.admission_no,
        jurisdiction=body.jurisdiction,
        passages=body.passages,
        changes=body.changes,
    )
    _fail(result)
    return result


@app.post("/ladder/stale")
async def post_stale(body: StaleIn) -> dict[str, Any]:
    result = await mark_stale(
        await _ready(),
        box_region=_box_region(),
        doc_id=body.doc_id,
        version_id=body.version_id,
        event=body.event,
    )
    _fail(result)
    return result


_POOL_CASE_SQL = """
SELECT source_id, text, citation
FROM zenas_pool.documents
WHERE document_id = $1
  AND kind = 'case'
  AND effective_to IS NULL
  AND status IS DISTINCT FROM 'superseded'
ORDER BY version_id DESC
LIMIT 1
""".strip()


async def _pool_case_row(pool_doc_id: str) -> dict[str, Any] | None:
    """The pool's own row. The request body is not consulted."""
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(_POOL_CASE_SQL, pool_doc_id)
    if row is None:
        return None
    return {"source_id": str(row["source_id"] or ""), "text": str(row["text"] or ""), "citation": str(row["citation"] or "")}


@app.post("/cases/facts")
async def post_case_facts(body: CaseFactsIn) -> dict[str, Any]:
    loaded = judgment_from_pool_row(body.judgment, body.source_id, await _pool_case_row(body.pool_doc_id))
    if not loaded.get("ok"):
        if loaded.get("reason") == BODY_TEXT_REFUSED:
            raise HTTPException(status_code=403, detail=BODY_TEXT_REFUSED)
        _fail(loaded)
    result = await extract_case_facts(
        await _ready(),
        _model(str(body.tenant_id)),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        jurisdiction=body.jurisdiction,
        pool_doc_id=body.pool_doc_id,
        pool_version_id=body.pool_version_id,
        citation=str(loaded.get("citation") or ""),
        source_id=str(loaded.get("source_id") or ""),
        judgment=str(loaded.get("judgment") or ""),
        rule_card_ids=body.rule_card_ids,
        as_at=body.as_at,
        provider=body.provider,
        tank=app.state.tank,
    )
    _fail(result)
    return result


@app.post("/ladder/match")
async def post_match(body: MatchIn) -> dict[str, Any]:
    result = await match_matters(
        await _ready(),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        jurisdiction=body.jurisdiction,
        rule_card_ids=body.rule_card_ids,
        features=body.features,
        narrative=body.narrative,
        question=body.question,
        sharing=body.sharing,
        tank=app.state.tank,
    )
    _fail(result)
    return result


@app.post("/ladder/apply")
async def post_apply(body: ApplyIn) -> dict[str, Any]:
    result = await apply_law(
        await _ready(),
        _model(str(body.tenant_id)),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        card_id=str(body.card_id),
        features=body.features,
        narrative=body.narrative,
        question=body.question,
        sharing=body.sharing,
        provider=body.provider,
        tank=app.state.tank,
    )
    _fail(result)
    return result


@app.post("/ladder/compose")
async def post_compose(body: ApplyIn) -> dict[str, Any]:
    result = await write_memo(
        await _ready(),
        _model(str(body.tenant_id)),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        card_id=str(body.card_id),
        features=body.features,
        narrative=body.narrative,
        question=body.question,
        sharing=body.sharing,
        provider=body.provider,
        tank=app.state.tank,
        smooth=body.smooth,
    )
    _fail(result)
    return result


@app.post("/ladder/plain")
async def post_plain(body: PlainIn) -> dict[str, Any]:
    result = await plain_language(
        await _ready(),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        text=body.text,
        citations=body.citations,
        model=_model(str(body.tenant_id)),
        provider=body.provider,
        tank=app.state.tank,
        jurisdiction=body.jurisdiction or None,
    )
    _fail(result)
    return result


@app.post("/matters/{matter_id}/release", status_code=201)
async def post_release(matter_id: UUID, body: ReleaseIn) -> dict[str, Any]:
    result = await release_matter(
        _store(),
        await _ready(),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        matter_id=str(matter_id),
        document_id=str(body.document_id),
        reviewer=body.reviewer,
        admission_no=body.admission_no,
        jurisdiction=body.jurisdiction,
        rule_card_ids=body.rule_card_ids,
        features=body.features,
        narrative=body.narrative,
        element_findings=body.element_findings,
        outcome=body.outcome,
        memo=body.memo,
        lawyer_edits=body.lawyer_edits,
        as_at=body.as_at,
        area=body.area,
        tank=app.state.tank,
    )
    _fail(result)
    return result


class TenantIn(BaseModel):
    tenant_id: UUID
    home_region: str


@app.post("/tenants", status_code=201)
async def post_tenant(body: TenantIn) -> dict[str, Any]:
    """Record this tenant's home region once. The body must name it. There is no default."""
    store = _store()
    await store.apply_schema()
    result = await register_home(
        store,
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
    )
    _fail(result)
    return result


class ClauseIn(BaseModel):
    tenant_id: UUID
    home_region: str
    jurisdiction: str
    template: str
    kind: str
    text: str
    approved_by: str
    admission_no: str
    fallback_positions: list[str] = Field(default_factory=list)
    notes: str = ""


@app.post("/clauses", status_code=201)
async def post_clause(body: ClauseIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await add_approved_clause(
        store,
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        jurisdiction=body.jurisdiction,
        template=body.template,
        kind=body.kind,
        text=body.text,
        approved_by=body.approved_by,
        admission_no=body.admission_no,
        fallback_positions=body.fallback_positions,
        notes=body.notes,
    )
    _fail(result)
    return result


class PlaybookIn(BaseModel):
    tenant_id: UUID
    home_region: str
    name: str
    template: str
    positions: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/playbooks", status_code=201)
async def post_playbook(body: PlaybookIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await add_playbook(
        store,
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        name=body.name,
        template=body.template,
        positions=body.positions,
    )
    _fail(result)
    return result


class DraftIn(BaseModel):
    tenant_id: UUID
    home_region: str
    template: str
    jurisdiction: str
    terms: dict[str, Any] = Field(default_factory=dict)
    narrative: str = ""
    matter_id: UUID | None = None
    provider: str = "luna"


@app.post("/drafts", status_code=201)
async def post_draft(body: DraftIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await draft_contract(
        store,
        await _ready(),
        _model(str(body.tenant_id)),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        template=body.template,
        jurisdiction=body.jurisdiction,
        terms=body.terms,
        narrative=body.narrative,
        matter_id=str(body.matter_id) if body.matter_id else None,
        provider=body.provider,
    )
    _fail(result)
    return result


class ContractReviewIn(BaseModel):
    tenant_id: UUID
    home_region: str
    playbook_id: UUID
    text: str
    provider: str = "luna"
    matter_id: UUID | None = None


@app.post("/contract-reviews", status_code=200)
async def post_contract_review(body: ContractReviewIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await review_contract(
        store,
        _model(str(body.tenant_id)),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        playbook_id=str(body.playbook_id),
        text=body.text,
        provider=body.provider,
        matter_id=str(body.matter_id) if body.matter_id else None,
    )
    _fail(result)
    return result


@app.get("/review")
async def get_review(tenant_id: UUID, home_region: str, jurisdiction: str = "") -> dict[str, Any]:
    result = await review_queue(
        _store(),
        await _ready(),
        tenant_id=str(tenant_id),
        home_region=home_region,
        box_region=_box_region(),
        jurisdiction=jurisdiction or None,
    )
    _fail(result)
    return result


class AnswerIn(BaseModel):
    tenant_id: UUID
    home_region: str
    jurisdiction: str
    question: str
    passages: list[dict[str, Any]] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    matter_id: UUID | None = None
    provider: str = "luna"


@app.post("/answers", status_code=200)
async def post_answer(body: AnswerIn) -> dict[str, Any]:
    store = _store()
    await store.apply_schema()
    result = await answer_question(
        store,
        await _ready(),
        _model(str(body.tenant_id)),
        tenant_id=str(body.tenant_id),
        home_region=body.home_region,
        box_region=_box_region(),
        jurisdiction=body.jurisdiction,
        question=body.question,
        passages=body.passages,
        features=body.features,
        matter_id=str(body.matter_id) if body.matter_id else None,
        provider=body.provider,
        tank=app.state.tank,
    )
    if result.get("reason") == "could not verify":
        return result
    _fail(result)
    return result


class ReviewSwitchIn(BaseModel):
    home_region: str
    review: str


@app.get("/review-switch")
async def get_review_switch(home_region: str) -> dict[str, Any]:
    ladder = await _ready()
    from agents.zenas.core.review_switch import review_required

    box = _box_region()
    if home_region != box:
        raise HTTPException(status_code=403, detail="wrong region")
    on = await review_required(ladder, region=box)
    return {"ok": True, "region": box, "review": "on" if on else "off"}


@app.post("/review-switch")
async def post_review_switch(body: ReviewSwitchIn) -> dict[str, Any]:
    ladder = await _ready()
    box = _box_region()
    if body.home_region != box:
        raise HTTPException(status_code=403, detail="wrong region")
    if body.review not in ("on", "off"):
        raise HTTPException(status_code=422, detail="review is on or off")
    await ladder.set_review(region=box, review=body.review)
    return {"ok": True, "region": box, "review": body.review}


class ConvertIn(BaseModel):
    home_region: str


@app.get("/cases/convert")
async def get_cases_convert(home_region: str) -> dict[str, Any]:
    """Counts for this region: decided cases loaded, converted, held out, failed."""
    box = _box_region()
    if home_region != box:
        raise HTTPException(status_code=403, detail="wrong region")
    ladder = await _ready()
    async with _pool_store() as pool:
        report = await conversion_report(pool, ladder, region=box)
    report["running"] = convert_running(box)
    return report


@app.post("/cases/convert")
async def post_cases_convert(body: ConvertIn) -> dict[str, Any]:
    """Start this region's conversion and return at once. The other region's cases are not read."""
    box = _box_region()
    if body.home_region != box:
        raise HTTPException(status_code=403, detail="wrong region")

    async def runner() -> None:
        ladder = await _ready()
        async with _pool_store() as pool:
            await convert_cases(pool, ladder, _model(HEARTBEAT_TENANT), region=box)

    started = await begin_convert(box, runner)
    ladder = await _ready()
    async with _pool_store() as pool:
        report = await conversion_report(pool, ladder, region=box)
    report["running"] = bool(started["running"])
    report["status"] = started["status"]
    return report


@app.get("/selftest")
async def get_selftest(home_region: str) -> dict[str, Any]:
    ladder = await _ready()
    box = _box_region()
    if home_region != box:
        raise HTTPException(status_code=403, detail="wrong region")
    return await list_scores(ladder, region=box)


@app.post("/selftest")
async def post_selftest(home_region: str) -> dict[str, Any]:
    ladder = await _ready()
    box = _box_region()
    if home_region != box:
        raise HTTPException(status_code=403, detail="wrong region")
    return await run_selftest(ladder, region=box)


@app.get("/ladder/metrics")
async def get_metrics(tenant_id: UUID, home_region: str) -> dict[str, Any]:
    result = await ladder_metrics(
        await _ready(),
        tenant_id=str(tenant_id),
        home_region=home_region,
        box_region=_box_region(),
    )
    _fail(result)
    return result
