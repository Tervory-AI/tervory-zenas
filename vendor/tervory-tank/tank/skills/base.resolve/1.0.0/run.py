"""base.resolve — Pass 1b. Fuzzy entity resolution against a registry. Pure code (RapidFuzz)."""

from rapidfuzz import fuzz, process


def _key(rec: dict) -> tuple[str, str, str]:
    f = rec.get("fields") or {}
    g = lambda k: str((f.get(k) or {}).get("value") or "").lower()  # noqa: E731
    return g("name_key"), g("city"), g("state")


async def run(payload: dict, ctx) -> dict:
    record = payload.get("record") or {}
    registry = payload.get("registry") or []
    name_key, city, state = _key(record)
    if not name_key or not registry:
        return {"verdict": "unmatched", "match": None, "score": 0}

    # Only compare within the same state when the record has one; cities drift, states rarely do.
    pool = [r for r in registry if not state or str(r.get("state", "")).lower() == state] or registry
    choices = {i: str(r.get("name_key", "")).lower() for i, r in enumerate(pool)}
    best = process.extract(name_key, choices, scorer=fuzz.token_set_ratio, limit=5)
    if not best:
        return {"verdict": "unmatched", "match": None, "score": 0}

    # Re-rank the top 5 with a city bonus so "Acme Outdoors, Boston" beats "Acme Outdoors, Worcester".
    ranked = []
    for _choice, score, idx in best:
        r = pool[idx]
        bonus = 5 if city and str(r.get("city", "")).lower() == city else 0
        ranked.append((min(100, score + bonus), r))
    ranked.sort(key=lambda t: t[0], reverse=True)
    score, match = ranked[0]
    verdict = "matched" if score >= 92 else "probable" if score >= 80 else "unmatched"
    return {"verdict": verdict, "match": match if verdict != "unmatched" else None, "score": round(float(score), 1)}
