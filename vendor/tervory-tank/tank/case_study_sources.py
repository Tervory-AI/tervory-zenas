"""Read existing product evidence. Missing stores leave the fixed zero figures."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import importlib.util
from typing import Any
import uuid

from base_metrics import compare, cost_rows, flags, latest, obj


async def exists(conn: Any, table: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table)


def put(figures: dict, name: str, value: Any, source: str, **metadata) -> None:
    figures[name].update(value=value, source=source, status="available", **metadata)


def histogram(figures: dict, name: str, values: dict, source: str, **metadata) -> None:
    put(figures, name, [{"id": row["id"], "value": values.get(row["id"], 0)}
                        for row in figures[name]["value"]], source, **metadata)


def ranked(values: dict) -> list[dict]:
    return [{"id": key, "value": value} for key, value in sorted(values.items(), key=lambda pair: (-pair[1], pair[0]))]


async def base(conn, tenant, region, start, end, figures):
    rows = await conn.fetch("""SELECT * FROM tank.base_record_results
        WHERE tenant_id=$1 AND checked_at >= $2 AND checked_at < $3""", tenant, start, end)
    records = [dict(row) for row in rows]
    verified = [row for row in latest(records) if row["status"] == "done" and flags(row)["verified"]]
    put(figures, "base.records_verified", len(verified), "tank.base_record_results")
    put(figures, "base.records_by_state", ranked(Counter(row["state"] for row in verified)), "tank.base_record_results")
    ledger = await conn.fetch("""SELECT l.*,b.record_id FROM tank.base_ledger_links b
        JOIN tank.ledger l ON l.tenant_id=$1 AND l.id=b.ledger_id
        WHERE b.tenant_id=$1 AND l.region=$2 AND l.ts >= $3 AND l.ts < $4""", tenant, region, start, end)
    costs = cost_rows(records, [dict(row) for row in ledger])
    known = sum((Decimal(row["known_cost_usd"]) for row in costs), Decimal(0))
    put(figures, "base.cost_per_verified_record", known / len(verified) if verified else 0,
        "base_metrics.cost_rows", unknown_cost_calls=sum(row["unpriced"] for row in costs))
    scores = await conn.fetch("""SELECT a.id,a.confidence,a.updated_at FROM agent_base.records a
        WHERE a.tenant_id=$1 AND a.id=ANY($2::uuid[])""", tenant, [row["record_id"] for row in verified])
    by_id = {row["id"]: row for row in scores}
    bands = Counter()
    for row in verified:
        score = by_id.get(row["record_id"])
        n = score["confidence"] if score and score["updated_at"] <= row["checked_at"] else None
        band = "missing" if n is None or not 0 <= n <= 100 else "0-39" if n < 40 else "40-59" if n < 60 else "60-79" if n < 80 else "80-100"
        bands[band] += 1
    histogram(figures, "base.confidence_histogram", bands, "agent_base.records.confidence")
    apollo = await conn.fetch("""SELECT DISTINCT ON(r.licence_key) r.* FROM tank.base_apollo_records r
        JOIN tank.base_apollo_imports i ON i.tenant_id=$1 AND i.id=r.import_id
        WHERE r.tenant_id=$1 AND i.imported_at >= $2 AND i.imported_at < $3
        ORDER BY r.licence_key,i.imported_at DESC,i.id DESC""", tenant, start, end)
    comparison = compare(records, [dict(row) for row in apollo])
    counts = Counter()
    fields = {"website", "phone", "email"}
    outcomes = ("matches", "base_only", "apollo_only", "disagreements", "neither")
    for state in comparison["states"]:
        # Follow the comparison's fields, including additions from other Base work.
        for field, buckets in state.items():
            if not isinstance(buckets, dict):
                continue
            fields.add(field)
            for outcome in outcomes:
                counts[f"{field}.{outcome}"] += buckets.get(outcome, 0)
    histogram(figures, "base.comparison_vs_apollo", counts, "base_metrics.compare")
    compared = len(comparison["rows"])

    def comparison_figure(name, value, **metadata):
        # Data only: presentation requires an approved Figma frame.
        figures[name] = {**figures["base.records_verified"], "id": name}
        put(figures, name, value, "base_metrics.compare", **metadata)

    for field in sorted(fields):
        for outcome in outcomes:
            comparison_figure(f"base.vs_apollo.{field}.{outcome}", counts[f"{field}.{outcome}"])
        comparison_figure(f"base.vs_apollo.{field}.records_compared", compared)
    verified_compared = sum(flags({"fields": row["base"]})["verified"] for row in comparison["rows"])
    comparison_figure("base.vs_apollo.coverage", verified_compared / compared if compared else 0,
                      unit="ratio", base_verified=verified_compared, apollo_returned=compared,
                      records_compared=compared)
    # #390 is independent: use its saved verdicts and arithmetic when installed.
    if importlib.util.find_spec("base_audit") and await exists(conn, "tank.base_audits"):
        from base_audit import measure
        audit = await conn.fetchrow("""SELECT id,verdicts FROM tank.base_audits
            WHERE tenant_id=$1 AND reviewed_at >= $2 AND reviewed_at < $3
            ORDER BY reviewed_at DESC,id DESC LIMIT 1""", tenant, start, end)
        measured = measure(obj(audit["verdicts"])) if audit else None
        bounds = (measured or {}).get("confidence_interval") or {"lower": 0, "upper": 0}
        put(figures, "base.measured_accuracy", 100 * ((measured or {}).get("accuracy") or 0), "base_audit.measure",
            lower=100 * bounds["lower"], upper=100 * bounds["upper"],
            sample_size=(measured or {}).get("sample_size", 0), reviewed=(measured or {}).get("reviewed", 0),
            resolved=(measured or {}).get("denominator", 0),
            unsure=(measured or {}).get("unsure", 0), audit_id=str(audit["id"]) if audit else None)


async def tiger(conn, tenant, region, start, end, figures):
    if await exists(conn, "agent_tiger.watchlist"):
        n = await conn.fetchval("SELECT count(*) FROM agent_tiger.watchlist WHERE tenant_id=$1 AND enabled", tenant)
        put(figures, "tiger.pages_watched", n, "agent_tiger.watchlist", scope="current")
    if await exists(conn, "agent_tiger.observations"):
        n = await conn.fetchval("SELECT count(*) FROM agent_tiger.observations WHERE tenant_id=$1 AND ts >= $2 AND ts < $3", tenant, start, end)
        put(figures, "tiger.checks_run", n, "agent_tiger.observations")
    if await exists(conn, "agent_tiger.alerts"):
        rows = await conn.fetch("SELECT kind,before,after FROM agent_tiger.alerts WHERE tenant_id=$1 AND ts >= $2 AND ts < $3", tenant, start, end)
        put(figures, "tiger.changes_caught", len(rows), "agent_tiger.alerts")
        counts, unmeasured = Counter(), 0
        for row in rows:
            if row["kind"] != "price":
                continue
            try:
                before, after = Decimal(row["before"]), Decimal(row["after"])
                if not before.is_finite() or not after.is_finite() or before <= 0:
                    raise ValueError
                change = 100 * (after - before) / before
            except (ValueError, TypeError, InvalidOperation):
                unmeasured += 1
                continue
            band = "below_-50" if change < -50 else "-50_to_-10" if change < -10 else "-10_to_0" if change < 0 else "0_to_10" if change < 10 else "10_to_50" if change < 50 else "50_and_above"
            counts[band] += 1
        histogram(figures, "tiger.price_change_histogram", counts, "agent_tiger.alerts", axis_unit="percent", unmeasured=unmeasured)
    if await exists(conn, "agent_tiger.alert_mail"):
        n = await conn.fetchval("""SELECT count(*) FROM agent_tiger.alert_mail
            WHERE tenant_id=$1 AND status='sent' AND sent_at >= $2 AND sent_at < $3""", tenant, start, end)
        put(figures, "tiger.alerts_sent", n, "agent_tiger.alert_mail.sent_at")
    # Observation time is not the time the seller actually changed the page.


async def market_lab(conn, tenant, region, start, end, figures):
    if await exists(conn, "agent_market_lab.reports"):
        row = await conn.fetchrow("""SELECT count(*) AS answers,
            coalesce(sum(jsonb_array_length(citations)),0) AS citations FROM agent_market_lab.reports
            WHERE tenant_id=$1 AND made_at >= $2 AND made_at < $3 AND answer<>''""", tenant, start, end)
        put(figures, "market_lab.questions_answered", row["answers"], "agent_market_lab.reports")
        put(figures, "market_lab.sources_cited", row["citations"], "agent_market_lab.reports.citations")
    # Current predictions have no forecast-confidence field; feed_sources is
    # global, with no tenant subscriptions. Neither can supply the client figure.


async def rig(conn, tenant, region, start, end, figures):
    if await exists(conn, "agent_rig.widget_counts"):
        rows = await conn.fetch("""SELECT event,sum(count) AS n FROM agent_rig.widget_counts
            WHERE tenant_id=$1 AND day >= $2 AND day < $3 GROUP BY event""", tenant, start.date(), end.date())
        counts = {row["event"]: row["n"] for row in rows}
        put(figures, "rig.searches", counts.get("searches", 0), "agent_rig.widget_counts.searches")
        put(figures, "rig.no_result_searches", counts.get("no_results", 0), "agent_rig.widget_counts.no_results")
    if await exists(conn, "agent_rig.answers"):
        n = await conn.fetchval("""SELECT coalesce(sum(jsonb_array_length(body->'setups')),0) FROM agent_rig.answers
            WHERE tenant_id=$1 AND kind='setups' AND jsonb_typeof(body->'setups')='array'""", tenant)
        put(figures, "rig.setups_built", n, "agent_rig.answers", scope="current_retained_setups")
    if await exists(conn, "agent_rig.quotes"):
        n = await conn.fetchval("SELECT count(*) FROM agent_rig.quotes WHERE tenant_id=$1 AND sent_at >= $2 AND sent_at < $3", tenant, start, end)
        put(figures, "rig.quotes_sent", n, "agent_rig.quotes.sent_at")
    # No persisted estimate/invoice conversion; returned search results are not
    # proof that the shopper searched for each individual product in that list.


async def zenas(conn, tenant, region, start, end, figures):
    if await exists(conn, "agent_zenas.documents"):
        row = await conn.fetchrow("""SELECT count(*) FILTER(WHERE kind='memo') AS answers,
            count(*) FILTER(WHERE kind='memo' AND jsonb_array_length(sources)>0) AS cited,
            coalesce(sum(jsonb_array_length(sources)) FILTER(WHERE kind='memo'),0) AS sources,
            count(*) FILTER(WHERE kind='contract') AS drafts FROM agent_zenas.documents
            WHERE tenant_id=$1 AND created_at >= $2 AND created_at < $3""", tenant, start, end)
        put(figures, "zenas.questions", row["answers"], "agent_zenas.documents.memo", scope="saved_matter_answers")
        put(figures, "zenas.cited_answers", row["cited"], "agent_zenas.documents.memo", scope="saved_matter_answers")
        put(figures, "zenas.sources_per_answer", row["sources"] / row["answers"] if row["answers"] else 0,
            "agent_zenas.documents.sources", scope="saved_matter_answers")
        put(figures, "zenas.drafts", row["drafts"], "agent_zenas.documents.contract", scope="saved_matter_drafts")
    # Criminal refusals return before a durable question/result record is saved.


async def trigger(conn, tenant, region, start, end, figures):
    marketing = {}
    if await exists(conn, "tank.trigger_posts"):
        published = await conn.fetchval("""SELECT count(*) FROM tank.trigger_posts p
            JOIN tank.ledger l ON l.tenant_id=p.tenant_id AND l.id=p.ledger_id
            WHERE p.tenant_id=$1 AND l.region=$2 AND p.state='published'
            AND p.published_at >= $3 AND p.published_at < $4""", tenant, region, start, end)
        marketing.update(published_posts=published, published_posts_source="tank.trigger_posts.published_at")
    if await exists(conn, "tank.trigger_gbp_replies"):
        replies = await conn.fetchval("""SELECT count(*) FROM tank.trigger_gbp_replies r
            JOIN tank.ledger l ON l.tenant_id=r.tenant_id AND l.id=r.ledger_id
            WHERE r.tenant_id=$1 AND l.region=$2 AND r.sent_at >= $3 AND r.sent_at < $4""", tenant, region, start, end)
        marketing.update(review_replies=replies, review_replies_source="tank.trigger_gbp_replies.sent_at")
    row = await conn.fetchrow("""SELECT count(*) AS drafts,
        count(*) FILTER(WHERE feedback='accepted' AND feedback_at<$4) AS accepted,
        count(*) FILTER(WHERE feedback IN ('accepted','rejected') AND feedback_at<$4) AS reviewed
        FROM tank.ledger WHERE tenant_id=$1 AND region=$2 AND ts >= $3 AND ts < $4
        AND task_type='trigger.write' AND outcome='delivered'""", tenant, region, start, end)
    example_drafts = await conn.fetchval("""SELECT count(DISTINCT l.id)
        FROM tank.command_threads t CROSS JOIN LATERAL jsonb_each(t.state->'cards') c
        CROSS JOIN LATERAL jsonb_array_elements(CASE
            WHEN c.value->>'tool'='trigger.write.batch' AND jsonb_typeof(c.value->'result'->'items')='array'
            THEN c.value->'result'->'items' ELSE jsonb_build_array(c.value->'result') END) item
        JOIN tank.ledger l ON l.tenant_id=$1 AND l.id::text=item->>'ledger_id'
        WHERE t.tenant_id=$1 AND l.region=$2 AND l.ts >= $3 AND l.ts < $4
            AND c.value->>'status'='confirmed'
            AND c.value->>'tool' IN ('trigger.write','trigger.revise','trigger.write.batch')
            AND jsonb_typeof(item->'accepted_write_ids')='array'
            AND item->'accepted_write_ids'<>'[]'::jsonb""", tenant, region, start, end)
    put(figures, "trigger.drafts", row["drafts"], "tank.ledger.trigger.write",
        drafts_using_accepted_examples=example_drafts, **marketing)
    put(figures, "trigger.acceptance_rate", 100 * row["accepted"] / row["reviewed"] if row["reviewed"] else 0,
        "tank.ledger.feedback", reviewed=row["reviewed"])
    # Read checker results, not the draft text or user prompts. A receipt binds
    # the action to this tenant, region and period; repeated events do not count.
    rows = await conn.fetch("""SELECT DISTINCT l.id,item->'report' AS report
        FROM tank.command_threads t CROSS JOIN LATERAL jsonb_each(t.state->'cards') c
        CROSS JOIN LATERAL jsonb_array_elements(CASE
            WHEN c.value->>'tool'='trigger.write.batch' AND jsonb_typeof(c.value->'result'->'items')='array'
            THEN c.value->'result'->'items' ELSE jsonb_build_array(c.value->'result') END) item
        JOIN tank.ledger l ON l.tenant_id=$1 AND l.id::text=item->>'ledger_id'
        WHERE t.tenant_id=$1 AND l.region=$2 AND l.ts >= $3 AND l.ts < $4
            AND c.value->>'status'='confirmed' AND c.value->>'tool' IN ('trigger.write','trigger.revise','trigger.write.batch')
            AND jsonb_typeof(item->'report')='object'""", tenant, region, start, end)
    reports = [obj(row["report"]) for row in rows]
    issues = sum(report.get("issue_count", 0) for report in reports)
    voice_hits = sum(item.get("source") == "voice" for report in reports for item in report.get("issues", []))
    put(figures, "trigger.checker_issues_per_draft", issues / len(reports) if reports else 0,
        "tank.command_threads.confirmed_trigger_write.report", checked_drafts=len(reports))
    put(figures, "trigger.voice_rule_hits", voice_hits, "tank.command_threads.confirmed_trigger_write.report",
        truncated_reports=sum(bool(report.get("truncated")) for report in reports))


async def tisan(conn, tenant, region, start, end, figures):
    if await exists(conn, "agent_tisan.jobs"):
        n = await conn.fetchval("""SELECT count(DISTINCT result->>'slug') FROM agent_tisan.jobs
            WHERE tenant_id=$1 AND kind='scaffold' AND status='done' AND result->>'ok'='true'
                AND finished_at >= $2 AND finished_at < $3""", tenant, start, end)
        put(figures, "tisan.sites_built", n, "agent_tisan.jobs.scaffold")
    # Build checks store pass/fail, not visual similarity scores.


async def radix(conn, tenant, region, start, end, figures):
    if await exists(conn, "agent_radix.jobs"):
        n = await conn.fetchval("""SELECT count(*) FROM agent_radix.jobs
            WHERE tenant_id=$1 AND kind='scan' AND status='done' AND finished_at >= $2 AND finished_at < $3""", tenant, start, end)
        put(figures, "radix.scans", n, "agent_radix.jobs.scan")
    if await exists(conn, "agent_radix.findings"):
        rows = await conn.fetch("""SELECT lower(severity) AS severity,count(*) AS n FROM agent_radix.findings
            WHERE tenant_id=$1 AND created_at >= $2 AND created_at < $3 GROUP BY lower(severity)""", tenant, start, end)
        counts = Counter()
        for row in rows:
            key = row["severity"] if row["severity"] in {"critical", "high", "medium", "low", "info"} else "unknown"
            counts[key] += row["n"]
        histogram(figures, "radix.findings_by_severity", counts, "agent_radix.findings")


async def populate(conn: Any, tenant: uuid.UUID, product: str, region: str,
                   start: datetime, end: datetime, figures: dict) -> None:
    readers = {"base": base, "tiger": tiger, "market_lab": market_lab, "rig": rig,
               "zenas": zenas, "trigger": trigger, "tisan": tisan, "radix": radix}
    if product in readers:
        await readers[product](conn, tenant, region, start, end, figures)
