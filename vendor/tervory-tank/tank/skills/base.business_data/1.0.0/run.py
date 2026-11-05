"""base.business_data — Pass 5, business data enrichment. One small-model call, strict JSON.

From everything the earlier passes established (description, categories, services, named people,
social presence, site size) estimate: size band, revenue band, business model, NAICS code. These
are estimates and are labelled as such: source="model:estimate", confidence never above 60.
Reviews/BBB pullers need provider keys and arrive as later versions.
"""

import json
import re

SIZE_BANDS = ("solo", "2-10", "11-50", "51-200", "200+")
REVENUE_BANDS = ("<$500k", "$500k-$2M", "$2M-$10M", "$10M+")
MODELS = ("retail", "online retail", "wholesale", "manufacturer", "service", "mixed")

PROMPT = """Estimate the size and shape of this business from the signals below. Reply with JSON only — start your reply with "{{" and write nothing before or after the object — exactly:
{{"size_band": one of {sizes}, "revenue_band": one of {revs}, "business_model": one of {models}, "naics": "6-digit code", "naics_label": "short label", "basis": "one short sentence on what you used", "sure": true|false}}
Be conservative: a single-location shop with no named staff is "solo" or "2-10". Set "sure" false when signals are thin.

Name: {name}
State: {state}
Description: {description}
Sells: {categories}
Services: {services}
Named people: {people}
Social profiles: {socials}
Site pages seen: {pages}
Registry licence type: {licence}"""


def _json(s: str) -> dict:
    """First JSON object in the reply. Tolerates code fences, prose before it, and a reply cut
    off before the closing brace (the fields already emitted are kept)."""
    start = s.find("{")
    if start < 0:
        return {}
    body = s[start:]
    end = body.rfind("}")
    if end >= 0:
        try:
            return json.loads(body[: end + 1])
        except json.JSONDecodeError:
            pass
    # Truncated: drop the dangling partial pair and close the object.
    cut = body.rfind(",")
    while cut > 0:
        try:
            return json.loads(body[:cut] + "}")
        except json.JSONDecodeError:
            cut = body.rfind(",", 0, cut)
    return {}


def _pick(v, allowed, default):
    v = str(v or "").strip().lower().replace("–", "-").replace(" ", "")
    for a in allowed:
        if v == a.lower().replace(" ", ""):
            return a
    return default


async def run(payload: dict, ctx) -> dict:
    name = str(payload.get("name") or "")
    desc = str(payload.get("description") or "")
    cats = payload.get("categories") or []
    svcs = payload.get("services") or []
    people = payload.get("people") or []
    thin = not desc and not cats and not svcs
    if thin:
        return {"size_band": None, "revenue_band": None, "business_model": None, "naics": None, "naics_label": None,
                "basis": "no signals from the site", "confidence": 0}
    raw = await ctx.model([{"role": "user", "content": PROMPT.format(
        sizes=list(SIZE_BANDS), revs=list(REVENUE_BANDS), models=list(MODELS),
        name=name, state=payload.get("state") or "", description=desc[:400],
        categories=", ".join(map(str, cats))[:200], services=", ".join(map(str, svcs))[:200],
        people=", ".join(f"{p.get('name')} ({p.get('role')})" for p in people if isinstance(p, dict))[:200] or "none",
        socials=payload.get("socials_count") or 0, pages=payload.get("pages") or 0,
        licence=payload.get("licence_type_label") or "n/a",
    )}], task_type="base.business_data", max_tokens=500)
    d = _json(raw)
    naics = re.sub(r"\D", "", str(d.get("naics") or ""))[:6]
    sure = bool(d.get("sure", False))
    if not d:
        # Nothing parseable: say so loudly instead of returning a confident-looking empty estimate.
        return {"size_band": None, "revenue_band": None, "business_model": None, "naics": None, "naics_label": None,
                "basis": "model reply was not JSON", "raw": raw[:300], "confidence": 0}
    return {
        "size_band": _pick(d.get("size_band"), SIZE_BANDS, None),
        "revenue_band": _pick(d.get("revenue_band"), REVENUE_BANDS, None),
        "business_model": _pick(d.get("business_model"), MODELS, None),
        "naics": naics if len(naics) == 6 else None,
        "naics_label": str(d.get("naics_label") or "")[:60] or None,
        "basis": str(d.get("basis") or "")[:200],
        "raw": raw[:300],
        "confidence": 60 if sure else 40,
    }
