"""Record a client rejection as an unverified price-read example.

No screen. A caller hands in the page and the price the client says is right.
The row is stored locally and stays unverified until a person checks it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INBOX = Path(__file__).resolve().parent / "gold_inbox" / "corrections.jsonl"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")


def _canonical(raw: str) -> str | None:
    text = raw.strip().replace("\xa0", "").replace(" ", "")
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", text):
            text = text.replace(",", "")
        elif re.fullmatch(r"-?\d+,\d{2}", text):
            text = text.replace(",", ".")
        else:
            return None
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    whole, _, frac = text.partition(".")
    return f"{int(whole)}.{(frac + '00')[:2]}"


def record_correction(
    *,
    page_id: str,
    layout: str,
    expected_price: str,
    html: str,
    url: str = "",
    currency: str | None = None,
    note: str = "",
    inbox: Path = INBOX,
) -> dict[str, Any]:
    """Append one unverified gold example. Refuses a price that is not a price."""
    if not _ID.fullmatch(page_id):
        raise ValueError("page id must be a short slug")
    if not layout.strip():
        raise ValueError("layout is required")
    price = _canonical(expected_price)
    if price is None:
        raise ValueError("correction needs the price the client says is on the page")
    if not html.strip():
        raise ValueError("correction needs the page html")
    inbox.parent.mkdir(parents=True, exist_ok=True)
    html_path = inbox.parent / f"{page_id}.html"
    html_path.write_text(html, encoding="utf-8")
    row = {
        "id": page_id,
        "source": "client_correction",
        "layout": layout.strip(),
        "url": url,
        "html": html_path.name,
        "expected_price": price,
        "currency": currency.upper() if currency else None,
        "note": note.strip(),
        "split": "inbox",
        "verified": False,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with inbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
