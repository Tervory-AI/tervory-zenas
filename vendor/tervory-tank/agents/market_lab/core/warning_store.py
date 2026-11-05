"""Tenant-owned warning evidence, series snapshots and an outbox for Tank."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from core.ledger import PostgresLedger

from core.evidence import current_revisions, usable_points
from core.pure import series_fetch

log = logging.getLogger(__name__)


def event_day(event: object) -> date | None:
    value = event.get("date") if isinstance(event, dict) else None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            pass
    return None


def decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def row_dict(row: Any) -> dict:
    return {
        key: decoded(value)
        if key in {"summary", "config", "baseline", "input", "output", "content"}
        else str(value)
        if isinstance(value, UUID)
        else value
        for key, value in dict(row).items()
    }


async def series(ledger: PostgresLedger, tenant: str, now: datetime) -> dict[str, dict]:
    records = await ledger.conn.fetch(
        """SELECT content FROM agent_market_lab.feed_documents
        WHERE tenant_id=$1 AND fixture=false AND fetched_at <= $2 ORDER BY fetched_at, id""",
        UUID(tenant),
        now,
    )
    collected = {}
    weather = {}
    weather_title = None
    skipped_events = 0
    for record in records:
        doc = decoded(record["content"])
        if doc.get("kind") == "warning_evidence":
            continue
        if doc.get("source_id") == "noaa_alerts":
            weather_title = doc["name"]
            for index, event in enumerate(doc.get("events") or []):
                observed_day = event_day(event)
                if observed_day is None:
                    skipped_events += 1
                    continue
                stamp = observed_day.isoformat()
                if event.get("kind") != "severe_weather" or stamp > now.date().isoformat():
                    continue
                # One alert can have several state rows and appear in many snapshots.
                identity = event.get("url") or (event.get("title"), event.get("body"))
                weather.setdefault(stamp, {}).setdefault(identity, {
                    "document_id": doc["id"], "row": index,
                    "href": f"/market-lab/sources/{doc['id']}?row={index}&kind=event#event-{index}",
                })
        current = current_revisions(doc["points"])
        for index, point in await usable_points(ledger, tenant, doc):
            if (
                current[(point["series_id"], point["date"])] != index
                or str(point["date"])[:10] > now.date().isoformat()
            ):
                continue
            key = point["series_id"]
            target = collected.setdefault(
                key,
                {
                    "id": key,
                    "source": doc["source_id"].split(":", 1)[0],
                    "ref": point.get("ref", ""),
                    "name": doc.get("warning_subject") or point["title"],
                    "unit": point["unit"],
                    "points": {},
                },
            )
            target["points"][point["date"]] = {
                "date": point["date"],
                "value": str(point["value"]),
                "unit": point["unit"],
                "title": point["title"],
                "display_value": point.get("display_value"),
                "document_id": doc["id"],
                "row": index,
                "href": f"/market-lab/sources/{doc['id']}?row={index}#point-{index}",
            }
    if skipped_events:
        log.warning("Skipped %d stored weather events without a usable date", skipped_events)
    if weather:
        # Missing collection days remain missing; no zero counts are invented.
        title = weather_title
        identity = "noaa_alerts:severe_weather:US"
        collected[identity] = {
            "id": identity, "source": "noaa_alerts", "ref": "severe_weather:US",
            "name": title, "unit": "count", "points": {
                stamp: {"date": stamp, "value": str(len(alerts)), "unit": "count",
                        "title": title, **next(iter(alerts.values())),
                        "evidence": list(alerts.values())}
                for stamp, alerts in weather.items()
            },
        }
    return {
        key: {
            **value,
            "points": sorted(value["points"].values(), key=lambda p: p["date"]),
        }
        for key, value in collected.items()
    }


async def import_series(
    ledger: PostgresLedger, tenant: str, engine: str, item: dict, now: datetime
) -> None:
    """A permitted upstream service's saved rows pass through the existing parser/guard."""
    from core.evidence import save_check

    identity = item["source"] + ":" + item["ref"]
    dates = {p["date"]: p for p in item["points"]}
    rows = sorted(dates.values(), key=lambda p: p["date"])
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    prior = await ledger.conn.fetchval(
        """SELECT 1 FROM agent_market_lab.feed_documents
        WHERE tenant_id=$1 AND source_id=$2 AND content->>'digest'=$3 LIMIT 1""",
        UUID(tenant),
        identity,
        digest,
    )
    if prior:
        return
    text = "date,value\n" + "".join(
        f"{p['date']},{Decimal(str(p['value']))}\n" for p in rows
    )
    parsed = series_fetch(
        {
            "adapter": "csv",
            "series_id": identity,
            "source": item["source"],
            "text": text,
        }
    )
    sid, batch = tenant + ":" + identity, str(uuid4())
    await ledger.save_series(
        {
            "id": sid,
            "tenant_id": tenant,
            "source": item["source"],
            "ref": item["ref"],
            "unit": item["unit"],
            "engine": engine,
        }
    )
    points = [
        {
            **p,
            "batch_id": batch,
            "series_id": identity,
            "ref": item["ref"],
            "title": item["title"],
            "geo": item["geo"],
            "geo_level": item["geo_level"],
            "unit": item["unit"],
            "currency": item["currency"],
            "region": os.environ.get("TANK_REGION", "us-east"),
            "revision": digest,
            "frequency": "daily",
            "fetched_at": now.isoformat(),
            "source": item["source"],
            "observation_id": dates[p["date"]]["observation_id"],
            "display_value": dates[p["date"]].get("display_value"),
        }
        for p in parsed["points"]
    ]
    await ledger.save_points(sid, tenant, points, stored=True, source=item["source"])
    status = await ledger.conn.fetchval(
        "SELECT status FROM agent_market_lab.batches WHERE tenant_id=$1 AND id=$2",
        UUID(tenant),
        batch,
    )
    if status != "pooled":
        return
    await save_check(
        ledger,
        tenant,
        {
            "source_id": identity,
            "name": item["title"],
            "points": points,
            "events": [],
            "downloads": [],
            "note": "",
            "fixture": False,
            "digest": digest,
            "warning_subject": item["name"],
            "url": item["url"],
            "licence": "Private observations in this workspace",
            "terms_checked_at": "",
            "presentation": {
                "explanation": item.get(
                    "explanation",
                    "Saved observations from the products watched in this workspace.",
                )
            },
        },
        at=now,
    )


