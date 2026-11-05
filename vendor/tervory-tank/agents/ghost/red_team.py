"""Standing red-team exercises against Tervory's own sites only.

A client's systems require written permission (client_settings.red_team_permission).
Never against anyone else. Receipts are marked exercise.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .client_settings import ClientAutumbraSettings
from .never_block import extract_ips, may_block_ip

OWN_SITES = frozenset({
    "tervory.com",
    "app.tervory.com",
    "coolify.tervory.com",
    "rig.tervory.com",
    "za.tervory.com",
})

EXERCISES = (
    {"id": "credential_stuffing_fixture", "plain": "Known-pattern credential stuffing from a fixture address list"},
    {"id": "path_scan_fixture", "plain": "Path scan against an allowlisted own host"},
    {"id": "outdated_package_fixture", "plain": "Out-of-date package planted in a test branch"},
    {"id": "forged_webhook_fixture", "plain": "Forged webhook signature (must be dropped)"},
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_exercises() -> list[dict[str, str]]:
    return [{"id": e["id"], "plain": e["plain"]} for e in EXERCISES]


def run_exercise(
    exercise_id: str,
    *,
    target_site: str,
    settings: ClientAutumbraSettings | None = None,
    fixture_log: str = "",
    own_sites: frozenset[str] = OWN_SITES,
) -> dict[str, Any]:
    """Run a fixture exercise. Returns a receipt marked exercise."""
    known = {e["id"]: e for e in EXERCISES}
    if exercise_id not in known:
        raise ValueError("Unknown exercise.")
    site = target_site.strip().lower()
    if site not in own_sites:
        if settings is None or not settings.red_team_permission:
            raise PermissionError(
                "Red-team exercises run on Tervory's own sites only, "
                "or a client with written permission."
            )
        # Client path still requires the site to match the client's configured site.
        if settings.site_name and site != settings.site_name.strip().lower():
            raise PermissionError("Exercise target is not this client's site.")

    receipt: dict[str, Any] = {
        "id": str(uuid4()),
        "product": "Autumbra",
        "kind": "exercise",
        "exercise_id": exercise_id,
        "plain": known[exercise_id]["plain"],
        "target_site": site,
        "at": now_iso(),
        "result": "ran",
        "learning": [],
        "addresses_seen": extract_ips(fixture_log),
    }

    if exercise_id == "forged_webhook_fixture":
        # Signature failure is success for this exercise.
        receipt["result"] = "expected_drop"
        receipt["learning"].append("unsigned webhook must be dropped and counted, never stored")
    elif exercise_id == "credential_stuffing_fixture":
        blocked = []
        refused = []
        for address in receipt["addresses_seen"]:
            ok, why = may_block_ip(address, raw_log=fixture_log, client_own_cidrs=(settings.own_cidrs if settings else ()))
            (blocked if ok else refused).append({"address": address, "why": why})
        receipt["blocked"] = blocked
        receipt["refused"] = refused
        if not blocked and refused:
            receipt["learning"].append("miss: no blockable address after never-block — review rules")
    elif exercise_id == "path_scan_fixture":
        receipt["learning"].append("path scan fixture recorded; Watch rules should propose rate_limit")
    elif exercise_id == "outdated_package_fixture":
        receipt["learning"].append("supply-chain watch should open a match against the planted lockfile version")

    return receipt


def run_own_sites_exercise_pass(
    exercise_id: str = "credential_stuffing_fixture",
    *,
    fixture_log: str = "stuffing from 203.0.113.9 against /login",
    own_sites: frozenset[str] = OWN_SITES,
) -> list[dict[str, Any]]:
    """Run one fixture exercise against every Tervory own site. Receipts marked exercise."""
    receipts: list[dict[str, Any]] = []
    for site in sorted(own_sites):
        receipt = run_exercise(
            exercise_id,
            target_site=site,
            fixture_log=fixture_log,
            own_sites=own_sites,
        )
        if receipt.get("kind") != "exercise":
            raise AssertionError("receipt must be marked exercise")
        receipts.append(receipt)
    return receipts
