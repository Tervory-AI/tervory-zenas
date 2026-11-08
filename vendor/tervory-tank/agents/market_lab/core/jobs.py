"""Scheduled work for the Market Lab box.

All licensed feeds are stored. Restricted series are never copied. Predictions
are inserted, never updated.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from core.pure import (
    areas_from_series,
    fred_is_link_only,
    gap_score,
    opportunity_rows,
    prediction_for_recommendation,
    research_answer,
    resolve_prediction,
    surge_detect,
)

from core.series import series_fetch

_ENGINE = re.compile(r"^[a-z][a-z0-9_]*$")


def load_engine(name: str) -> Any:
    if not isinstance(name, str) or _ENGINE.fullmatch(name) is None:
        raise ValueError("engine is required")
    return importlib.import_module(f"engines.{name}.engine")


async def perform_refresh(
    *,
    tenant_id: str,
    engine: str,
    ledger: Any,
    fixtures: str | None = None,
    fred_bodies: dict[str, Any] | None = None,
    live: bool = False,
    now: datetime | None = None,
    backfill: bool = False,
    source: str | None = None,
    client: Any = None,
    extra_sources: list | None = None,
) -> dict[str, Any]:
    module = load_engine(engine)
    spec = module.plan()
    clock = _clock(now)
    fetched_at = clock.replace(microsecond=0).isoformat()
    if not fred_is_link_only(engine):
        raise RuntimeError("fred terms allow a copy; refusing to run")
    from core.evidence import save_check
    from feeds.collect import EvidenceClient, collect
    from feeds.live import BlockedSite
    from feeds.catalog import sources

    if not fixtures and not live:
        raise RuntimeError("refusing to open the network")
    feed_plan = set(spec["feeds"]) | set(spec.get("linked_pages", []))
    selected = [item for item in sources(engine) if item.id in feed_plan and (source is None or item.id == source)]
    if extra_sources is not None:
        selected = extra_sources
    if source and not selected:
        raise ValueError("unknown source")
    from urllib.parse import urlsplit
    domains = {".".join((urlsplit(item.url).hostname or "").split(".")[-2:]) for item in sources(engine)}
    http = client or EvidenceClient(allowed_domains=domains)
    public_http = client or EvidenceClient(public_feeds=True, tenant_id=tenant_id)
    stored_rows = 0
    observations_stored = 0
    events = 0
    checks = []
    for feed in selected:
        feed_http = public_http if feed.adapter in {"rss", "comtrade_preview", "searxng"} else http
        result = await asyncio.to_thread(collect, feed, fixtures=fixtures, backfill=backfill or bool(fixtures), client=feed_http)
        grouped: dict[str, list[dict]] = {}
        for point in result["points"]:
            grouped.setdefault(point["series_id"], []).append(point)
        accepted = []
        held = 0
        for raw_id, points in grouped.items():
            first = points[0]
            series_id = f"{tenant_id}:{raw_id}"
            await _call(ledger.save_series, {"id": series_id, "tenant_id": tenant_id,
                        "source": feed.id, "ref": first["ref"], "unit": first["unit"], "engine": engine})
            batch_id = str(uuid.uuid4())
            stamped = [{**point, "source": feed.id, "batch_id": batch_id, "fetched_at": fetched_at} for point in points]
            added = await _call(ledger.save_points, series_id, tenant_id, stamped, stored=True, source=feed.id)
            if hasattr(ledger, "conn"):
                status = await ledger.conn.fetchval(
                    "SELECT status FROM agent_market_lab.batches WHERE tenant_id = $1 AND id = $2",
                    uuid.UUID(str(tenant_id)), batch_id)
            else:
                status = ledger.pool.batches[batch_id]["status"]
            if status != "pooled":
                held += len(points)
                continue
            accepted.extend(stamped)
            observations_stored += added
            if any(feed.id == supply["source"] and first["ref"].startswith(supply["prefix"]) for supply in spec["supply"]):
                stored_rows += added
                surge = surge_detect({"points": points, **spec["surge"]})
                for event in surge["events"]:
                    saved = await _call(ledger.save_event, series_id, tenant_id, event)
                    events += int(bool(saved))
        result["points"] = accepted
        if held:
            result["note"] = "Some observations are held for review"
        if not accepted and not result["events"] and not result["note"]:
            result["note"] = "Off, the source returned no observations"
        checks.append(await save_check(ledger, tenant_id, result, at=_clock(now)))
    fred_read: list[dict[str, Any]] = []
    fred_blocked = False
    for cost in spec["costs"] if extra_sources is None else []:
        if fred_blocked:
            fred_read.append({**_fred_off(str(cost['ref'])), 'note': 'blocked'})
            continue
        body = None if fred_bodies is None else fred_bodies.get(cost["ref"])
        if body is None and live:
            try:
                body = await _live_fred(str(cost["ref"]), pacing=getattr(http, "client", None))
            except BlockedSite:
                fred_read.append({**_fred_off(str(cost["ref"])), "note": "blocked"})
                fred_blocked = True
                continue
            except (RuntimeError, ValueError):
                fred_read.append({**_fred_off(str(cost['ref'])), 'note': 'Off, source could not be read'})
                continue
        if body is None:
            if live:
                fred_read.append(_fred_off(str(cost["ref"])))
            continue
        try:
            fetched = series_fetch(
                {
                    "adapter": "fred",
                    "series_id": cost["ref"],
                    "body": body,
                    "fetched_at": fetched_at,
                }
            )
            if not fetched['points']:
                raise ValueError('empty source response')
        except (ValueError, TypeError, KeyError):
            fred_read.append({**_fred_off(str(cost['ref'])), 'note': 'Off, source could not be read'})
            continue
        if fetched["stored"]:
            raise RuntimeError("fred points were marked for storage")
        await _call(
            ledger.save_points,
            f"{tenant_id}:fred:{cost['ref']}",
            tenant_id,
            fetched["points"],
            stored=False,
            source="fred",
        )
        fred_read.append(
            {
                "ref": cost["ref"],
                "points": len(fetched["points"]),
                "stored": False,
                "source": "fred",
                "state": "on",
                "note": "",
            }
        )
    return {
        "listings_stored": stored_rows,
        "observations_stored": observations_stored,
        "events": events,
        "fred": fred_read,
        "fred_stored": 0,
        "feeds": checks,
    }


async def perform_opportunities(
    *,
    tenant_id: str,
    engine: str,
    ledger: Any,
    areas: list[dict[str, Any]] | None = None,
    signals: dict[str, Any] | None = None,
    memory: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    module = load_engine(engine)
    spec = module.plan()
    clock = _clock(now)
    if areas is None:
        series_rows = await _call(ledger.series_for, tenant_id)
        point_rows = await _call(ledger.points_for, tenant_id)
        # points_for does not include tenant on the row; series id is enough.
        for point in point_rows:
            point.setdefault("series_id", point["series_id"])
        areas = areas_from_series(series_rows, point_rows, spec["bindings"], None)
    scored = gap_score({"areas": areas})
    found = opportunity_rows(scored["ranked"], module.products(), signals or {})
    for row in scored["ranked"]:
        signal_id = str(uuid.uuid4())
        await _call(
            ledger.save_opportunity,
            {
                "id": signal_id,
                "tenant_id": tenant_id,
                "made_at": clock,
                "area": row["area"],
                "segment": row["segment"],
                "demand_idx": row["demand_idx"],
                "supply_idx": row["supply_idx"],
                "gap": row["gap"],
                "saturation": row["saturation"],
                "reachability": row["reachability"],
                "trend": row["trend"],
                "rank": row["rank"],
                "evidence": {
                    "recommendation": f"{row['recommendation']} marketing",
                    "per_capita": row.get("per_capita"),
                    "products": [item for item in found if item["area"] == row["area"]],
                },
            },
        )
        from core.execution import publish
        await publish(tenant_id, "marketlab.signal.scored", signal_id, {
            "signal_id": signal_id, "score": str(row["gap"]), "rank": row["rank"],
            "observed_at": clock.isoformat()})
        await _call(
            ledger.save_prediction,
            prediction_for_recommendation(
                row,
                tenant_id=tenant_id,
                made_at=clock,
                targets=spec.get("response_targets"),
            ),
        )
    from core.brief_sources import claims as brief_claims
    writeup, citations = await brief_claims(ledger, tenant_id, spec["bindings"], scored["ranked"], now=clock)
    from core.briefs import save as save_brief
    await save_brief(ledger, {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id, "made_at": clock,
        "question": "Where is demand outrunning supply?", "answer": writeup, "citations": citations,
    }, now=clock)
    if memory is not None and writeup:
        await memory.remember(tenant_id, writeup, "report", "schedule:opportunities")
    return {
        "top_area": scored["top_area"],
        "top_recommendation": scored["top_recommendation"],
        "ranked": scored["ranked"],
        "products": found,
    }


async def perform_resolve(
    *,
    tenant_id: str,
    ledger: Any,
    points_by_series: dict[str, list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
    only_ids: set[str] | None = None,
) -> dict[str, Any]:
    clock = _clock(now)
    existing = await _call(ledger.predictions_for, tenant_id)
    resolved_ids = set()
    for row in existing:
        for source in row.get("sources") or []:
            if isinstance(source, dict) and source.get("resolves"):
                resolved_ids.add(str(source["resolves"]))
    written = 0
    outcomes: list[dict[str, Any]] = []
    for row in existing:
        is_warning = any(isinstance(source, dict) and source.get("warning_id") for source in row.get("sources") or [])
        if (only_ids is None and is_warning) or (only_ids is not None and str(row["id"]) not in only_ids):
            continue
        if row.get("outcome") or str(row["id"]) in resolved_ids:
            continue
        series_id = str(row["metric_series_id"])
        observed_series_id = series_id
        if points_by_series is not None and series_id in points_by_series:
            points = points_by_series[series_id]
        else:
            points = await _call(ledger.points_for, tenant_id, series_id)
            if not points and series_id.startswith('response:'):
                # An explicitly named, guarded upload can supply the measured
                # response metric. Never infer a different tenant, unit or series.
                candidates = [series for series in await _call(ledger.series_for, tenant_id)
                              if str(series['tenant_id']) == str(tenant_id)
                              and series.get('ref') == series_id
                              and str(series.get('unit', '')).lower() in {'percent', '%'}]
                if len(candidates) == 1:
                    observed_series_id = candidates[0]['id']
                    points = await _call(ledger.points_for, tenant_id, observed_series_id)
        from core.predictions import clock as prediction_clock
        deadline = prediction_clock(row["resolve_by"])
        if clock < deadline:
            continue
        if only_ids is not None:
            # Warning resolution already selects an observation in its measured window.
            observed = [p for p in points if str(p["date"])[:10] <= clock.date().isoformat()]
            selected = max(observed, key=lambda p: str(p["date"])) if observed else None
        else:
            # A stale pre-horizon value is not evidence of the outcome. Wait for
            # the first observation on/after the deadline; never read a future date.
            observed = [p for p in points
                        if deadline.date().isoformat() <= str(p["date"])[:10] <= clock.date().isoformat()]
            selected = min(observed, key=lambda p: str(p["date"])) if observed else None
        if selected is None:
            continue
        nxt = resolve_prediction(row, [{"date": deadline.date().isoformat(), "value": selected["value"]}], clock)
        if nxt is not None:
            nxt.update({key: row.get(key) for key in ("horizon_seconds", "confidence", "kind", "feed")})
            nxt.update(realised_value=float(selected["value"]), realised_date=str(selected["date"])[:10])
            nxt['sources'].append({'metric_series_id': observed_series_id, 'date': str(selected['date'])[:10]})
        if nxt is None:
            continue
        if row.get("statement") != nxt["statement"] or row.get("target") != nxt["target"]:
            raise RuntimeError("resolution changed the prediction")
        await _call(ledger.save_prediction, nxt)
        written += 1
        outcomes.append({"id": nxt["id"], "resolves": row["id"], "outcome": nxt["outcome"], "score": nxt["score"]})
    return {"resolved": written, "outcomes": outcomes}


async def perform_research(
    *,
    tenant_id: str,
    ledger: Any,
    question: str,
    passages: list[dict[str, Any]] | None = None,
    model_text: str | None = None,
    memory: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    clock = _clock(now)
    payload: dict[str, Any] = {"question": question}
    if passages is not None:
        from core.evidence import gated_facts
        payload["passages"] = await gated_facts(ledger, tenant_id, passages, now=clock)
    if model_text is not None:
        payload["model_text"] = model_text
    answer = research_answer(payload)
    report_id = str(uuid.uuid4())
    await _call(
        ledger.save_report,
        {
            "id": report_id,
            "tenant_id": tenant_id,
            "made_at": clock,
            "question": question,
            "answer": answer["answer"],
            "citations": answer["citations"],
        },
    )
    if memory is not None and answer["answer"]:
        source = answer["citations"][0]["url"] if answer["citations"] else f"report:{report_id}"
        await memory.remember(tenant_id, answer["answer"], "report", source)
    return answer


def _fred_off(ref: str) -> dict[str, Any]:
    from feeds.keys import FRED_KEY

    return {
        "ref": ref,
        "points": 0,
        "stored": False,
        "source": "fred",
        "state": "off",
        "note": f"needs key {FRED_KEY}",
    }


async def _live_fred(series_id: str, *, pacing: Any = None) -> dict[str, Any] | None:
    """Read one FRED series. Returns None when the key is missing.

    The body is for the caller to turn into points. It is not written here.
    A failure names the host only, never the key or the request URL.
    """
    from feeds.keys import FRED_KEY
    from feeds.live import BlockedSite, USER_AGENT, blocked_page

    key = os.environ.get(FRED_KEY, "").strip()
    if not key:
        return None
    import httpx

    try:
        if pacing is not None:
            pacing.ensure_open("https://api.stlouisfed.org/")
            await asyncio.to_thread(pacing._wait, "api.stlouisfed.org")
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=False) as http:
            response = await http.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": series_id, "api_key": key, "file_type": "json"},
            )
        if response.status_code in {301, 302, 303, 307, 308, 401, 402, 403, 429} or blocked_page(getattr(response, "content", b"")):
            raise BlockedSite("api.stlouisfed.org")
        if response.status_code >= 400:
            raise RuntimeError("fred read failed")
        body = response.json()
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("fred read failed") from None
    if not isinstance(body, dict):
        raise RuntimeError("fred read failed")
    return body


def _clock(now: datetime | None) -> datetime:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc)


async def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result
