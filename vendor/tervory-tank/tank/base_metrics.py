"""Tenant result counts, comparison and expense arithmetic shared by JSON and CSV."""
from __future__ import annotations

from collections import defaultdict
import csv
from decimal import Decimal
import io
import json
import re
from typing import Any
from urllib.parse import urlsplit

from base_evidence import FIELDS, SITE_SIGNAL_FIELDS, SOCIAL_NETWORKS, phone, social_network

COUNTS = ("attempted", "website", "phone", "email", "email_and_phone", "email_only", "phone_only", "neither", "verified", "failed")
RUNGS = {1: "paid model", 2: "cheaper model", 3: "code", 4: "cache"}


def obj(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def latest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One business result per licence, newest attempted version; CSV rows stay separate."""
    chosen: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: (str(r["checked_at"]), str(r["record_id"]))):
        chosen[row.get("licence_key") or str(row["record_id"])] = row
    return list(chosen.values())


def selected(row: dict[str, Any], states: list[str], kinds: list[str], collectors: bool) -> bool:
    return (not states or row["state"] in states) and (not kinds or row["licence_type"] in kinds) and (
        collectors or row["licence_type"] != "03"
    )


def flags(row: dict[str, Any]) -> dict[str, bool]:
    fields = obj(row["fields"])
    found = {name: bool(fields[name].get("verified")) for name in FIELDS}
    return {"attempted": True, **found,
            "email_and_phone": found["email"] and found["phone"],
            "email_only": found["email"] and not found["phone"],
            "phone_only": found["phone"] and not found["email"],
            "neither": not found["email"] and not found["phone"],
            "verified": any(found[name] for name in ("website", "phone", "email")),
            "failed": row.get("status", "done") in {"done", "failed"} and any(not found[name] for name in FIELDS)}


def failure_reasons(row: dict[str, Any]) -> list[str]:
    if row.get("status", "done") not in {"done", "failed"}:
        return []
    return sorted({str(value.get("reason") or "No verified source.") for value in obj(row["fields"]).values() if not value.get("verified")})


def counts(reference: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}

    def group(row: dict[str, Any]) -> dict[str, Any]:
        key = (row["state"], row["licence_type"])
        return groups.setdefault(key, {"state": key[0], "licence_type": key[1], "licensees": 0,
                                       **{metric: 0 for metric in COUNTS}, "failed_by_reason": {}})
    for row in reference:
        group(row)["licensees"] += 1
    for row in latest(results):
        item = group(row)
        for metric, present in flags(row).items():
            if metric in COUNTS:
                item[metric] += int(present)
        for reason in failure_reasons(row):
            item["failed_by_reason"][reason] = item["failed_by_reason"].get(reason, 0) + 1
    return [groups[key] for key in sorted(groups)]


def cost_rows(results: list[dict[str, Any]], ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = {str(row["record_id"]): row for row in results}
    groups: dict[tuple[str, str, int], dict[str, Any]] = {}
    denominator: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in results:
        if flags(row)["verified"]:
            denominator[(row["state"], str(row["checked_at"])[:7])].add(row.get("licence_key") or str(row["record_id"]))
    seen: set[str] = set()
    for entry in ledger:
        identity = str(entry["id"])
        if identity in seen:
            continue
        seen.add(identity)
        record = records.get(str(entry.get("record_id")))
        if record is None:
            continue
        key = (record["state"], str(entry["ts"])[:7], 4 if entry.get("cache_hit") else int(entry["rung"]))
        item = groups.setdefault(key, {"state": key[0], "month": key[1], "rung": key[2], "rung_label": RUNGS.get(key[2], "other"),
                                       "known_cost": Decimal(0), "unpriced": 0, "ledger_ids": [], "vendors": set()})
        item["ledger_ids"].append(identity)
        item["vendors"].add(entry.get("vendor") or "")
        if entry.get("cost_status") == "not_set" or entry["cost_usd"] is None:
            item["unpriced"] += 1
        else:
            item["known_cost"] += Decimal(str(entry["cost_usd"]))
    out = []
    for key, item in sorted(groups.items()):
        n = len(denominator[(key[0], key[1])])
        known = item.pop("known_cost")
        total = None if item["unpriced"] else known
        out.append({**item, "verified_records": n, "known_cost_usd": str(known),
                    "cost_usd": str(total) if total is not None else None,
                    "cost_per_verified_record": str(total / n) if n and total is not None else None,
                    "vendors": sorted(item["vendors"]),
                    "status": "price not set" if item["unpriced"] else "no verified records" if not n else "priced"})
    return out


def list_costs(ledger: list[dict[str, Any]], record_ids: list[str] | None = None) -> dict[str, Any]:
    """One contribution per ledger ID, with unknown prices kept unknown."""
    entries = {str(entry["id"]): entry for entry in ledger}.values()
    actual, baseline = Decimal(0), Decimal(0)
    missing = not ledger or bool(record_ids is not None and set(map(str, record_ids))
                                 - {str(entry.get("record_id")) for entry in ledger})
    unpriced = baseline_missing = missing
    calls = 0
    for entry in entries:
        calls += 1
        if entry.get("cost_status") == "not_set" or entry.get("cost_usd") is None:
            unpriced = True
        else:
            actual += Decimal(str(entry["cost_usd"]))
        if entry.get("baseline_cost_usd") is None:
            baseline_missing = True
        else:
            baseline += Decimal(str(entry["baseline_cost_usd"]))
    return {"actual_usd": None if unpriced else actual,
            "baseline_usd": None if baseline_missing else baseline,
            "saved_usd": None if unpriced or baseline_missing else baseline - actual,
            "calls": calls, "receipt_status": "price not set" if unpriced else "priced"}


def matches(row: dict[str, Any], metric: str, reason: str | None = None) -> bool:
    return (flags(row).get(metric, False) if metric != "all" else True) and (not reason or reason in failure_reasons(row))


def csv_text(rows: list[dict[str, Any]], columns: list[str]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns)
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column)
            text = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else "" if value is None else str(value)
            if re.match(r"^[\s\ufeff]*[=+@-]|^[\t\r\n]", text) and not re.fullmatch(r"\+?[1-9][0-9]{6,14}(?:; \+?[1-9][0-9]{6,14})*", text):
                text = "'" + text
            cells.append(text)
        writer.writerow(cells)
    return "\ufeff" + stream.getvalue()


def csv_reason(reason: str | None, field: str) -> str | None:
    """Readable export copy; stored reasons and count filters keep their codes."""
    if not reason:
        return None
    reasons = {
        "blocked": "The store blocked automated checks.",
        "active_block": "The store requires a human access check; automated checks stopped.",
        "login_required": "The page requires sign-in, so it was not read.",
        "paywall": "The page requires payment, so it was not read.",
        "unavailable": "The website could not be reached.",
        "no_website": "No website found for this business.",
        "no_website_found": "No website found for this business.",
    }
    code = reason.strip().lower()
    if code in reasons:
        return reasons[code]
    if code.startswith("blocked:") or code in {"http_403", "http_429", "http_451"}:
        return reasons["blocked"]
    if code == "not_found":
        detail = {"website": "website", "phone": "phone number", "email": "email address", "socials": "social links"}[field]
        return f"No {detail} found for this business."
    if re.fullmatch(r"[\w.:-]+", reason.strip()):
        return "This detail could not be verified."
    return reason


SOCIAL_NAMES = {"facebook": "Facebook", "instagram": "Instagram", "x": "X", "linkedin": "LinkedIn",
                "youtube": "YouTube", "tiktok": "TikTok"}


def verified_contacts(field: dict[str, Any]) -> list[dict[str, str]]:
    """Keep verification order and each value's own evidence; never borrow from a sibling."""
    if not field.get("verified"):
        return []
    found, seen = [], set()
    for item in field.get("items") or [field]:
        if not item.get("verified", True) or not item.get("source_url") or not item.get("checked_at"):
            continue
        values = item.get("value")
        for value in values if isinstance(values, list) else [values]:
            if not isinstance(value, str) or not value.strip() or value in seen:
                continue
            seen.add(value)
            found.append({"value": value, "source_url": item["source_url"], "checked_at": item["checked_at"]})
    return found


def export_records(rows: list[dict[str, Any]], ledger: list[dict[str, Any]] | None = None) -> str:
    identity = ["record_id", "licence_key", "state", "licence_type", "name"]
    contacts = ["email", "other_emails", "phone", "other_phones", "website", "other_websites"]
    contacts += [name for network in SOCIAL_NETWORKS for name in (network, f"other_{network}")]
    columns = identity + contacts
    columns += [f"{name}_{part}" for name in contacts for part in ("source_url", "checked_at", "reason")]
    quality_columns = ("phone_e164", "phone_valid", "phone_line_type", "email_syntax_ok", "email_disposable")
    columns += list(quality_columns)
    columns += [f"{name}_{part}" for name in quality_columns for part in ("source_url", "checked_at", "confidence")]
    columns += list(SITE_SIGNAL_FIELDS)
    columns += [f"{name}_{part}" for name in SITE_SIGNAL_FIELDS
                for part in ("source_url", "checked_at", "evidence", "confidence")]
    per_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ledger_records: dict[str, set[str]] = defaultdict(set)
    record_ids = {str(row["record_id"]) for row in rows}
    if ledger is not None:
        columns += ["cost_usd", "baseline_cost_usd"]
        for entry in ledger:
            record_id = str(entry.get("record_id"))
            if record_id in record_ids:
                per_record[record_id].append(entry)
                ledger_records[str(entry["id"])].add(record_id)
    # Preserve existing CSV bytes unless a verified link needs the catch-all columns.
    other_socials = any(any(social_network(item["value"]) not in SOCIAL_NETWORKS
                           for item in verified_contacts(obj(row["fields"]).get("socials") or {})) for row in rows)
    if other_socials:
        columns += ["other_socials", "other_socials_source_url", "other_socials_checked_at"]
    exported = []
    for row in rows:
        fields = obj(row["fields"])
        record = {name: row.get(name) for name in identity}

        def write(name: str, values: list[dict[str, str]], reason: str | None = None,
                  missing: dict[str, Any] | None = None) -> None:
            record[name] = "; ".join(item["value"] for item in values)
            for part in ("source_url", "checked_at"):
                record[f"{name}_{part}"] = "; ".join(item[part] for item in values) if values else (missing or {}).get(part)
            record[f"{name}_reason"] = reason if not values else None

        emails = verified_contacts(fields["email"])
        write("email", emails[:1], csv_reason(fields["email"].get("reason"), "email"),
              fields["email"] if not fields["email"].get("verified") else None)
        write("other_emails", emails[1:])
        for name in ("phone", "website"):
            values = verified_contacts(fields[name])
            write(name, values[:1], csv_reason(fields[name].get("reason"), name),
                  fields[name] if not fields[name].get("verified") else None)
            write(f"other_{name}s", values[1:])
        socials = verified_contacts(fields["socials"])
        for network in SOCIAL_NETWORKS:
            values = [item for item in socials if social_network(item["value"]) == network]
            reason = csv_reason(fields["socials"].get("reason"), "socials") or f"No verified {SOCIAL_NAMES[network]} link found for this business."
            write(network, values[:1], reason, fields["socials"] if not fields["socials"].get("verified") else None)
            write(f"other_{network}", values[1:])
        if other_socials:
            write("other_socials", [item for item in socials if social_network(item["value"]) not in SOCIAL_NETWORKS])
        for name in quality_columns:
            item = fields.get(name) or {}
            if item.get("verified"):
                record[name] = item.get("value")
                for part in ("source_url", "checked_at", "confidence"):
                    record[f"{name}_{part}"] = item.get(part)
        for name in SITE_SIGNAL_FIELDS:
            signal = fields.get(name) or {}
            if signal.get("verified"):
                record[name] = signal.get("value")
                for part in ("source_url", "checked_at", "evidence", "confidence"):
                    record[f"{name}_{part}"] = signal.get(part)
        if ledger is not None:
            entries = per_record[str(row["record_id"])]
            shared = any(len(ledger_records[str(entry["id"])]) > 1 for entry in entries)
            costs = list_costs(entries)
            for column, key in (("cost_usd", "actual_usd"), ("baseline_cost_usd", "baseline_usd")):
                value = None if shared else costs[key]
                record[column] = None if value is None else format(value, "f")
        exported.append(record)
    return csv_text(exported, columns)


def normal(value: Any, name: str) -> set[str]:
    values = value if isinstance(value, list) else re.split(r"[;,]", str(value or ""))
    result = set()
    for item in values:
        text = str(item).strip().lower()
        if name == "phone":
            text = phone(text)
        elif name == "website" and text:
            try:
                text = (urlsplit(text if "://" in text else "https://" + text).hostname or "").removeprefix("www.")
            except ValueError:
                text = ""
        if text:
            result.add(text)
    return result


def compare(results: list[dict[str, Any]], apollo: list[dict[str, Any]]) -> dict[str, Any]:
    lookup = {row["licence_key"]: row for row in latest(results) if row.get("licence_key")}
    groups: dict[str, Any] = {}
    rows = []
    for other in apollo:
        row = obj(other.get("base_snapshot")) or lookup.get(other["licence_key"])
        if row is None:
            continue
        base = obj(row["fields"])
        answer = obj(other["fields"])
        outcomes = {}
        state = groups.setdefault(row["state"], {"state": row["state"], "records": 0,
            **{name: {key: 0 for key in ("matches", "base_only", "apollo_only", "disagreements", "neither")}
               for name in ("website", "phone", "email")}})
        state["records"] += 1
        for name in ("website", "phone", "email"):
            left = normal(base[name]["value"] if base[name]["verified"] else None, name)
            right = normal(answer.get(name), name)
            outcome = "neither" if not left and not right else "base_only" if not right else "apollo_only" if not left else "matches" if left == right else "disagreements"
            state[name][outcome] += 1
            outcomes[name] = outcome
        rows.append({"record_id": str(row["record_id"]), "licence_key": row["licence_key"], "state": row["state"],
                     "base": base, "apollo": answer, "outcomes": outcomes})
    return {"states": [groups[key] for key in sorted(groups)], "rows": rows}
