"""Supply, demand, and cost series for the firearms case study.

Supply: active licences per area and type from the ATF monthly lists, per
capita (Census population), dealers with a web presence (Base), stock and
price signals (Tiger).

Demand: the founder's per-state licence-application history (uploaded),
monthly FBI NICS background-check counts per state (a demand proxy, not
sales), population growth, and client order data where the client allows it.

Costs: copper, zinc, and lead from FRED, plus freight indices. FRED is read
live and never stored. Saturation is supply density plus competitor activity.
"""

from __future__ import annotations

from typing import Any

# Platform tenant. Scheduled work for this case study lands here until a
# client tenant is named on the job.
PLATFORM_TENANT = "00000000-0000-0000-0000-000000000001"

def _fields(value: Any, required: str, optional: str = "") -> None:
    required_keys, optional_keys = set(required.split()), set(optional.split())
    if not isinstance(value, dict) or not required_keys <= value.keys() or value.keys() - required_keys - optional_keys:
        raise ValueError("invalid supply map fields")


def load_map(path: Any = None) -> dict[str, Any]:
    """Reject malformed configuration before any collection or warning runs."""
    import json
    import math
    from pathlib import Path

    def unique(items: list) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate supply map field")
            result[key] = value
        return result

    data = json.loads(Path(path or Path(__file__).with_name("supply_map.json")).read_text(), object_pairs_hook=unique)
    _fields(data, "plan warnings")
    spec, warnings = data["plan"], data["warnings"]
    _fields(spec, "supply demand population costs surge bindings response_targets")
    _fields(warnings, "signals outcomes pairs", "event_signals")
    for role, required in {
        "supply": "source prefix unit per_capita note",
        "demand": "source prefix unit note",
        "population": "source prefix unit",
        "costs": "role ref feeds_id adapter source store unit",
    }.items():
        if not isinstance(spec[role], list) or not spec[role]:
            raise ValueError("missing supply map entries")
        for item in spec[role]:
            _fields(item, required)
            if any(not isinstance(v, str) or not v for k, v in item.items() if k not in {"per_capita", "store"}):
                raise ValueError("invalid supply map value")
            if role == "supply" and type(item["per_capita"]) is not bool:
                raise ValueError("invalid per capita flag")
            if role == "costs" and (item["source"] != "fred" or item["adapter"] != "fred" or item["store"] is not False):
                raise ValueError("FRED must remain linked and never stored")
    _fields(spec["surge"], "window z_threshold pct_threshold")
    _fields(spec["response_targets"], "increase hold reduce")
    _fields(spec["bindings"], "supply demand population competitors")
    for entries in spec["bindings"].values():
        if not isinstance(entries, list) or not entries:
            raise ValueError("missing supply map bindings")
        for item in entries:
            _fields(item, "source prefix")
            if any(not isinstance(v, str) or not v for v in item.values()):
                raise ValueError("invalid supply map binding")
    for key, optional in (("signals", "ref kind"), ("outcomes", "note")):
        if not isinstance(warnings[key], list) or not warnings[key]:
            raise ValueError("missing warning entries")
        for item in warnings[key]:
            _fields(item, "source label", optional)
            if any(not isinstance(v, str) or not v for v in item.values()):
                raise ValueError("invalid warning entry")
    if not isinstance(warnings["pairs"], list) or not warnings["pairs"]:
        raise ValueError("missing warning pairs")
    upstream = {item["source"] for item in warnings["signals"]}
    downstream = {item["source"] for item in warnings["outcomes"]}
    ids = set()
    for pair in warnings["pairs"]:
        _fields(pair, "id source ref label downstream_source direction outcome_direction baseline_value outcome_pct outcome_words prediction_words min_episodes window z_threshold pct_threshold min_lag_days max_lag_days max_gap_days", "trigger key_name")
        if pair["source"] not in upstream or pair["downstream_source"] not in downstream or pair["id"] in ids:
            raise ValueError("unknown warning source or duplicate pair")
        ids.add(pair["id"])
        for key in "id source ref label downstream_source outcome_words prediction_words".split():
            if not isinstance(pair[key], str) or not pair[key]:
                raise ValueError("invalid warning pair value")
        if pair["direction"] not in {"drop", "surge"} or pair["outcome_direction"] not in {"drop", "surge"}:
            raise ValueError("invalid warning direction")
        for key in "min_episodes window min_lag_days max_lag_days max_gap_days".split():
            if type(pair[key]) is not int or pair[key] <= 0:
                raise ValueError("invalid warning window")
        if pair["min_lag_days"] > pair["max_lag_days"]:
            raise ValueError("invalid warning lag")
        for key in "baseline_value outcome_pct z_threshold pct_threshold".split():
            if type(pair[key]) not in (int, float) or not math.isfinite(pair[key]) or pair[key] <= 0:
                raise ValueError("invalid warning threshold")
    from feeds.keys import KEY_NAMES
    import re
    rules = warnings.get("event_signals", [])
    if not isinstance(rules, list):
        raise ValueError("invalid event signals")
    refs = set()
    for rule in rules:
        _fields(rule, "source kind ref key_name region_source region_prefix", "stage stage_pattern")
        if any(not isinstance(value, str) or not value for value in rule.values()):
            raise ValueError("invalid event signal")
        if rule["source"] not in upstream or rule["key_name"] not in KEY_NAMES or rule["ref"] in refs:
            raise ValueError("invalid event signal source")
        refs.add(rule["ref"])
        if not any(r["source"] == rule["region_source"] and r["prefix"] == rule["region_prefix"] for r in spec["supply"]):
            raise ValueError("invalid event supply region")
        if ("stage" in rule) != ("stage_pattern" in rule):
            raise ValueError("invalid event stage")
        if "stage_pattern" in rule:
            try:
                pattern = re.compile(rule["stage_pattern"])
                if "stage" not in pattern.groupindex:
                    raise ValueError("invalid event stage")
            except re.error:
                raise ValueError("invalid event stage") from None
    for pair in warnings["pairs"]:
        if pair.get("trigger") not in {None, "event"} or pair.get("key_name") not in {None, *KEY_NAMES}:
            raise ValueError("invalid event pair")
        if pair.get("trigger") == "event" and not any(
            r["source"] == pair["source"] and r["ref"] == pair["ref"] and r["key_name"] == pair.get("key_name") for r in rules):
            raise ValueError("missing event signal rule")
    for values in (spec["surge"], spec["response_targets"]):
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values.values()):
            raise ValueError("invalid plan threshold")
    if type(spec["surge"]["window"]) is not int:
        raise ValueError("invalid plan window")
    return data


