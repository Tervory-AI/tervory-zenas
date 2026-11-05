"""Reservation letter for a shop. The address comes from the shop's publish
settings. This module does not open a network connection.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any


def reservation_letter(
    reference: str,
    payload: dict[str, Any],
    contact: dict[str, Any],
    retailer: dict[str, Any],
) -> dict[str, str]:
    product = _product_name(payload)
    shop_name = str(retailer.get("name") or "").strip()
    # Custom Nation asks for a quote. Other shops keep the reservation subject.
    if shop_name == "Custom Nation":
        subject = f"Quote request: {product} - {reference}"
    else:
        subject = f"Reservation: {product} - {reference}"
    lines = [
        f"Reference: {reference}",
        f"Shop: {str(retailer.get('name') or '').strip()}",
        f"To: {str(retailer.get('email') or '').strip()}",
        f"Subject: {subject}",
        f"Customer-Name: {_clean(contact.get('name'))}",
        f"Customer-Email: {_clean(contact.get('email'))}",
        f"Customer-Phone: {_clean(contact.get('phone'))}",
        "",
    ]
    total_amount = ""
    total_currency = ""
    total_label = ""
    setup = payload.get("setup") if isinstance(payload.get("setup"), dict) else None
    if setup is not None:
        for index, part in enumerate(setup.get("parts") or [], start=1):
            lines.extend(_item_lines(index, part))
            lines.append("")
        total = setup.get("total") if isinstance(setup.get("total"), dict) else {}
        total_amount = str(total.get("amount") or "")
        total_currency = str(total.get("currency") or "")
        total_label = _label_from_parts(setup)
    else:
        product_row = payload.get("product") if isinstance(payload.get("product"), dict) else {}
        lines.extend(_item_lines(1, {"product": product_row, "choice": ""}))
        lines.append("")
        price = product_row.get("price") if isinstance(product_row.get("price"), dict) else {}
        total_amount = str(price.get("amount") or "")
        total_currency = str(price.get("currency") or "")
        total_label = _price_label(product_row)
    shop = str(retailer.get("name") or "The shop").strip() or "The shop"
    lines.extend(
        [
            f"Total-Amount: {total_amount}",
            f"Total-Currency: {total_currency}",
            f"Total-Label: {total_label}",
            f"Note: {shop} confirms stock and paperwork before anything is final. Nothing is held until they confirm.",
        ]
    )
    return {
        "to": str(retailer.get("email") or "").strip(),
        "subject": subject,
        "text": "\n".join(lines),
        "reference": reference,
        "reply_to": _clean(contact.get("email")),
    }


class SmtpMailer:
    """Sends one plain-text reservation. Built only when SMTP_HOST is set."""

    def __init__(self, host: str, port: int, username: str, password: str, sender: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender

    @classmethod
    def from_env(cls) -> SmtpMailer | None:
        host = (os.environ.get("SMTP_HOST") or "").strip()
        if not host:
            return None
        port = int(os.environ.get("SMTP_PORT") or "587")
        return cls(
            host,
            port,
            (os.environ.get("SMTP_USER") or "").strip(),
            os.environ.get("SMTP_PASSWORD") or "",
            (os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "").strip(),
        )

    async def send(self, letter: dict[str, str]) -> None:
        if not letter.get("to") or not self.sender:
            raise RuntimeError("mail is not configured")
        message = EmailMessage()
        message["Subject"] = letter.get("subject") or ""
        message["From"] = self.sender
        message["To"] = letter["to"]
        if letter.get("message_id"):
            message["Message-ID"] = letter["message_id"]
        reply_to = str(letter.get("reply_to") or "").strip()
        if reply_to:
            message["Reply-To"] = reply_to
        message.set_content(letter.get("text") or "")
        await asyncio.to_thread(self._deliver, message)

    def _deliver(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)


def _product_name(payload: dict[str, Any]) -> str:
    setup = payload.get("setup") if isinstance(payload.get("setup"), dict) else None
    if setup is not None:
        parts = setup.get("parts") or []
        if parts:
            return str((parts[0].get("product") or {}).get("name") or "Item")
    product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
    return str(product.get("name") or "Item")


def _item_lines(index: int, part: dict[str, Any]) -> list[str]:
    product = part.get("product") if isinstance(part.get("product"), dict) else {}
    price = product.get("price") if isinstance(product.get("price"), dict) else {}
    return [
        f"Item: {index}",
        f"Item-Code: {product.get('id') or ''}",
        f"Item-Name: {_clean(product.get('name'))}",
        "Item-Qty: 1",
        f"Item-Amount: {price.get('amount') or ''}",
        f"Item-Currency: {price.get('currency') or ''}",
        f"Item-Price-Label: {_price_label(product)}",
        f"Item-Choice: {_clean(part.get('choice'))}",
        f"Item-Stock: {_stock_line(product)}",
    ]


def _price_label(product: dict[str, Any]) -> str:
    attributes = product.get("attributes") if isinstance(product.get("attributes"), dict) else {}
    label = str(attributes.get("price_label") or "").strip()
    if label:
        return label
    return "Indicative"


def _label_from_parts(setup: dict[str, Any]) -> str:
    parts = setup.get("parts") or []
    if not parts:
        return "Indicative"
    return _price_label(parts[0].get("product") or {})


def _stock_line(product: dict[str, Any]) -> str:
    availability = product.get("availability") if isinstance(product.get("availability"), dict) else {}
    if not availability:
        attributes = product.get("attributes") if isinstance(product.get("attributes"), dict) else {}
        stock = attributes.get("stock") if isinstance(attributes.get("stock"), dict) else {}
        availability = stock
    status = str(availability.get("status") or "").strip()
    label = str(availability.get("label") or "").strip()
    lead = str(availability.get("lead") or "").strip()
    parts = [part for part in (status, label, lead) if part]
    return " · ".join(parts)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").replace("\r", " ").split())