async def evidence(
    ledger: PostgresLedger, tenant: str, name: str, data: dict, now: datetime
) -> str:
    ident = str(uuid4())
    content = {
        "id": ident,
        "kind": "warning_evidence",
        "source_id": "warning_evidence",
        "name": name,
        "fetched_at": now.isoformat(),
        "points": [],
        "events": [],
        "downloads": [],
        "calculation": data,
    }
    await ledger.conn.execute(
        """INSERT INTO agent_market_lab.feed_documents
        (tenant_id,id,source_id,fetched_at,fixture,content) VALUES ($1,$2,'warning_evidence',$3,false,$4::jsonb)""",
        UUID(tenant),
        UUID(ident),
        now,
        json.dumps(content),
    )
    return ident


async def pair_event(
    ledger: PostgresLedger,
    tenant: str,
    warning: dict,
    kind: str,
    output: dict,
    *,
    actor: str,
    ident: str,
) -> None:
    content = await ledger.conn.fetchval(
        "SELECT content FROM agent_market_lab.feed_documents WHERE tenant_id=$1 AND id=$2",
        UUID(tenant),
        UUID(str(warning["document_id"])),
    )
    snapshot = decoded(content)["calculation"]
    await ledger.conn.execute(
        """INSERT INTO agent_market_lab.warning_training
        (tenant_id,id,warning_id,kind,actor,input,output) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)
        ON CONFLICT (tenant_id,id) DO NOTHING""",
        UUID(tenant),
        UUID(ident),
        UUID(str(warning["id"])),
        kind,
        actor,
        json.dumps(
            {
                "warning_id": str(warning["id"]),
                "prediction_id": str(warning["prediction_id"]),
                "evidence": f"/market-lab/sources/{warning['document_id']}",
                "window_end": str(warning["window_end"]),
                "calculation": snapshot,
                "baseline": warning["baseline"],
            }
        ),
        json.dumps(output),
    )


async def pending(ledger: PostgresLedger, tenant: str) -> list[dict]:
    return [
        row_dict(r)
        for r in await ledger.conn.fetch(
            "SELECT * FROM agent_market_lab.warning_training WHERE tenant_id=$1 AND delivered_at IS NULL ORDER BY created_at",
            UUID(tenant),
        )
    ]


async def mark_delivered(ledger: PostgresLedger, tenant: str, ident: str) -> None:
    await ledger.conn.execute(
        "UPDATE agent_market_lab.warning_training SET delivered_at=$3 WHERE tenant_id=$1 AND id=$2",
        UUID(tenant),
        UUID(ident),
        datetime.now(timezone.utc),
    )
