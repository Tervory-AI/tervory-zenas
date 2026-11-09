"""Owner update email — built by code from receipts. No model. Plain English only.

Exact founder wording (item 8 phrases). Founder brief is in-repo byte-identical.
"critical" only with a linked advisory rating. No literal "[...]" placeholders in
a sent body. Recipient from client settings. Weekly schedule is a real job entry.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .client_settings import ApplyMode, ClientAutumbraSettings

_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")


def _link(label: str, url: str) -> str:
    url = (url or "").strip()
    label = (label or "").strip()
    if not url:
        raise ValueError(f"missing URL for link label {label!r}")
    return f"{label} ({url})"


def _fmt_count(n: int) -> str:
    return f"{int(n):,}"


def _assert_no_placeholders(body: str) -> None:
    if _PLACEHOLDER_RE.search(body or ""):
        raise ValueError("owner email body must not contain [...] placeholders")


_DASHBOARD_URL = "https://protect.tervory.com/autumbra"


def _count_link(label: str, receipt_url: str) -> str:
    """Link a count to the signed receipts page, or the signed-in dashboard if unsigned."""
    url = (receipt_url or "").strip()
    if url and "token=" in url:
        return _link(label, url)
    return _link(label, _DASHBOARD_URL)


def _receipt_footer(receipt_url: str, *, acted: bool = True) -> str:
    url = (receipt_url or "").strip()
    privacy = ("Nothing from your logs was kept. We read them, acted, and deleted them. "
               if acted else "Nothing from your logs is kept. ")
    if url and "token=" in url:
        return (
            privacy +
            f"{_link('Full receipt', url)}"
        )
    return (
        privacy +
        f"Sign in at {_link('your Autumbra dashboard', _DASHBOARD_URL)} to see your receipts."
    )


def build_owner_update(
    *,
    settings: ClientAutumbraSettings,
    site_name: str,
    threats_found: int,
    critical: list[dict[str, Any]],
    shield: dict[str, Any] | None = None,
    fix_report_url: str = "",
    pr_url: str = "",
    undo_fix_url: str = "",
    undo_shield_url: str = "",
    receipt_url: str = "",
    checks_run: int | None = None,
) -> dict[str, str]:
    """Exact founder wording. Numbers must link to the receipt behind them."""
    site = site_name or settings.site_name or "your site"
    n = int(threats_found)

    cleaned_critical: list[dict[str, Any]] = []
    for item in critical or []:
        if not isinstance(item, dict):
            raise ValueError("critical item must be an object")
        rating = str(item.get("rating") or "").lower()
        url = str(item.get("advisory_url") or "").strip()
        if rating and rating not in {"critical", "known_exploited"}:
            raise ValueError("critical paragraph requires a critical public advisory rating")
        if not rating or not url:
            raise ValueError(
                "critical means a public advisory rates it critical and the link proves it"
            )
        cleaned_critical.append(item)
    c = len(cleaned_critical)
    subject = f"Autumbra update for {site}: {n} threats found, {c} critical"

    # Quiet week — brief: "Quiet week. [1,204] checks, 0 threats, 0 actions."
    if n == 0 and c == 0 and not shield:
        checks = int(checks_run if checks_run is not None else 0)
        first_action = "Connect your site's log feed to start a check.\n\n" if checks == 0 else ""
        body = (
            f"Quiet week. {_count_link(_fmt_count(checks), receipt_url)} "
            f"checks, 0 threats, 0 actions.\n\n"
            f"{first_action}{_receipt_footer(receipt_url, acted=False)}"
        )
        _assert_no_placeholders(body)
        return {"subject": subject, "body": body}

    stacks: list[str] = []
    linked_stacks: list[str] = []
    for item in cleaned_critical:
        name = str(item.get("stack") or item.get("name") or "software")
        stacks.append(name)
        linked_stacks.append(_link(name, str(item["advisory_url"])))
    if not stacks:
        stack_list_linked = "software"
    elif len(stacks) == 1:
        stack_list_linked = linked_stacks[0]
    else:
        stack_list_linked = ", ".join(linked_stacks[:-1]) + " and " + linked_stacks[-1]

    # Lead sentence always states what happened. Critical / fix-PR / campaign
    # sentences are omitted unless the matching proof exists.
    lead = (
        f"This week we found {_count_link(str(n), receipt_url)} "
        f"threats to {site}."
    )
    if c > 0:
        lead += (
            f" {_count_link(str(c), receipt_url)} were critical: out-of-date "
            f"{stack_list_linked} with known holes serious enough to "
            f"take down a company's whole IT system. "
            f"(Each linked to its public advisory.)"
        )
    body_lines = [lead, ""]

    def _url_host(url: str) -> str | None:
        try:
            parsed = urlparse((url or "").strip())
        except Exception:
            return None
        if parsed.scheme not in {"http", "https"}:
            return None
        host = (parsed.hostname or "").lower()
        return host or None

    def _host_matches(host: str | None, name: str) -> bool:
        if not host:
            return False
        return host == name or host.endswith("." + name)

    def _is_real_pr_url(url: str) -> bool:
        # Host checks via urlparse — not substring-in-URL (CodeQL).
        try:
            parsed = urlparse((url or "").strip())
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        if _host_matches(host, "protect.tervory.com"):
            return False
        path = (parsed.path or "").lower()
        if "/autumbra/" in path:
            return False
        return _host_matches(host, "github.com") or _host_matches(host, "gitlab.com")

    def _is_matched_public_source(url: str) -> bool:
        try:
            parsed = urlparse((url or "").strip())
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        # Our own receipt pages are not a matched public campaign source.
        if _host_matches(host, "protect.tervory.com"):
            return False
        path = (parsed.path or "").lower()
        if _host_matches(host, "tervory.com") and "/autumbra" in path:
            return False
        return True

    mode = settings.fix_mode
    if mode is ApplyMode.approve_each:
        # "we built the fix" only when a real fix PR exists with its link.
        if _is_real_pr_url(pr_url):
            body_lines.append(
                f"You chose to approve fixes yourself. We have built the fix and it is "
                f"waiting for your approval here. {_link('Approve the fix', pr_url)} "
                f"Until you approve it, the hole is still open; we have reduced the risk at "
                f"the edge as described below."
            )
        else:
            body_lines.append(
                "You chose to approve fixes yourself. "
                "We have reduced the risk at the edge as described below."
            )
    else:
        # Dedicated fix report (not the weekly receipts page) keeps the Report link.
        report = (fix_report_url or "").strip()
        report_host = _url_host(report)
        report_path = ""
        if report:
            try:
                report_path = (urlparse(report).path or "").lower()
            except Exception:
                report_path = ""
        if report and not (
            _host_matches(report_host, "protect.tervory.com")
            and "/autumbra/receipts" in report_path
        ):
            body_lines.append(
                f"You chose that we never touch your code. Here is exactly what to fix and how. "
                f"{_link('Report', report)} "
                f"We have reduced the risk at the edge as described below."
            )
        else:
            body_lines.append(
                "You chose that we never touch your code. "
                "We have reduced the risk at the edge as described below."
            )

    if shield:
        blocked = int(shield.get("blocked_addresses") or 0)
        source_url = str(shield.get("source_url") or "").strip()
        source_name = str(shield.get("source_name") or "public source").strip()
        campaign = _is_matched_public_source(source_url)
        if campaign:
            public_source = _link(source_name, source_url)
            campaign_clause = (
                f"It was part of a widely reported campaign ({public_source}). "
            )
        else:
            campaign_clause = ""
        signed_undo = (undo_shield_url or "").strip()
        if signed_undo and "token=" not in signed_undo:
            signed_undo = ""
        if signed_undo:
            undo_clause = (
                "If this caused any problem, undo it here with one tap. "
                f"{_link('Undo', signed_undo)}"
            )
        else:
            # Secret unset or signing failed — never promise a dead one-tap link.
            undo_clause = (
                "If this caused any problem, sign in at "
                f"{_link('your Autumbra dashboard', _DASHBOARD_URL)} "
                "or reply to this email and we will remove the block."
            )
        body_lines.extend([
            "",
            f"We also stopped an attack in progress. {campaign_clause}"
            f"We blocked {_count_link(str(blocked), receipt_url)} "
            f"addresses that were attacking your site. Your own addresses, your payment "
            f"provider and Cloudflare were never blocked. {undo_clause}",
        ])

    body_lines.extend([
        "",
        _receipt_footer(receipt_url),
    ])

    body = "\n".join(body_lines)
    _assert_no_placeholders(body)
    return {"subject": subject, "body": body}


class FixtureEmailTransport:
    """In-memory sender used by tests and dogfood. Records every send."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        if not to:
            raise ValueError("recipient must come from the client's settings")
        if to == "owner@example.test":
            raise ValueError("recipient must come from the client's settings")
        _assert_no_placeholders(body)
        row = {"to": to, "subject": subject, "body": body}
        self.sent.append(row)
        return {"sent": True, "transport": "fixture", "to": to, "subject": subject}


DEFAULT_FIXTURE_TRANSPORT = FixtureEmailTransport()


def send_owner_update(
    email: dict[str, str],
    *,
    to: str,
    transport: Any | None = None,
    tenant_id: str = "",
    message_key: str = "",
) -> dict[str, Any]:
    """Call the sender. Weekly/quiet-week paths use the same door."""
    if not to:
        raise ValueError("recipient must come from the client's settings")
    if transport is None and tenant_id:
        from .smtp_email import configured_transport
        sender = configured_transport(tenant_id, message_key)
    else:
        sender = transport or DEFAULT_FIXTURE_TRANSPORT
    result = sender.send(to=to, subject=email["subject"], body=email["body"])
    return {
        "sent": bool(result.get("sent")),
        "delivery_state": result.get("state"),
        "transport": result.get("transport") or type(sender).__name__,
        "to": to,
        "subject": email["subject"],
        "body": email["body"],
        "receipt": {
            "product": "Autumbra",
            "kind": "owner_email",
            "to": to,
            "subject": email["subject"],
            "transport": result.get("transport") or type(sender).__name__,
        },
    }
