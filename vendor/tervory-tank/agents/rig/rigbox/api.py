"""Public routes from the widget contract. Errors are {error: {code, message}}."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from chassis.tenant_db import TenantContextMiddleware, activate_tenant

from rigbox.service import RigError
from rigbox.quotes.privacy import shopper_body
from rigbox.quotes.limits import QUOTE_ROUTES, client_address

router = APIRouter(prefix="/v1/rig")

_CORS_HEADERS = "Content-Type, X-Rig-Key, Accept"
_CORS_METHODS = "GET, POST, OPTIONS"


class SetupIn(BaseModel):
    product_id: str = Field(min_length=1)


class SearchEventIn(BaseModel):
    event_id: UUID
    no_results: bool


class SwapIn(BaseModel):
    setup: dict[str, Any]
    role: str = ""
    commit: bool = False


class AskIn(BaseModel):
    product_id: str = Field(min_length=1)
    question: str = ""
    setup: dict[str, Any] | None = None


class LinkIn(BaseModel):
    model_config = {"extra": "forbid"}
    url: str = ""
    note: str = ""
    contact: dict[str, Any] | None = None
    notice_version: str | None = Field(default=None, max_length=64)


class QuoteIn(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = Field(default=None, max_length=200)
    request: str | None = Field(default=None, max_length=2000)
    quantity: int | None = Field(default=None, ge=1, le=100000)
    setup: dict[str, Any] | None = None
    product_id: str | None = None
    contact: dict[str, Any] = Field(default_factory=dict)
    notice_version: str | None = Field(default=None, max_length=64)


class CompareIn(BaseModel):
    product_ids: list[str] = Field(min_length=1)


class FitDecision(BaseModel):
    part_sku: str = Field(min_length=1)
    fits_sku: str | None = None
    fits_family: str | None = None


def mount(app: FastAPI) -> None:
    if not any(item.cls is TenantContextMiddleware for item in app.user_middleware):
        app.add_middleware(TenantContextMiddleware)
    app.include_router(router)
    app.add_exception_handler(RigError, _on_rig_error)
    app.add_exception_handler(RequestValidationError, _on_invalid)


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "rig", None)
    if service is None:
        raise RigError("unavailable", "Rig is not ready.", 503)
    return service


def _origin(request: Request) -> str:
    return request.headers.get("origin") or ""


def _cors(response: Response, origin: str, allowed: bool) -> Response:
    if origin and allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = _CORS_HEADERS
    response.headers["Access-Control-Allow-Methods"] = _CORS_METHODS
    response.headers["Access-Control-Expose-Headers"] = "Retry-After"
    return response


async def _auth(request: Request) -> dict[str, Any]:
    if request.method == "OPTIONS":
        return {}
    raw = request.headers.get("x-rig-key") or ""
    address = client_address(request)
    row = await _service(request).authenticate(raw, address)
    origin = _origin(request)
    route = request.scope["route"].path
    if (route in QUOTE_ROUTES and not origin) or (origin and origin not in (row.get("origins") or [])):
        raise RigError("origin", "This shop is not allowed to use that key.", 403)
    request.state.rig_origin = origin
    request.state.rig_origin_ok = bool(origin)
    request.state.tenant_id = row["tenant_id"]
    activate_tenant(row["tenant_id"])
    decision = await _service(request).public_limits.consume(str(row["tenant_id"]), address, route)
    request.state.rig_hold_quote = decision.hold
    if decision.retry_after:
        request.state.rig_retry_after = decision.retry_after
        raise RigError("rate_limit", "Too many requests. Please try again shortly.", 429)
    return row


def _tenant(request: Request) -> str:
    return str(request.state.tenant_id)


async def _admin(request: Request) -> dict[str, Any]:
    raw = request.headers.get("x-rig-key") or ""
    row = await _service(request).authenticate_admin(raw)
    request.state.tenant_id = row["tenant_id"]
    activate_tenant(row["tenant_id"])
    request.state.rig_origin_ok = False
    return row


def _one_target(payload: FitDecision) -> tuple[str, str | None, str | None]:
    part = payload.part_sku.strip()
    sku = (payload.fits_sku or "").strip()
    family = (payload.fits_family or "").strip()
    if not part or bool(sku) == bool(family):
        raise RigError("invalid", "Name the part and either the product or the family it fits.", 422)
    return part, (sku or None), (family or None)


async def _on_rig_error(request: Request, exc: RigError) -> JSONResponse:
    response = JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})
    if exc.status == 429:
        response.headers["Retry-After"] = str(getattr(request.state, "rig_retry_after", 60))
    origin = _origin(request)
    return _cors(response, origin, bool(getattr(request.state, "rig_origin_ok", False) and origin))


async def _on_invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
    if not request.url.path.startswith("/v1/rig"):
        raise exc
    response = JSONResponse(
        status_code=422,
        content={"error": {"code": "invalid", "message": "The request is not valid."}},
    )
    origin = _origin(request)
    return _cors(response, origin, bool(getattr(request.state, "rig_origin_ok", False) and origin))


@router.options("")
@router.options("/{rest:path}")
async def preflight(request: Request, rest: str = "") -> Response:
    del rest
    response = Response(status_code=204)
    return _cors(response, _origin(request), True)


@router.get("/search")
async def search(
    request: Request,
    q: str = "",
    limit: int = 12,
    in_stock: bool = False,
    min_price: float | None = None,
    max_price: float | None = None,
    sort: str = "",
    category: str = "",
) -> Response:
    await _auth(request)
    filters: dict[str, Any] = {}
    if in_stock:
        filters["in_stock"] = True
    if min_price is not None:
        filters["min_price"] = min_price
    if max_price is not None:
        filters["max_price_inclusive"] = max_price
    if sort:
        filters["sort"] = sort
    if category:
        filters["category"] = category
    body = await _service(request).search(_tenant(request), q, limit, filters)
    return _ok(request, body)


class LocateIn(BaseModel):
    model_config = {"extra": "forbid"}
    product_id: str = Field(min_length=1, max_length=200)


@router.post("/locate")
async def locate(request: Request, payload: LocateIn) -> Response:
    await _auth(request)
    body = await _service(request).locate(_tenant(request), payload.product_id)
    return _ok(request, body)


class LocateAcceptIn(BaseModel):
    model_config = {"extra": "forbid"}
    product_id: str = Field(min_length=1, max_length=200)
    hold: str = Field(min_length=1, max_length=200)
    contact: dict[str, Any] = Field(default_factory=dict)
    notice_version: str | None = Field(default=None, max_length=64)
    # Blind ship only: where the other store ships the parcel.
    delivery: dict[str, Any] | None = None


@router.post("/locate/confirm")
async def locate_confirm(request: Request, payload: LocateIn) -> Response:
    """Live re-check of the located offer. Returns a short-lived signed hold."""
    await _auth(request)
    body = await _service(request).locate_confirm(_tenant(request), payload.product_id)
    return _ok(request, body)


@router.post("/locate/accept")
async def locate_accept(request: Request, payload: LocateAcceptIn) -> Response:
    """Accept a held quote and hand it to the store. No order or UI."""
    await _auth(request)
    service = _service(request)
    if not hasattr(service, "locate_accept"):
        raise RigError("unavailable", "Ordering from other stores is not available.", 503)
    body = await service.locate_accept(_tenant(request), payload.model_dump())
    return _ok(request, body)


@router.post("/search-events")
async def settled_search(request: Request, payload: SearchEventIn) -> Response:
    await _auth(request)
    counts = getattr(_service(request), "quote_store", None)
    if counts is None:
        raise RigError("unavailable", "Rig is not ready.", 503)
    accepted = await counts.settled_search(_tenant(request), str(payload.event_id), payload.no_results)
    return _ok(request, {"counted": accepted})


@router.get("/products/{product_id}")
async def product(request: Request, product_id: str) -> Response:
    await _auth(request)
    body = await _service(request).product(_tenant(request), product_id)
    return _ok(request, body)


@router.post("/setups")
async def setups(request: Request, payload: SetupIn) -> Response:
    await _auth(request)
    body = await _service(request).setups(_tenant(request), payload.product_id)
    if body.get("setups"):
        await _count(request, "setups_opened")
    return _ok(request, body)


@router.post("/swap")
async def swap(request: Request, payload: SwapIn) -> Response:
    await _auth(request)
    body = await _service(request).swap(_tenant(request), payload.setup, payload.role)
    if payload.commit:
        await _count(request, "swaps")
    return _ok(request, body)


@router.get("/graph/{product_id}")
async def graph(request: Request, product_id: str) -> Response:
    await _auth(request)
    body = await _service(request).graph(_tenant(request), product_id)
    return _ok(request, body)


@router.post("/compare")
async def compare(request: Request, payload: CompareIn) -> Response:
    await _auth(request)
    body = await _service(request).compare(_tenant(request), payload.product_ids)
    return _ok(request, body)


@router.post("/ask")
async def ask(request: Request, payload: AskIn) -> Response:
    await _auth(request)
    body = await _service(request).ask(_tenant(request), payload.product_id, payload.question, payload.setup)
    return _ok(request, body)


@router.get("/privacy-notice")
async def privacy_notice(request: Request) -> Response:
    await _auth(request)
    from rigbox.quotes.notice import notice

    retailer = await _service(request).store.retailer(_tenant(request)) or {}
    response = _ok(request, {"notice": notice(retailer.get("quote_notice"))})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/link-quote")
async def link_quote(request: Request, payload: LinkIn) -> Response:
    await _auth(request)
    kwargs = {"contact": payload.contact} if payload.contact is not None else {}
    if payload.notice_version is not None:
        kwargs["notice_version"] = payload.notice_version
    if request.state.rig_hold_quote:
        kwargs["hold"] = True
    body = await _service(request).link_quote(_tenant(request), payload.url, payload.note, **kwargs)
    return _ok(request, body)


@router.post("/quote")
async def quote(request: Request, payload: QuoteIn) -> Response:
    await _auth(request)
    body = await _service(request).quote(
        _tenant(request),
        {"setup": payload.setup, "product_id": payload.product_id, "contact": payload.contact,
         "notice_version": payload.notice_version},
        **({"hold": True} if request.state.rig_hold_quote else {}),
    )
    return _ok(request, body)


@router.get("/admin/fits")
async def admin_fits(request: Request, status: str = "suggested") -> Response:
    await _admin(request)
    if status != "suggested":
        raise RigError("invalid", "Ask for suggested fits.", 422)
    body = await _service(request).suggested_fits(_tenant(request))
    return _ok(request, body)


@router.post("/admin/fits/approve")
async def admin_approve(request: Request, payload: FitDecision) -> Response:
    await _admin(request)
    part, sku, family = _one_target(payload)
    ok = await _service(request).approve(_tenant(request), part, sku, family)
    if not ok:
        raise RigError("not_found", "No such fit.", 404)
    return _ok(request, {"approved": True})


@router.post("/admin/fits/reject")
async def admin_reject(request: Request, payload: FitDecision) -> Response:
    await _admin(request)
    part, sku, family = _one_target(payload)
    ok = await _service(request).reject(_tenant(request), part, sku, family)
    if not ok:
        raise RigError("not_found", "No such fit.", 404)
    return _ok(request, {"rejected": True})


@router.post("/admin/fits/suggest")
async def admin_suggest(request: Request) -> Response:
    await _admin(request)
    body = await _service(request).suggest(_tenant(request))
    return _ok(request, body)


def _ok(request: Request, body: dict[str, Any]) -> JSONResponse:
    response = JSONResponse(shopper_body(body))
    return _cors(response, _origin(request), bool(getattr(request.state, "rig_origin_ok", False)))


async def _count(request: Request, event: str) -> None:
    counts = getattr(_service(request), "quote_store", None)
    if counts is not None:
        await counts.count(_tenant(request), event)


class SupplySourceIn(BaseModel):
    model_config = {"extra": "forbid"}
    id: str = Field(min_length=1, max_length=200)
    approved: bool
    priority: int = 0
    max_offer_age_seconds: int = Field(gt=0)
    provider: str = Field(default="", max_length=200)
    site: str = Field(default="", max_length=200)
    feed_id: str = Field(default="", max_length=200)
    # Blind ship: this source must pack a neutral slip (no own branding or invoice).
    neutral_slip_required: bool = False
    # Auto-order: the source's real vendor name and the inbox that takes its orders.
    vendor_name: str = Field(default="", max_length=200)
    order_email: str = Field(default="", max_length=254)


class SupplyImportIn(BaseModel):
    model_config = {"extra": "forbid"}
    source_id: str = Field(min_length=1, max_length=200)
    record: dict[str, Any]
    contents: str = Field(max_length=8_000_000)
    seen_at: str


class SupplyMapIn(BaseModel):
    model_config = {"extra": "forbid"}
    product_sku: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    offer_sku: str = Field(min_length=1, max_length=200)


async def _supply_operation(request: Request, operation: str, payload: BaseModel) -> Response:
    from rigbox.locate import SupplyStore
    await _admin(request)
    store = SupplyStore(_service(request).store)
    data = payload.model_dump()
    try:
        if operation == "source":
            await store.source(_tenant(request), data)
        elif operation == "import":
            return _ok(request, await store.import_catalogue(_tenant(request), **data))
        else:
            await store.map_sku(_tenant(request), **data)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise RigError("invalid", "The request is not valid.", 422) from None
    return _ok(request, {"ok": True})


@router.post("/admin/supply/sources")
async def supply_source(request: Request, payload: SupplySourceIn) -> Response:
    return await _supply_operation(request, "source", payload)


@router.post("/admin/supply/import")
async def supply_import(request: Request, payload: SupplyImportIn) -> Response:
    return await _supply_operation(request, "import", payload)


@router.post("/admin/supply/sku-map")
async def supply_map(request: Request, payload: SupplyMapIn) -> Response:
    return await _supply_operation(request, "map", payload)
