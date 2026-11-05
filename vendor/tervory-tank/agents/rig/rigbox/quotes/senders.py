"""Store email and existing tenant webhook delivery; no invoicing or payment.

Configuration and credentials never reach shoppers. Custom Nation requests go
to the store, which owns its invoice and EFT process.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlsplit


class DeliveryError(Exception):
    """Safe, fixed error code. Never include a remote response or credentials."""


def https_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise DeliveryError("configuration") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise DeliveryError("configuration")
    return value.rstrip("/")


def flow_for(tenant_id: str) -> dict[str, Any]:
    try:
        flows = json.loads(os.environ.get("RIG_QUOTE_FLOWS") or "{}")
        flow = flows.get(str(tenant_id), {})
        if not isinstance(flow, dict):
            raise ValueError
        return flow
    except (ValueError, AttributeError):
        raise DeliveryError("configuration") from None


class EmailHandoff:
    """Use the stack's existing SMTP transport; delivery state belongs to PgQuotes."""

    async def send(self, email: str | None, quote: dict[str, Any]) -> None:
        from rigbox.reserve import SmtpMailer, reservation_letter

        try:
            mailer = SmtpMailer.from_env()
            if not mailer or not email:
                raise DeliveryError("not_configured")
            reference = quote["id"]
            letter = reservation_letter(reference, quote["payload"], quote.get("contact", {}), {"email": email})
            letter["subject"] = f"Quote request: {reference}"
            letter["text"] = "\n".join(f"Subject: {letter['subject']}" if line.startswith("Subject: ") else line
                                       for line in letter["text"].splitlines())
            # Link quotes have no product/setup yet. Keep the shopper's request.
            if quote["payload"].get("kind") == "link":
                letter["text"] += f"\nLink: {quote['payload']['url']}\nNote: {quote['payload'].get('note', '')}"
            letter["message_id"] = f"<{reference}@rig.tervory.com>"
            await mailer.send(letter)
        except DeliveryError:
            raise
        except Exception:
            # SMTP exceptions can contain recipients, credentials or remote text.
            raise DeliveryError("email_unavailable") from None


class RetailerHandoff:
    """Implements Service's existing sender interface, selected by exact tenant."""

    def __init__(self, http: Any) -> None:
        self.http = http

    async def send(self, *, tenant_id: str, email: str | None, webhook: str | None,
                   quote: dict[str, Any]) -> None:
        flow = flow_for(tenant_id)
        sender = flow.get("sender", "email" if email and not webhook else "webhook")
        if sender == "email":
            await EmailHandoff().send(email, quote)
            return
        if sender != "webhook":
            raise DeliveryError("configuration")
        if not webhook:
            raise DeliveryError("not_configured")
        try:
            response = await self.http.post(https_url(webhook),
                json={"tenant_id": tenant_id, "quote": quote},
                headers={"Idempotency-Key": f"rig:{tenant_id}:{quote['id']}"},
                timeout=20, follow_redirects=False)
            response.raise_for_status()
        except DeliveryError:
            raise
        except Exception:
            raise DeliveryError("retailer_unavailable") from None

    def portal(self, tenant_id: str) -> str | None:
        url = flow_for(tenant_id).get("portal_url")
        return https_url(url) if isinstance(url, str) and url else None
