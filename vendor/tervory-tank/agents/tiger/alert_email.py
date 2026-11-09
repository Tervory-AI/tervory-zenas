"""Tenant-scoped alert mail. Copy comes only from the existing Tiger alert row.

Rig's SMTP implementation is reused unchanged. A sending record is never
reclaimed automatically: an interrupted SMTP exchange may already be accepted.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import smtplib
import socket
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.headerregistry import Address
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from agents.rig.rigbox.reserve import SmtpMailer


class MailConfigurationError(ValueError):
    pass


def _address(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(c in value for c in "\r\n")
    ):
        raise MailConfigurationError("mail_not_configured")
    try:
        address = Address(addr_spec=value.strip())
        if not address.username or not address.domain:
            raise ValueError
    except ValueError:
        raise MailConfigurationError("mail_not_configured") from None
    return address.addr_spec


def configuration(tenant_id: str, *, prefix: str = "TIGER") -> tuple[SmtpMailer, str]:
    """Exact tenant mapping; never select another tenant or a default recipient."""
    if prefix not in {"TIGER", "ZENAS", "MARKET_LAB"}:
        raise MailConfigurationError("mail_not_configured")
    try:
        tenant = str(UUID(tenant_id))
        addresses = json.loads(os.environ.get(f"{prefix}_SMTP_ALERT_ADDRESSES") or "{}")
        if not isinstance(addresses, dict):
            raise ValueError
        recipient = _address(addresses.get(tenant))
        host = os.environ.get(f"{prefix}_SMTP_HOST", "").strip()
        sender = _address(os.environ.get(f"{prefix}_SMTP_FROM", ""))
        port = int(os.environ.get(f"{prefix}_SMTP_PORT") or "587")
        if not host or not 0 < port < 65536:
            raise ValueError
        return SmtpMailer(
            host,
            port,
            os.environ.get(f"{prefix}_SMTP_USER", "").strip(),
            os.environ.get(f"{prefix}_SMTP_PASSWORD", ""),
            sender,
        ), recipient
    except (ValueError, TypeError, AttributeError):
        raise MailConfigurationError("mail_not_configured") from None


def _price(value: Any, currency: str) -> str:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0 or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError
    return ("$" if currency == "USD" else currency + " ") + f"{amount:.2f}"


def _stock(value: Any) -> str:
    text = re.sub(r"[_\s-]", "", str(value or "")).lower()
    for token, label in [
        ("outofstock", "Out of stock"),
        ("instock", "In stock"),
        ("preorder", "Pre-order"),
    ]:
        if token in text:
            return label
    raise ValueError


def letter_for(alert: dict) -> dict[str, str] | None:
    """Existing layout, with the three G5 lines approved on 28 October.

    No greeting, footer, invented subject or replacement for missing data.
    """
    try:
        name = str(alert.get("product_name") or "").strip()
        url = str(alert.get("url") or "")
        parsed = urlsplit(url)
        if (
            not name
            or any(c in name for c in "\r\n")
            or re.fullmatch(r"untitled\s+page", name, re.I)
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
        ):
            return None
        if alert["kind"] == "price":
            currency = str(alert.get("currency") or "").strip().upper()
            before, after = (
                _price(alert[key], currency) for key in ("before", "after")
            )
            direction = (
                "down"
                if Decimal(str(alert["after"])) < Decimal(str(alert["before"]))
                else "up"
            )
            change = f"Price went {direction} from {before} to {after}"
        elif alert["kind"] == "stock":
            change = f"Stock changed from {_stock(alert['before'])} to {_stock(alert['after'])}."
        elif alert["kind"] == "new_product":
            change = f"New product listed: {name}"
        elif alert["kind"] == "below_cost":
            currency = str(alert.get("currency") or "").strip().upper()
            price, cost = _price(alert["after"], currency), _price(alert["before"], currency)
            change = f"Competitor price {price} is below your cost {cost}"
        elif alert["kind"] == "market_gap":
            change = "All watched competitors are out of stock. You are in stock."
        else:
            return None
        return {"subject": change, "text": "\n".join((name, change, url))}
    except (ValueError, TypeError, KeyError, InvalidOperation):
        return None


async def deliver_pending(
    store: Any, tenant_id: str, *, transport=None, recipient=None, clock=None, namespace="tiger", dry_run=False
) -> dict:
    """Retry definite rejections; never repeat an accepted/uncertain SMTP send."""
    if namespace not in {"tiger", "zenas", "market_lab"}:
        raise MailConfigurationError("mail_not_configured")
    clock = clock or (lambda: datetime.now(timezone.utc))
    if dry_run:
        from readiness import missing
        return {'dry_run': True, 'sent': 0,
                'pending': len((await store.pending_mail(tenant_id, clock()))[:3]),
                'settings': missing(*(namespace.upper() + suffix for suffix in
                                      ('_SMTP_HOST', '_SMTP_FROM', '_SMTP_ALERT_ADDRESSES')))}
    if transport is None:
        try:
            transport, recipient = configuration(tenant_id, prefix=namespace.upper())
        except MailConfigurationError:
            return {"mail": "not_configured", "sent": 0}
    recipient = _address(recipient)
    counts = {"sent": 0, "pending": 0, "uncertain": 0, "suppressed": 0, "failed": 0}
    # Bound the dispatch job; subsequent minute ticks drain the remaining rows.
    for alert_id in (await store.pending_mail(tenant_id, clock()))[:3]:
        record = await store.claim_mail(tenant_id, alert_id, clock())
        if record is None:
            continue
        if not record["allowed"]:
            await store.finish_mail(tenant_id, alert_id, "suppressed", clock())
            counts["suppressed"] += 1
            continue
        letter = {
            **record["letter"],
            "to": recipient,
            "message_id": f"<{namespace}.{tenant_id}.{alert_id}@tervory.com>",
        }
        try:
            async with asyncio.timeout(30):
                await transport.send(letter)
        except (
            smtplib.SMTPDataError,
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPAuthenticationError,
            smtplib.SMTPConnectError,
            smtplib.SMTPHeloError,
            smtplib.SMTPNotSupportedError,
            ConnectionRefusedError,
            socket.gaierror,
        ):
            # The SMTP transaction was explicitly rejected (or never connected).
            state = "failed" if record["attempts"] >= 3 else "pending"
        except Exception:
            # A timeout/disconnect may follow DATA acceptance. Re-sending could
            # duplicate mail. Keep the receipt for operator reconciliation.
            state = "uncertain"
        else:
            state = "sent"
        await store.finish_mail(tenant_id, alert_id, state, clock())
        counts[state] += 1
    if counts['pending'] or counts['uncertain'] or counts['failed']:
        counts['error'] = 'Tiger is not reachable right now.'
    return counts
