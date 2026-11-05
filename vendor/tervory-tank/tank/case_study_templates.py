"""The founder's fixed case-study figure list, version 1. API data only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal


@dataclass(frozen=True)
class Figure:
    id: str
    unit: str
    type: Literal["number", "series", "histogram", "ranked_list"] = "number"
    bins: tuple[str, ...] = ()


TANK = (
    Figure("tank.spend", "USD"),
    Figure("tank.savings_vs_baseline", "USD"),
    Figure("tank.cost_per_task", "USD/task"),
    Figure("tank.cache_hits", "calls"),
    Figure("tank.work_by_execution", "calls", "histogram", ("code", "cache", "model")),
    Figure("tank.graduation_progress", "percent", "ranked_list"),
)
PRODUCTS = {
    "base": (
        Figure("base.records_verified", "records"),
        Figure("base.cost_per_verified_record", "USD/record"),
        Figure("base.measured_accuracy", "percent"),
        Figure("base.confidence_histogram", "records", "histogram", ("0-39", "40-59", "60-79", "80-100", "missing")),
        Figure("base.records_by_state", "records", "ranked_list"),
        Figure("base.comparison_vs_apollo", "fields", "histogram", tuple(
            f"{field}.{outcome}" for field in ("website", "phone", "email")
            for outcome in ("matches", "base_only", "apollo_only", "disagreements", "neither"))),
    ),
    "tiger": (
        Figure("tiger.pages_watched", "pages"),
        Figure("tiger.checks_run", "checks"),
        Figure("tiger.changes_caught", "changes"),
        Figure("tiger.alerts_sent", "alerts"),
        Figure("tiger.price_change_histogram", "changes", "histogram", ("below_-50", "-50_to_-10", "-10_to_0", "0_to_10", "10_to_50", "50_and_above")),
        Figure("tiger.time_to_detect", "seconds"),
    ),
    "market_lab": (
        Figure("market_lab.questions_answered", "answers"),
        Figure("market_lab.sources_cited", "citations"),
        Figure("market_lab.calibration", "percent", "histogram", tuple(f"{i}-{i+10}" for i in range(0, 100, 10))),
        Figure("market_lab.active_feeds", "feeds"),
    ),
    "rig": (
        Figure("rig.searches", "searches"),
        Figure("rig.no_result_searches", "searches"),
        Figure("rig.setups_built", "setups"),
        Figure("rig.quotes_sent", "quotes"),
        Figure("rig.quotes_converted", "quotes"),
        Figure("rig.most_searched_products", "searches", "ranked_list"),
    ),
    "zenas": (
        Figure("zenas.questions", "questions"),
        Figure("zenas.cited_answers", "answers"),
        Figure("zenas.criminal_questions_refused", "questions"),
        Figure("zenas.sources_per_answer", "citations/answer"),
        Figure("zenas.drafts", "drafts"),
    ),
    "trigger": (
        Figure("trigger.drafts", "drafts"),
        Figure("trigger.checker_issues_per_draft", "issues/draft"),
        Figure("trigger.acceptance_rate", "percent"),
        Figure("trigger.voice_rule_hits", "hits"),
    ),
    "tisan": (
        Figure("tisan.sites_built", "sites"),
        Figure("tisan.visual_check_scores", "score", "series"),
    ),
    "radix": (
        Figure("radix.scans", "scans"),
        Figure("radix.findings_by_severity", "findings", "histogram", ("critical", "high", "medium", "low", "info", "unknown")),
    ),
    "autumbra": (
        Figure("autumbra.checks_run", "checks"),
        Figure("autumbra.incidents", "incidents"),
        Figure("autumbra.shields_applied", "shields"),
    ),
}


def empty_figures(product: str, start: date, end: date) -> dict[str, dict]:
    """Inclusive UTC dates, including a zero for every day of each series."""
    figures = {}
    for spec in (*TANK, *PRODUCTS[product]):
        value = 0
        if spec.type == "histogram":
            value = [{"id": key, "value": 0} for key in spec.bins]
        elif spec.type == "ranked_list":
            value = []
        elif spec.type == "series":
            value = [{"date": (start + timedelta(days=i)).isoformat(), "value": 0}
                     for i in range((end - start).days + 1)]
        figures[spec.id] = {"id": spec.id, "unit": spec.unit, "type": spec.type,
                            "value": value, "source": None, "status": "missing_source"}
    if product == "base":
        figures["base.measured_accuracy"].update(lower=0, upper=0, sample_size=0, resolved=0, unsure=0)
    return figures
