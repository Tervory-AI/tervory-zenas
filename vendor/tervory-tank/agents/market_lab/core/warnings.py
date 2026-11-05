"""Issue and resolve cited series warnings; all market choices come from the engine."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from core.ledger import PostgresLedger

from core.jobs import load_engine, perform_resolve
from core.pure import make_prediction
from core.warning_stats import analyze, day, is_hit, measured
from core import warning_store as store


def stamp(day_value: date) -> datetime:
    return datetime.combine(day_value, time.max, timezone.utc)


def source_link(ident: str, anchor: str = "history") -> str:
    return f"/market-lab/sources/{ident}#{anchor}"


async def run(
    ledger: PostgresLedger,
    tenant: str,
    engine: str,
    *,
    imported: list[dict] | None = None,
    now: datetime | None = None,
) -> dict:
    clock = now or datetime.now(timezone.utc)
    module = load_engine(engine)
    plan = module.warning_plan()
    pending_patterns = []
    memory = getattr(ledger, "memory", None)
    async with ledger.conn.transaction():
        await ledger.conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", tenant + ":warnings"
        )
        active = set()
        for group in imported or []:
            for item in module.warning_series(group["watch"], group["observations"]):
                await store.import_series(ledger, tenant, engine, item, clock)
                active.add(item["source"] + ":" + item["ref"])
        series = await store.series(ledger, tenant, clock)
        from core.event_signals import series as event_series
        from feeds.keys import key_is_set
        series.update(await event_series(ledger, tenant, plan, series, clock))
        for config in plan["pairs"]:
            if config.get("key_name") and not key_is_set(config["key_name"]):
                continue
            upstream = next(
                (
                    s
                    for s in series.values()
                    if s["source"] == config["source"] and s["ref"] == config["ref"]
                ),
                None,
            )
            downstream = [
                s
                for s in series.values()
                if s["source"] == config["downstream_source"] and s["id"] in active
            ]
            if not downstream:
                downstream = [
                    {
                        "id": "unconnected",
                        "name": next(
                            o["label"]
                            for o in plan["outcomes"]
                            if o["source"] == config["downstream_source"]
                        ),
                        "points": [],
                    }
                ]
            for target in downstream:
                pair_key = config["id"] + ":" + target["id"]
                stats = analyze(
                    upstream["points"] if upstream else [],
                    target["points"],
                    config,
                    clock.date(),
                )
                data = {
                    **stats,
                    "upstream_name": config["label"],
                    "downstream_name": target["name"],
                    "kind": "history",
                }
                fingerprint = hashlib.sha256(
                    json.dumps(data, sort_keys=True).encode()
                ).hexdigest()
                old = await ledger.conn.fetchrow(
                    "SELECT * FROM agent_market_lab.warning_pairs WHERE tenant_id=$1 AND pair_key=$2",
                    UUID(tenant),
                    pair_key,
                )
                previous = store.decoded(old["summary"]) if old else {}
                changed = previous.get("fingerprint") != fingerprint
                if not changed:
                    doc_id = str(old["document_id"])
                else:
                    doc_id = await store.evidence(
                        ledger, tenant, "Past changes and what followed", data, clock
                    )
                count = stats["episode_count"]
                text = (
                    f"Not enough history yet: {count} of {config['min_episodes']} past episodes"
                    if count < config["min_episodes"]
                    else "No new warning from this history."
                )
                summary = {
                    "name": f"{config['label']} → {target['name']}",
                    "text": text,
                    "href": source_link(doc_id),
                    "fingerprint": fingerprint,
                }
                pending_pattern = changed or previous.get("pattern_pending", True)
                if pending_pattern and memory is not None:
                    pending_patterns.append((pair_key, config, stats, doc_id))
                summary["pattern_pending"] = pending_pattern
                await ledger.conn.execute(
                    """INSERT INTO agent_market_lab.warning_pairs (tenant_id,pair_key,document_id,summary,updated_at)
                    VALUES ($1,$2,$3,$4::jsonb,$5) ON CONFLICT (tenant_id,pair_key) DO UPDATE
                    SET document_id=EXCLUDED.document_id,summary=EXCLUDED.summary,updated_at=EXCLUDED.updated_at""",
                    UUID(tenant),
                    pair_key,
                    UUID(doc_id),
                    json.dumps(summary),
                    clock,
                )
                current = stats["current"]
                if not current or count < config["min_episodes"] or not stats["hits"]:
                    continue
                start = date.fromisoformat(current["start"])
                window_start, window_end = (
                    start + timedelta(days=stats["lag_min"]),
                    start + timedelta(days=stats["lag_max"]),
                )
                if clock.date() >= window_end:
                    continue  # A late collection must not issue a retrospective warning.
                baseline = next(
                    (p for p in reversed(target["points"]) if day(p) <= start), None
                )
                if (
                    not baseline
                    or float(baseline["value"]) <= 0
                    or (start - day(baseline)).days > config["max_gap_days"]
                    or (
                        config.get("baseline_value") is not None
                        and float(baseline["value"]) != config["baseline_value"]
                    )
                ):
                    continue
                if any(
                    is_hit(
                        float(p["value"]),
                        float(baseline["value"]),
                        config["outcome_direction"],
                        config["outcome_pct"],
                    )
                    for p in target["points"]
                    if start < day(p) <= clock.date()
                ):
                    continue  # The downstream move has already happened.
                exists = await ledger.conn.fetchval(
                    "SELECT id FROM agent_market_lab.warnings WHERE tenant_id=$1 AND pair_key=$2 AND event_date=$3",
                    UUID(tenant),
                    pair_key,
                    start,
                )
                if exists:
                    continue
                ident = str(uuid4())
                expected = float(baseline["value"]) * (
                    1 - config["outcome_pct"]
                    if config["outcome_direction"] == "drop"
                    else 1 + config["outcome_pct"]
                )
                claim = f"{target['name']} {config['prediction_words']} after the upstream change."
                prediction = make_prediction(
                    tenant_id=tenant,
                    statement=claim,
                    metric_series_id=tenant + ":" + target["id"],
                    comparator="lte"
                    if config["outcome_direction"] == "drop"
                    else "gte",
                    target=expected,
                    resolve_by=stamp(window_end),
                    made_at=clock,
                    rationale="Past observed episodes; association does not establish cause.",
                    sources=[{"warning_id": ident, "evidence": source_link(doc_id)}],
                )
                # Freeze only the completed, non-overlapping history available
                # at issuance. Future outcomes never revise this confidence.
                from core.warning_bridge import call
                try:
                    reliability = await call("confidence", {"tenant_id": tenant, "hits": stats["hits"], "total": count})
                except RuntimeError:
                    # An optional calculation corridor must not prevent the
                    # warning itself. Unavailable confidence stays unknown.
                    reliability = None
                prediction.update(confidence=reliability["confidence"] if reliability else None,
                                  kind=config["id"], feed=config["source"])
                if reliability:
                    prediction["sources"].append({"confidence_method": "wilson_lower_95",
                                                  "hits": stats["hits"], "total": count})
                await ledger.save_prediction(prediction)
                await ledger.conn.execute(
                    """INSERT INTO agent_market_lab.warnings
                    (tenant_id,id,pair_key,event_date,prediction_id,document_id,downstream_id,config,baseline,window_start,window_end,created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,$12)""",
                    UUID(tenant),
                    UUID(ident),
                    pair_key,
                    start,
                    UUID(prediction["id"]),
                    UUID(doc_id),
                    target["id"],
                    json.dumps(config),
                    json.dumps(baseline),
                    window_start,
                    window_end,
                    clock,
                )
        await resolve(ledger, tenant, series, clock)
    # Immutable source documents must commit before a separate Hermes request.
    # Stable memory IDs make concurrent or lost-response retries harmless.
    for pair_key, config, stats, doc_id in pending_patterns:
        from core.patterns import remember
        if await remember(memory, tenant, config, stats, doc_id):
            await ledger.conn.execute(
                """UPDATE agent_market_lab.warning_pairs
                SET summary=jsonb_set(summary,'{pattern_pending}','false'::jsonb)
                WHERE tenant_id=$1 AND pair_key=$2 AND document_id=$3""",
                UUID(tenant), pair_key, UUID(doc_id),
            )
    return await listing(ledger, tenant)


async def resolve(
    ledger: PostgresLedger, tenant: str, series: dict, clock: datetime
) -> None:
    rows = await ledger.conn.fetch(
        """SELECT w.* FROM agent_market_lab.warnings w
        WHERE w.tenant_id=$1 AND w.window_end < $2 AND NOT EXISTS
        (SELECT 1 FROM agent_market_lab.warning_resolutions r WHERE r.tenant_id=w.tenant_id AND r.warning_id=w.id)""",
        UUID(tenant),
        clock.date(),
    )
    predictions = {str(p["id"]): p for p in await ledger.predictions_for(tenant)}
    for raw in rows:
        warning = store.row_dict(raw)
        config, baseline = warning["config"], warning["baseline"]
        points = series.get(warning["downstream_id"], {}).get("points", [])
        observed = [
            p
            for p in points
            if warning["window_start"] <= day(p) <= warning["window_end"]
        ]
        hit = next(
            (
                p
                for p in observed
                if is_hit(
                    float(p["value"]),
                    float(baseline["value"]),
                    config["outcome_direction"],
                    config["outcome_pct"],
                )
            ),
            None,
        )
        enough = measured(
            points, warning["event_date"], warning["window_end"], config["max_gap_days"]
        )
        prediction = predictions[warning["prediction_id"]]
        if hit or (enough and observed):
            selected = hit or observed[-1]
            result = await perform_resolve(
                tenant_id=tenant,
                ledger=ledger,
                now=clock,
                only_ids={warning["prediction_id"]},
                points_by_series={prediction["metric_series_id"]: [selected]},
            )
            outcome = result["outcomes"][0]
            resolved_id, status, score = (
                outcome["id"],
                outcome["outcome"],
                outcome["score"],
            )
        else:
            resolved_id, status, score = str(uuid4()), "not_measurable", None
            await ledger.save_prediction(
                {
                    **prediction,
                    "id": resolved_id,
                    "outcome": status,
                    "score": None,
                    "resolved_at": clock,
                    "sources": list(prediction["sources"])
                    + [{"resolves": warning["prediction_id"]}],
                }
            )
        details = {
            "kind": "resolution",
            "outcome": status,
            "score": score,
            "baseline": baseline,
            "window_start": warning["window_start"].isoformat(),
            "window_end": warning["window_end"].isoformat(),
            "observations": observed,
            "selected": hit or (observed[-1] if observed else None),
            "original": source_link(warning["document_id"]),
            "coverage_complete": enough,
            "coverage_end": next(
                (p for p in points if day(p) >= warning["window_end"]), None
            ),
        }
        doc = await store.evidence(
            ledger, tenant, "What happened during this warning's window", details, clock
        )
        await ledger.conn.execute(
            """INSERT INTO agent_market_lab.warning_resolutions
            (tenant_id,warning_id,prediction_id,document_id,outcome,score,resolved_at) VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            UUID(tenant),
            UUID(warning["id"]),
            UUID(resolved_id),
            UUID(doc),
            status,
            score,
            clock,
        )
        await store.pair_event(
            ledger,
            tenant,
            warning,
            "resolution",
            {"outcome": status, "score": score, "evidence": source_link(doc)},
            actor="measured outcome",
            ident=str(uuid5(NAMESPACE_URL, tenant + ":resolution:" + warning["id"])),
        )


