"""Workspace's tenant-scoped Market Lab corridor. It never routes a model call."""
import os
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class MarketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    question: str = Field(default="", max_length=2000)
    document_id: UUID | None = None
    offset: int = Field(default=0, ge=0, le=10000000)
    row: int | None = Field(default=None, ge=0, le=10000000)
    row_kind: Literal["point", "event"] | None = None


def build_router(tank):
    router = APIRouter()
    _warning_routes(router, tank)
    _research_route(router, tank)
    from market_lab_ml3 import build_router as ml3_router
    router.include_router(ml3_router(tank))

    @router.post("/v1/market-lab/{operation}", dependencies=[Depends(tank.require_key)])
    async def call(operation: str, body: MarketRequest):
        if operation not in {"overview", "ask", "document", "refresh"}:
            raise HTTPException(404, "Not found")
        key = os.environ.get("TANK_KEY_MARKET_LAB", "")
        if not key or tank._http is None:
            raise HTTPException(503, "Market Lab is not connected")
        try:
            response = await tank._http.post(
                "http://agent-market-lab:8000/market/" + operation,
                json=body.model_dump(mode="json"), headers={"x-tank-key": key},
                timeout=180 if operation == "ask" else 30,
            )
            if response.status_code >= 400:
                status = response.status_code if response.status_code in {404, 422, 503} else 502
                raise HTTPException(status, "Market Lab could not complete this request")
            return JSONResponse(response.json(), headers={"Cache-Control": "private, no-store"})
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, "Market Lab could not complete this request") from None

    return router


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    home_region: str = Field(min_length=1, max_length=50)


def _research_route(router, tank):
    from fastapi import Header
    import hmac

    async def room_only(x_tank_key: str = Header(default="")):
        key = os.environ.get("TANK_KEY_MARKET_LAB", "")
        if not key or not x_tank_key or not hmac.compare_digest(key.encode(), x_tank_key.encode()):
            raise HTTPException(401, "Room key required")

    @router.post("/v1/market-lab/internal/research", dependencies=[Depends(room_only)])
    async def research(body: ResearchRequest):
        region = tank.box_region()
        refused = tank.home_region_refusal(tank.call_home(body.home_region, region), region)
        if refused:
            raise HTTPException(403, refused)
        if tank._http is None:
            raise HTTPException(503, "Market Lab is not connected")
        try:
            response = await tank._http.post("http://market-research:8000/research",
                json=body.model_dump(mode="json"), timeout=160,
                headers={"X-Tank-Key": os.environ["TANK_KEY_MARKET_LAB"]})
            response.raise_for_status()
            return JSONResponse(response.json(), headers={"Cache-Control": "private, no-store"})
        except Exception:
            raise HTTPException(503, "Market Lab could not complete this request") from None

    @router.post("/v1/market-lab/internal/search", dependencies=[Depends(room_only)])
    async def search(body: ResearchRequest):
        # Feed collection uses our search index directly. No researcher, model,
        # provider credentials or arbitrary caller-selected endpoint is involved.
        import json
        region = tank.box_region()
        if tank.home_region_refusal(tank.call_home(body.home_region, region), region):
            raise HTTPException(403, "This request must stay on its home server.")
        if tank._http is None:
            raise HTTPException(503, "Market Lab is not connected")
        try:
            async with tank._http.stream("GET", "http://searxng:8080/search",
                    params={"q": body.question, "format": "json", "pageno": 1},
                    follow_redirects=False, timeout=45) as response:
                response.raise_for_status()
                raw = bytearray()
                async for part in response.aiter_bytes():
                    raw.extend(part)
                    if len(raw) > 2 * 1024 * 1024:
                        raise ValueError("response limit")
            results = json.loads(raw)["results"]
            if not isinstance(results, list):
                raise ValueError("invalid search results")
            return JSONResponse({"results": [{key: item.get(key, "") for key in ("title", "url", "content")}
                                             for item in results[:100] if isinstance(item, dict)]},
                                headers={"Cache-Control": "private, no-store"})
        except Exception:
            raise HTTPException(503, "Market Lab could not complete this request") from None


class WarningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    warning_id: UUID | None = None
    event_id: UUID | None = None
    useful: StrictBool | None = None
    actor: str = Field(default="", max_length=200)


class WarningInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    event_id: UUID | None = None


def _warning_routes(router, tank):
    from fastapi import Header
    import hmac

    async def room_only(x_tank_key: str = Header(default="")):
        key = os.environ.get("TANK_KEY_MARKET_LAB", "")
        if not key or not x_tank_key or not hmac.compare_digest(key.encode(), x_tank_key.encode()):
            raise HTTPException(401, "Room key required")

    async def room(operation: str, body: dict):
        key = os.environ.get("TANK_KEY_MARKET_LAB", "")
        if not key or tank._http is None:
            raise HTTPException(503, "Warnings are unavailable")
        try:
            response = await tank._http.post("http://agent-market-lab:8000/market/warnings/" + operation,
                json=body, headers={"x-tank-key": key}, timeout=120)
            if response.status_code >= 400:
                raise HTTPException(response.status_code if response.status_code in {404,409,422,503} else 502, "Warnings could not be read or saved")
            return response.json()
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, "Warnings are unavailable") from None

    @router.post("/v1/market-lab/warnings/{operation}", dependencies=[Depends(tank.require_key)])
    async def workspace(operation: str, body: WarningRequest):
        if operation not in {"list", "feedback"}:
            raise HTTPException(404, "Not found")
        return JSONResponse(await room(operation, body.model_dump(mode="json")), headers={"Cache-Control":"private, no-store"})

    @router.post("/v1/market-lab/internal/training", dependencies=[Depends(room_only)])
    async def training(body: WarningInternal):
        from training import save_pair
        if body.event_id is None:
            raise HTTPException(422, "An event is required")
        event = await room("pair", body.model_dump(mode="json"))
        if str(event.get("tenant_id")) != str(body.tenant_id) or str(event.get("id")) != str(body.event_id):
            raise HTTPException(502, "Invalid event owner")
        if tank._pool is None:
            raise HTTPException(503, "Learning records are unavailable")
        async with tank._pool.acquire() as conn, conn.transaction():
            await save_pair(conn, tenant=body.tenant_id, agent="market_lab", task="market.warning." + event["kind"],
                value=event["input"], output=event["output"], approved_by=event["actor"], source="market_lab_warning",
                ref=str(body.event_id), client_matter=True, public=False)
            # Keep feedback for later review. This brief does not activate training.
            await conn.execute("""UPDATE tank.training_pairs SET active=false,public=false,
                polarity=CASE WHEN $3 THEN 'negative' ELSE 'positive' END
                WHERE tenant_id=$1 AND source='market_lab_warning' AND source_ref=$2""", body.tenant_id, str(body.event_id),
                event["output"].get("useful") is False or event["output"].get("outcome") == "miss")
        return {"stored": True}

    @router.post("/v1/market-lab/internal/series", dependencies=[Depends(room_only)])
    async def series(body: WarningInternal):
        # Use the existing Tank-owned Tiger transport and tenant filter. Never
        # expose another room's URL/key, or let the caller select an HTTP target.
        response = await tank._tiger_call("GET", "/watchlist", [("tenant_id", str(body.tenant_id))], None)
        if response.status_code != 200:
            raise HTTPException(503, "Saved product observations are unavailable")
        watches = tank._tiger_keep(response.json(), body.tenant_id).get("watches", [])
        result, unavailable = [], False
        for watch in watches:
            data = await tank._tiger_call("GET", "/observations", [("tenant_id", str(body.tenant_id)), ("url", watch["url"])], None)
            if data.status_code != 200:
                unavailable = True
                continue
            rows = tank._tiger_keep(data.json(), body.tenant_id).get("observations", [])
            result.append({"watch": watch, "observations": [row for row in rows if row.get("url") == watch["url"]]})
        return {"series": result, "problem": "Some watched product histories could not be read. Existing warnings keep their saved evidence; new warnings wait for those observations." if unavailable else None}
