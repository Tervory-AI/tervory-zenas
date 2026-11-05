"""Weekly owner-update job entry for Autumbra.

Real schedule registry entry — not a comment. Builds every count from receipts
in the reported week (never from a request body). Sends when Autumbra acts, and
weekly for every tenant with a stored owner_email. Every link points to a page
that exists. Undo links carry a signed, expiring token naming tenant + shield.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

from .client_settings import ClientAutumbraSettings, load_settings
from .owner_email import (
    build_owner_update,
    send_owner_update,
)
from .undo_token import sign_receipts_token, sign_undo_token

# Real job entry (discovered by ops / hermes-style schedulers).
WEEKLY_OWNER_UPDATE_JOB: dict[str, Any] = {
    "id": "autumbra_weekly_owner_update",
    "product": "Autumbra",
    "kind": "schedule",
    "cadence": "weekly",
    "entrypoint": "ghost.owner_email_jobs.run_weekly_owner_updates",
    "description": (
        "Build the owner update email from receipts (code only) and send it "
        "to each client's owner_email. Quiet week when nothing fired."
    ),
}

PROTECT_HOST = "https://protect.tervory.com"


def default_receipt_url(tenant_id: str) -> str:
    """Weekly receipt page with a signed token for that tenant (email credential).

    Empty when AUTUMBRA_UNDO_TOKEN_SECRET is unset — never emit a dead token-less link.
    """
    tid = (tenant_id or "").strip()
    if not tid:
        return ""
    try:
        token = sign_receipts_token(tenant_id=tid)
    except Exception:
        return ""
    base = f"{PROTECT_HOST}/autumbra/receipts/weekly/{tid}"
    return f"{base}?{urlencode({'token': token})}"


def default_undo_url(
    receipt_id: str,
    *,
    tenant_id: str = "",
    rule_id: str = "",
    site: str = "",
) -> str:
    """Undo landing page with a signed token naming tenant + shield.

    Empty when the secret is unset or inputs are incomplete — never a dead link.
    """
    if not tenant_id or not receipt_id:
        return ""
    try:
        token = sign_undo_token(
            tenant_id=tenant_id,
            receipt_id=receipt_id,
            rule_id=rule_id,
            site=site,
        )
    except Exception:
        return ""
    base = f"{PROTECT_HOST}/autumbra/undo/{receipt_id}"
    return f"{base}?{urlencode({'token': token})}"


def list_jobs() -> list[dict[str, Any]]:
    return [dict(WEEKLY_OWNER_UPDATE_JOB)]


def list_clients_with_owner(store) -> list[ClientAutumbraSettings]:
    """Every tenant that has stored Autumbra settings with an owner_email."""
    rows = store.db.execute(
        "SELECT tenant, value FROM ghost_state WHERE key=?",
        ("autumbra_client_settings",),
    ).fetchall()
    out: list[ClientAutumbraSettings] = []
    for tenant, _value in rows:
        settings = load_settings(store, str(tenant))
        if (settings.owner_email or "").strip():
            out.append(settings)
    return out


def _parse_receipt_time(receipt: dict[str, Any]) -> datetime | None:
    raw = receipt.get("timestamp") or receipt.get("at") or receipt.get("time") or ""
    if isinstance(raw, datetime):
        moment = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def week_bounds(
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """UTC week window: Monday 00:00 through now (or end of that Monday-week)."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=moment.weekday())
    return start, moment


def normalize_severity(value: Any) -> str:
    """Severity from a receipt enum or string → plain label (critical, high, …)."""
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value).strip().lower()
    text = str(value).strip()
    if not text:
        return ""
    # str(ThreatSeverity.critical) → "ThreatSeverity.critical"
    if "." in text and not text.lower().startswith("cve"):
        text = text.rsplit(".", 1)[-1]
    return text.lower()


def _is_our_receipt_page(url: str) -> bool:
    u = (url or "").strip().lower()
    return "protect.tervory.com/autumbra" in u or "/autumbra/receipts/" in u


