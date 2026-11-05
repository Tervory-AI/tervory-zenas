"""tier0.classify: Classifies a request into the agent that should handle it: tier0, base, tiger, market_lab, radix, tisan or zenas. Label only."""

import re

LABELS = ("market_lab", "tier0", "base", "tiger", "radix", "tisan", "zenas")  # longest first so market_lab wins over base

# The model has to know what each name means. These are the Squad roles from the Build Pack.
TEMPLATE = """Route this request to the one team member who should handle it.

tier0      — quick questions, short drafts, summaries; anything answerable in a minute with no data work
base       — data: build, clean, match, enrich or deduplicate lists and records; find websites, emails, phones
tiger      — watch prices and competitors over time and send alerts; price monitoring
market_lab — research a market: trends, supply and demand, forecasts, reports
radix      — security: vulnerability scans, code and website security review, compliance checks
tisan      — build or change websites, apps, automations, integrations, infrastructure
zenas      — legal: contracts, NDAs, terms, compliance questions, trademarks

Request:
{text}

Reply with the label only."""


def _label(raw: str) -> str:
    """Pull the first known label out of whatever the model said. Unknown -> tier0,
    because a wrong 'tier0' is cheap and visible; a wrong 'zenas' is not."""
    low = raw.lower().replace("-", "_")
    for lab in LABELS:
        if re.search(rf"\b{lab}\b", low):
            return lab
    return "tier0"


async def run(payload: dict, ctx) -> dict:
    text = str(payload.get("text", "")).strip()
    if not text:
        return {"text": "", "error": "empty input"}
    # No output cap here: Haiku returned an empty string when capped at 16 tokens.
    # The route.request policy row already limits output to 64 tokens.
    raw = await ctx.model(
        [{"role": "user", "content": TEMPLATE.format(text=text)}],
        task_type="route.request",
    )
    return {"text": _label(raw), "raw": raw.strip()}
