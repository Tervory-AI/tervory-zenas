"""ZENAS_REVIEW, per box. Default off.

Off: a rule card that passes cite-check is active and is reused at once.
On: only a lawyer-approved card is reused. The review queue is unchanged.

The scope gate, identifier protection, and cite-check stay on either way.
"""

from __future__ import annotations

import os
from typing import Any


def env_review_on() -> bool:
    raw = os.environ.get("ZENAS_REVIEW", "off").strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def review_required(ladder: Any = None, *, region: str | None = None) -> bool:
    """True only when this box's switch is on. A missing row uses the env, default off."""
    if ladder is not None:
        mode = getattr(ladder, "review_mode", None)
        if mode in ("on", "off"):
            return mode == "on"
        getter = getattr(ladder, "get_review", None)
        if getter is not None and region:
            try:
                stored = await getter(region=region)
            except Exception:
                stored = None
            if stored in ("on", "off"):
                return stored == "on"
    return env_review_on()


def card_servable(card: dict[str, Any], *, review_on: bool) -> bool:
    status = str(card.get("status") or "")
    if status == "stale":
        return False
    if status == "lawyer_approved":
        return True
    return bool(card.get("active")) and not review_on and status == "draft"