def week_stats_from_receipts(
    receipts: Iterable[dict[str, Any]],
    *,
    tenant_id: str,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> dict[str, Any]:
    """Derive owner-email counts from this week's receipts only — never from a request body."""
    if week_start is None or week_end is None:
        week_start, week_end = week_bounds()
    threats = 0
    critical: list[dict[str, Any]] = []
    blocked = 0
    shield_source_url = ""
    shield_source_name = ""
    undo_receipt_id = ""
    undo_rule_id = ""
    undo_site = ""
    checks = 0
    seen_critical: set[str] = set()
    fix_pr_url = ""
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        if receipt.get("tenant_id") and str(receipt.get("tenant_id")) != tenant_id:
            continue
        moment = _parse_receipt_time(receipt)
        if moment is None or moment < week_start or moment > week_end:
            continue
        kind = str(receipt.get("kind") or "")
        outcome = str(receipt.get("outcome") or "")
        role = str(receipt.get("role") or "")
        if kind == "queue_quarantine":
            continue
        if role in {"Watch", "Enforce"} or outcome in {
            "queued", "applied", "undone", "awaiting_approval", "report_only",
            "auto_apply", "refused", "burned",
        }:
            checks += 1
        applied = bool(receipt.get("applied"))
        if applied or outcome in {"applied", "awaiting_approval", "report_only"}:
            threats += 1
            if applied and receipt.get("undo", {}).get("rule_id"):
                blocked += 1
                undo_receipt_id = str(receipt.get("id") or undo_receipt_id)
                undo_rule_id = str(receipt.get("undo", {}).get("rule_id") or undo_rule_id)
                undo_site = str(receipt.get("site") or undo_site)
            sev = normalize_severity(receipt.get("severity"))
            url = str(receipt.get("advisory_url") or "").strip()
            if sev in {"critical", "known_exploited"} and url:
                key = url
                if key not in seen_critical:
                    seen_critical.add(key)
                    cves = receipt.get("cve_ids") or []
                    advisory_id = cves[0] if cves else None
                    critical.append({
                        "stack": str(
                            receipt.get("stack")
                            or receipt.get("component")
                            or "software"
                        ),
                        "rating": sev,
                        "advisory_url": url,
                        "advisory_id": advisory_id,
                    })
                if not shield_source_url:
                    shield_source_url = url
                    shield_source_name = str(
                        receipt.get("source_name") or "public advisory"
                    )
            # Real fix PR only — never invent the weekly receipts page as a PR.
            pr = str(receipt.get("fix_pr_url") or receipt.get("pr_url") or "").strip()
            if pr and not _is_our_receipt_page(pr) and not fix_pr_url:
                fix_pr_url = pr
            src = str(receipt.get("source_url") or "").strip()
            if src and not _is_our_receipt_page(src) and not shield_source_url:
                shield_source_url = src
                shield_source_name = str(receipt.get("source_name") or "public advisory")
    shield = None
    if blocked:
        # Campaign source only when we have a real matched public source URL.
        if shield_source_url and not _is_our_receipt_page(shield_source_url):
            shield = {
                "blocked_addresses": blocked,
                "source_name": shield_source_name or "public advisory",
                "source_url": shield_source_url,
            }
        else:
            shield = {
                "blocked_addresses": blocked,
                "source_name": "",
                "source_url": "",
            }
    undo_url = ""
    if undo_receipt_id:
        undo_url = default_undo_url(
            undo_receipt_id,
            tenant_id=tenant_id,
            rule_id=undo_rule_id,
            site=undo_site,
        )
    # Signed links only — never a token-less URL the owner cannot use.
    if undo_url and "token=" not in undo_url:
        undo_url = ""
    # undo_fix_url only when a code fix PR exists — never the shield undo link
    # (an IP block must not trigger the "patched versions" auto-fix paragraph).
    undo_fix = undo_url if fix_pr_url else ""
    receipt_url = default_receipt_url(tenant_id)
    return {
        "threats_found": threats,
        "critical": critical,
        "shield": shield,
        "checks_run": checks,
        "undo_shield_url": undo_url,
        "receipt_url": receipt_url,
        # Empty unless a real fix PR / dedicated report exists on a receipt.
        "fix_report_url": "",
        "pr_url": fix_pr_url,
        "undo_fix_url": undo_fix,
        "week_start": week_start.isoformat().replace("+00:00", "Z"),
        "week_end": week_end.isoformat().replace("+00:00", "Z"),
    }


def build_owner_update_from_receipts(
    *,
    settings: ClientAutumbraSettings,
    receipts: Iterable[dict[str, Any]],
    receipt_url: str = "",
    undo_shield_url: str = "",
    fix_report_url: str = "",
    pr_url: str = "",
    undo_fix_url: str = "",
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> dict[str, str]:
    """Build the owner email entirely from this week's receipts + stored settings."""
    stats = week_stats_from_receipts(
        receipts,
        tenant_id=settings.tenant_id,
        week_start=week_start,
        week_end=week_end,
    )
    return build_owner_update(
        settings=settings,
        site_name=settings.site_name or "your site",
        threats_found=int(stats["threats_found"]),
        critical=list(stats["critical"]),
        shield=stats.get("shield"),
        fix_report_url=fix_report_url or str(stats.get("fix_report_url") or ""),
        pr_url=pr_url or str(stats.get("pr_url") or ""),
        undo_fix_url=undo_fix_url or str(stats.get("undo_fix_url") or ""),
        undo_shield_url=undo_shield_url or str(stats.get("undo_shield_url") or ""),
        receipt_url=receipt_url or str(stats.get("receipt_url") or default_receipt_url(settings.tenant_id)),
        checks_run=stats.get("checks_run"),
    )


def run_weekly_owner_updates(
    clients: Iterable[ClientAutumbraSettings],
    *,
    week_stats: dict[str, dict[str, Any]] | None = None,
    receipts_by_tenant: dict[str, list[dict[str, Any]]] | None = None,
    transport: Any | None = None,
    receipt_url_for: Callable[[ClientAutumbraSettings], str] | None = None,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> list[dict[str, Any]]:
    """Send one weekly email per client. Recipient from client settings only.

    Prefer receipts_by_tenant (real counts for the reported week). week_stats is
    accepted only when it was itself derived from receipts by the caller.
    """
    stats = week_stats or {}
    by_receipts = receipts_by_tenant or {}
    sender = transport
    results: list[dict[str, Any]] = []
    for settings in clients:
        to = (settings.owner_email or "").strip()
        if not to:
            results.append({
                "tenant_id": settings.tenant_id,
                "sent": False,
                "skipped": "no owner_email in client settings",
            })
            continue
        if settings.tenant_id in by_receipts:
            row = week_stats_from_receipts(
                by_receipts[settings.tenant_id],
                tenant_id=settings.tenant_id,
                week_start=week_start,
                week_end=week_end,
            )
        else:
            row = stats.get(settings.tenant_id) or {}
            if not row:
                row = week_stats_from_receipts(
                    [],
                    tenant_id=settings.tenant_id,
                    week_start=week_start,
                    week_end=week_end,
                )
        receipt_url = ""
        if receipt_url_for is not None:
            receipt_url = receipt_url_for(settings)
        receipt_url = receipt_url or str(row.get("receipt_url") or "")
        if not receipt_url:
            receipt_url = default_receipt_url(settings.tenant_id)
        shield = row.get("shield")
        undo_shield = str(row.get("undo_shield_url") or "")
        # Never substitute a receipts page (or any unsigned URL) as Undo.
        if undo_shield and "token=" not in undo_shield:
            undo_shield = ""
        # Unsigned weekly receipts page is dead — omit rather than email a broken link.
        if receipt_url and "token=" not in receipt_url and "/autumbra/receipts/" in receipt_url:
            receipt_url = ""
        email = build_owner_update(
            settings=settings,
            site_name=settings.site_name or "your site",
            threats_found=int(row.get("threats_found") or 0),
            critical=list(row.get("critical") or []),
            shield=shield,
            fix_report_url=str(row.get("fix_report_url") or ""),
            pr_url=str(row.get("pr_url") or ""),
            undo_fix_url=str(row.get("undo_fix_url") or ""),
            undo_shield_url=undo_shield,
            receipt_url=receipt_url,
            checks_run=row.get("checks_run"),
        )
        start = week_start or week_bounds()[0]
        sent = send_owner_update(email, to=to, transport=sender, tenant_id=settings.tenant_id,
                                 message_key="weekly:" + start.isoformat())
        results.append({
            "tenant_id": settings.tenant_id,
            "job_id": WEEKLY_OWNER_UPDATE_JOB["id"],
            **sent,
        })
    return results