async def listing(ledger: PostgresLedger, tenant: str) -> dict:
    rows = await ledger.conn.fetch(
        """SELECT w.*,d.content,r.outcome,r.score,r.document_id AS resolution_document_id,
        (SELECT t.output FROM agent_market_lab.warning_training t WHERE t.tenant_id=w.tenant_id AND t.warning_id=w.id AND t.kind='feedback' ORDER BY t.created_at DESC LIMIT 1) AS feedback
        FROM agent_market_lab.warnings w JOIN agent_market_lab.feed_documents d ON d.tenant_id=w.tenant_id AND d.id=w.document_id
        LEFT JOIN agent_market_lab.warning_resolutions r ON r.tenant_id=w.tenant_id AND r.warning_id=w.id
        WHERE w.tenant_id=$1 ORDER BY w.created_at DESC,w.id LIMIT 50""",
        UUID(tenant),
    )
    warnings = []
    for row in rows:
        data = store.decoded(row["content"])["calculation"]
        current = data["current"]
        previous, value = (
            float(current["previous"]["value"]),
            float(current["point"]["value"]),
        )
        change = abs((value - previous) / previous * 100) if previous else None
        direction = "fell" if value < previous else "rose"
        period = date.fromisoformat(current["start"]).strftime("%B %Y")
        href = source_link(str(row["document_id"]))
        facts = [
            {
                "text": f"{data['downstream_name']} {data['config']['prediction_words']}.",
                "href": href,
            },
            {
                "text": current["point"]["event_title"] if data["config"].get("trigger") == "event"
                else f"{data['upstream_name']} {direction} {change:.1f}% in {period}."
                if change is not None
                else f"{data['upstream_name']} changed in {period}.",
                "href": source_link(str(row["document_id"]), "upstream"),
            },
            {
                "text": f"After {data['hits']} of {data['episode_count']} past changes, {data['downstream_name']} {data['config']['outcome_words']} within {data['lag_min']}–{data['lag_max']} days.",
                "href": href,
            },
            {
                "text": f"The expected change followed in {data['hit_rate'] * 100:.0f}% of those past episodes. This past record does not guarantee another change or prove what caused it.",
                "href": href,
            },
            {
                "text": f"Expected window: {row['window_start'].strftime('%d %b %Y')} to {row['window_end'].strftime('%d %b %Y')}.",
                "href": source_link(str(row["document_id"]), "window"),
            },
        ]
        status = {
            None: "Window still open",
            "hit": "Came true",
            "miss": "Did not come true",
            "not_measurable": "Not measurable: there were not enough saved observations during the window.",
        }[row["outcome"]]
        feedback = store.decoded(row["feedback"]) if row["feedback"] else None
        warnings.append(
            {
                "id": str(row["id"]),
                "title": "A change to watch",
                "facts": facts,
                "status": status,
                "resolution_href": source_link(
                    str(row["resolution_document_id"]), "resolution"
                )
                if row["resolution_document_id"]
                else None,
                "feedback": feedback.get("useful") if feedback else None,
            }
        )
    pairs = [
        store.decoded(r["summary"])
        for r in await ledger.conn.fetch(
            "SELECT summary FROM agent_market_lab.warning_pairs WHERE tenant_id=$1 AND updated_at=(SELECT max(updated_at) FROM agent_market_lab.warning_pairs WHERE tenant_id=$1) ORDER BY pair_key",
            UUID(tenant),
        )
    ]
    return {
        "warnings": warnings,
        "history": pairs,
        "training_pending": bool(await store.pending(ledger, tenant)),
    }
