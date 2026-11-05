"""base.extract — Pass 2c. Structured facts from site text via a small model. Strict JSON."""

import json
import re

PROMPT = """You extract facts about a business from its website text. Reply with JSON only, no prose, exactly this shape:
{{"categories": ["..."], "services": ["..."], "description": "one sentence", "people": [{{"name": "...", "role": "..."}}], "explicit": true}}
Rules: categories = what they sell (max 6 short nouns). services = what they do for customers (max 6). people only if a person is named with a role. Set "explicit" false if the text barely describes the business. Empty lists are fine. Never invent.

Business: {name}
Website text:
{text}"""


def _json(s: str) -> dict:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


async def run(payload: dict, ctx) -> dict:
    name = str(payload.get("name") or "")
    text = str(payload.get("text") or "")[:6000]
    if len(text) < 40:
        return {"categories": [], "services": [], "description": "", "people": [], "confidence": 0}
    raw = await ctx.model([{"role": "user", "content": PROMPT.format(name=name, text=text)}], task_type="base.extract", max_tokens=600)
    data = _json(raw)
    explicit = bool(data.get("explicit", True))
    return {
        "categories": [str(c)[:40] for c in (data.get("categories") or [])][:6],
        "services": [str(c)[:60] for c in (data.get("services") or [])][:6],
        "description": str(data.get("description") or "")[:300],
        "people": [p for p in (data.get("people") or []) if isinstance(p, dict) and p.get("name")][:5],
        "confidence": 75 if explicit else 45,
    }