# Retain the existing module exports, with configuration owned by the data file.
SURGE = load_map()["plan"]["surge"]
RESPONSE_TARGETS = load_map()["plan"]["response_targets"]
FRED_COSTS = [{k: row[k] for k in ("role", "ref", "feeds_id")} for row in load_map()["plan"]["costs"]]


def plan() -> dict[str, Any]:
    from feeds.catalog import sources

    return {
        "feeds": [source.id for source in sources("ffl") if source.allow_bulk],
        "linked_pages": [source.id for source in sources("ffl") if not source.allow_bulk],
        "engine": "ffl",
        **load_map()["plan"],
    }


def products() -> list[dict[str, Any]]:
    """Where a gap lines up with a product Tervory has or could build."""
    return [
        {"id": "tisan", "when": "no_website", "fit": 0.9, "note": "dealers with no website"},
        {"id": "rig", "when": "web_shop", "fit": 0.8, "note": "dealers with a web shop"},
        {"id": "zenas", "when": "expiring", "fit": 0.7, "note": "licences expiring"},
    ]


def schedules() -> list[dict[str, Any]]:
    """Daily refresh reads FRED live and reloads licence counts. A repeated month is not overwritten. Gaps are recomputed on the first of the month."""
    base = {"tenant_id": PLATFORM_TENANT, "input": {"engine": "ffl"}}
    return [
        {**base, "cron": "20 6 * * *", "kind": "refresh"},
        {**base, "cron": "40 6 1 * *", "kind": "opportunities"},
        {**base, "cron": "10 7 * * *", "kind": "resolve"},
    ]


def warning_plan() -> dict[str, Any]:
    from engines.ffl.warnings import plan
    return plan()


def warning_series(watch: dict, observations: list[dict]) -> list[dict]:
    from engines.ffl.warnings import product_series
    return product_series(watch, observations)
