"""Graduation: prove a cheaper model, a code skill, or the semantic cache, then switch.

Pure decisions live here so tests can run them with no database and no model call.
tank/main.py shadow and /v1/graduation/* are the only callers.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

SKILL_PREFIX = "skill:"
CACHE_CANDIDATE = "cache:semantic"
TOOL_PREFIX = "tool:"
SEMANTIC_THRESHOLD = 0.97
DEFAULT_THRESHOLD = 0.99
DEFAULT_MIN_SAMPLES = 200
DEFAULT_MAX_REJECT = 0.05
DEFAULT_TOLERANCE = 0.01
WATCH_DAYS = 7
RESEARCH_DAYS = 30

_ID_KEY = re.compile(r"(^id$|_id$)", re.I)
_SKILL_REF = re.compile(r"^skill:([^@\s]+)@([^\s]+)$")


def _norm(value: Any) -> str:
    return " ".join(str(value).lower().split())


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, str) and value:
        try:
            return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _meets(value: float, bar: float) -> bool:
    return round(value, 4) >= round(bar, 4)


def is_skill(candidate: str | None) -> bool:
    return bool(candidate) and str(candidate).startswith(SKILL_PREFIX)


def is_semantic_cache(candidate: str | None) -> bool:
    return candidate == CACHE_CANDIDATE


def is_tool(candidate: str | None) -> bool:
    return bool(candidate) and str(candidate).startswith(TOOL_PREFIX)


def parse_skill(candidate: str | None) -> tuple[str, str] | None:
    if not candidate:
        return None
    match = _SKILL_REF.match(str(candidate).strip())
    if match is None:
        return None
    return match.group(1), match.group(2)


def code_candidate_problem(
    candidate: str | None,
    *,
    state: str | None,
    task_type: str | None,
    on_disk: bool,
) -> str | None:
    """A code candidate is an approved skill with no task type, so it cannot call a model."""
    if not is_skill(candidate):
        return None
    if parse_skill(candidate) is None:
        return "a code candidate looks like skill:id@version"
    if not on_disk:
        return "no such skill package"
    if task_type:
        return "a code candidate must not call a model"
    if state != "approved":
        return "a code candidate must be an approved skill"
    return None


def skill_payload(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """The skill sees the same input the model saw. JSON in the last user message wins."""
    content = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            content = str(message.get("content") or "")
            break
    parsed = as_obj(content)
    if isinstance(parsed, dict):
        return parsed
    return {"text": content}


def as_obj(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_set(value: Any) -> bool:
    return isinstance(value, (list, tuple, set))


def _item_key(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str).lower()
    return _norm(value)


def jaccard(left: Any, right: Any) -> float:
    a = {_item_key(item) for item in (left or []) if _item_key(item)}
    b = {_item_key(item) for item in (right or []) if _item_key(item)}
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _as_set(value: Any) -> list[Any]:
    if _is_set(value):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def score_value(key: str, left: Any, right: Any, *, tolerance: float) -> float:
    if _is_number(left) and _is_number(right) and not _ID_KEY.search(key):
        if left == right:
            return 1.0
        scale = max(abs(float(left)), abs(float(right)), 1.0)
        return 1.0 if abs(float(left) - float(right)) <= tolerance * scale else 0.0
    if _ID_KEY.search(key):
        return 1.0 if str(left).strip() == str(right).strip() else 0.0
    if _is_set(left) or _is_set(right):
        return jaccard(_as_set(left), _as_set(right))
    if isinstance(left, dict) and isinstance(right, dict):
        score, _fields = field_scores(left, right, tolerance=tolerance)
        return score
    if left is None and right is None:
        return 1.0
    if left is None or right is None:
        return 0.0
    return 1.0 if _norm(left) == _norm(right) else 0.0


def field_scores(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    keys: list[str] | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> tuple[float, dict[str, float]]:
    chosen = list(keys) if keys is not None else sorted(set(left) | set(right))
    chosen = [key for key in chosen if not str(key).startswith("_")]
    if not chosen:
        return 1.0, {}
    fields = {
        key: score_value(key, left.get(key), right.get(key), tolerance=tolerance)
        for key in chosen
    }
    return sum(fields.values()) / len(fields), fields


@dataclass
class Comparison:
    agreement: float | None
    method: str
    fields: dict[str, float]


def compare(primary: Any, candidate: Any, *, tolerance: float = DEFAULT_TOLERANCE) -> Comparison:
    """Field by field when both sides are JSON. Free text is left for the judge."""
    left, right = as_obj(primary), as_obj(candidate)
    if not isinstance(left, dict) or not isinstance(right, dict):
        return Comparison(agreement=None, method="text", fields={})
    score, fields = field_scores(left, right, tolerance=tolerance)
    return Comparison(agreement=score, method="fields", fields=fields)


def score_pair(expected: Any, output: Any, *, tolerance: float = DEFAULT_TOLERANCE) -> float:
    """One screen case. Expected keys only, so extra skill fields do not dilute the score.
    Free text falls back to word overlap so a screen does not need the judge."""
    if isinstance(expected, dict):
        got = output if isinstance(output, dict) else as_obj(output)
        if got is None:
            blobs = [str(value) for value in expected.values() if isinstance(value, str)]
            structured = any(isinstance(value, (dict, list)) for value in expected.values())
            if blobs and not structured:
                return jaccard(" ".join(blobs).split(), str(output or "").split())
            got = {}
        score, _fields = field_scores(expected, got, keys=list(expected.keys()), tolerance=tolerance)
        return score
    compared = compare(expected, output, tolerance=tolerance)
    if compared.agreement is None:
        return jaccard(str(expected).split(), str(output or "").split())
    return compared.agreement


def average_fields(rows: list[dict[str, Any]]) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for row in rows:
        raw = row.get("fields") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            try:
                buckets.setdefault(str(key), []).append(float(value))
            except (TypeError, ValueError):
                continue
    return {key: sum(values) / len(values) for key, values in buckets.items()}


def _floors(policy: dict[str, Any]) -> dict[str, float]:
    raw = policy.get("field_floors") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def fields_clear(scores: dict[str, float], policy: dict[str, Any]) -> tuple[bool, str | None]:
    bar = _float(policy.get("eval_threshold"), DEFAULT_THRESHOLD)
    floors = _floors(policy)
    for name, score in scores.items():
        floor = floors.get(name, bar)
        if not _meets(score, floor):
            return False, f"{name} is below its floor"
    return True, None


def proven_reason(policy: dict[str, Any], evidence: Evidence) -> tuple[bool, str | None]:
    bar = _float(policy.get("eval_threshold"), DEFAULT_THRESHOLD)
    minimum = _int(policy.get("min_shadow_samples"), DEFAULT_MIN_SAMPLES)
    if str(policy.get("candidate_model") or "").startswith("routellm:"):
        bar, minimum = max(bar, DEFAULT_THRESHOLD), max(minimum, DEFAULT_MIN_SAMPLES)
    ceiling = _float(policy.get("max_reject_rate"), DEFAULT_MAX_REJECT)
    if evidence.samples < minimum:
        return False, "too few shadow samples"
    if evidence.agreement is None or not _meets(evidence.agreement, bar):
        return False, "agreement is below the bar"
    ok, why = fields_clear(evidence.fields, policy)
    if not ok:
        return False, why
    if evidence.judged > 0 and round(evidence.rejected / evidence.judged, 4) > round(ceiling, 4):
        return False, "client rejections are above the bar"
    return True, None


@dataclass
class Evidence:
    samples: int = 0
    agreement: float | None = None
    fields: dict[str, float] = field(default_factory=dict)
    rejected: int = 0
    judged: int = 0
    live_agreement: float | None = None


@dataclass
class Move:
    action: str
    policy: dict[str, Any]
    reason: str | None = None


def _copied(policy: dict[str, Any]) -> dict[str, Any]:
    return dict(policy)



def matching_routellm_candidate(policy: dict[str, Any]) -> bool:
    """Only the current strong/weak/threshold/checkpoint pin may pass promotion."""
    from routellm_shadow import configuration
    config = configuration(policy)
    return config is not None and policy.get("candidate_model") == config.candidate


def _promoted(policy: dict[str, Any], now: datetime) -> dict[str, Any]:
    updated = _copied(policy)
    candidate = str(policy.get("candidate_model") or "")
    if router_cannot_serve(candidate) and not matching_routellm_candidate(policy):
        raise ValueError(ROUTER_BLOCK)
    updated["previous_model"] = policy.get("model")
    updated["model"] = candidate
    updated["candidate_model"] = None
    updated["shadow_rate"] = 0
    updated["promoted_at"] = _aware(now)
    if candidate.startswith(SKILL_PREFIX):
        updated["rung"] = 3
        updated["semantic_cache"] = False
    elif candidate.startswith("routellm:"):
        updated["rung"] = 1
        updated["semantic_cache"] = False
    elif candidate == CACHE_CANDIDATE or candidate.startswith("cache:"):
        updated["rung"] = 4
        updated["semantic_cache"] = True
    return updated


def _rolled_back(policy: dict[str, Any], reason: str) -> dict[str, Any]:
    updated = _copied(policy)
    previous = str(policy.get("previous_model") or "")
    updated["model"] = previous
    updated["previous_model"] = None
    updated["promoted_at"] = None
    updated["shadow_rate"] = 0
    updated["note"] = reason
    if previous.startswith(SKILL_PREFIX):
        updated["rung"] = 3
        updated["semantic_cache"] = False
    elif previous.startswith("cache:"):
        updated["rung"] = 4
        updated["semantic_cache"] = True
    else:
        updated["rung"] = 1
        updated["semantic_cache"] = False
    return updated


def router_cannot_serve(candidate: str | None) -> bool:
    """Only pinned skills have a foreground runner; cache policies still name a model."""
    if candidate is None:
        return False
    name = str(candidate).strip()
    if not name:
        return False
    return (is_skill(name) and parse_skill(name) is None) or name.startswith(("cache:", "tool:", "routellm:"))


ROUTER_BLOCK = "use a pinned skill:id@version or a model with cache enabled in policy"


def code_failure(output: dict[str, Any], floor: float) -> str | None:
    """Skills may report a fraction or the existing Base 0..100 confidence scale."""
    if not output or output.get("answerable") is False or output.get("not_answerable") or output.get("refused"):
        return "not_answerable"
    if output.get("status") in {"not-answerable", "not_answerable", "error", "failed"} or output.get("error"):
        return "not_answerable"
    scores = []
    if "confidence" in output:
        scores.append(output["confidence"])
    elif isinstance(output.get("fields"), dict):
        scores.extend(v["confidence"] for v in output["fields"].values() if isinstance(v, dict) and "confidence" in v)
    try:
        values = [float(v) / (100 if float(v) > 1 else 1) for v in scores]
    except (TypeError, ValueError):
        return "invalid_confidence"
    if values and (any(not 0 <= v <= 1 for v in values) or min(values) < floor):
        return "low_confidence"
    return None


def semantic_form(messages: list[dict[str, str]]) -> str:
    """Conservative local equivalence, preserving word order, names, numbers and negation.

    No embedding service/key. Only spelling and request-politeness variants are
    equated; wider paraphrases deliberately miss until a local encoder is proven.
    """
    synonyms = {"summarise": "summarize", "summarising": "summarizing", "colour": "color"}
    rows = []
    for message in messages:
        text = message.get("content", "")
        text = re.sub(r"^\s*(?:please\s+|could you\s+|can you\s+)", "", text, flags=re.I)
        words = re.findall(r"\w+|[^\w\s]", text.casefold())
        rows.append([message.get("role"), [synonyms.get(word, word) for word in words]])
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def semantic_vector(form: str) -> list[float]:
    # Stable across processes/Python versions; used only after equivalence and
    # tenant/context checks, so a hash collision cannot authorize a cache hit.
    vector = [0.0] * 256
    for word in re.findall(r"\w+", form):
        index = int.from_bytes(hashlib.sha256(word.encode()).digest()[:2], "big") % len(vector)
        vector[index] += 1
    return vector


def promote_if_proven(policy: dict[str, Any], evidence: Evidence, now: datetime, *, automatic: bool) -> Move:
    if not policy.get("candidate_model"):
        return Move("hold", _copied(policy), "no candidate")
    candidate = str(policy.get("candidate_model") or "")
    if router_cannot_serve(candidate) and not matching_routellm_candidate(policy):
        return Move("hold", _copied(policy), ROUTER_BLOCK)
    ok, reason = proven_reason(policy, evidence)
    if not ok:
        return Move("hold", _copied(policy), reason)
    if automatic:
        return Move("hold", _copied(policy), "proven, waiting for a person")
    return Move("promote", _promoted(policy, now), None)


def rollback_if_dropped(policy: dict[str, Any], evidence: Evidence, now: datetime) -> Move:
    promoted_at = _as_dt(policy.get("promoted_at"))
    if promoted_at is None or not policy.get("previous_model") or policy.get("candidate_model"):
        return Move("hold", _copied(policy), None)
    if _aware(now) - promoted_at > timedelta(days=WATCH_DAYS):
        return Move("hold", _copied(policy), None)
    ceiling = _float(policy.get("max_reject_rate"), DEFAULT_MAX_REJECT)
    bar = _float(policy.get("eval_threshold"), DEFAULT_THRESHOLD)
    if str(policy.get("model") or "").startswith("routellm:"):
        bar = max(bar, DEFAULT_THRESHOLD)
    reasons: list[str] = []
    if evidence.judged > 0 and round(evidence.rejected / evidence.judged, 4) > round(ceiling, 4):
        reasons.append("client rejections are above the bar")
    if evidence.live_agreement is not None and not _meets(evidence.live_agreement, bar):
        reasons.append("live agreement is below the bar")
    if not reasons:
        return Move("hold", _copied(policy), None)
    if router_cannot_serve(str(policy.get("previous_model") or "")):
        return Move("hold", _copied(policy), ROUTER_BLOCK)
    reason = "; ".join(reasons)
    return Move("rollback", _rolled_back(policy, reason), reason)


def apply(policy: dict[str, Any], evidence: Evidence, now: datetime) -> Move:
    """Hold proven candidates for founder approval; roll back a fresh promotion that slips."""
    if policy.get("candidate_model"):
        return promote_if_proven(policy, evidence, now, automatic=True)
    return rollback_if_dropped(policy, evidence, now)


def budget_open(spent: float, limit: float, *, code: bool) -> bool:
    """Paid shadow stops at the daily cap. Code costs nothing and is always run."""
    if code:
        return True
    return float(spent) < float(limit)


def research_reason(
    searched_at: datetime | None,
    now: datetime,
    roster_fingerprint: str,
    price_fingerprint: str,
    stored_roster: str | None,
    stored_price: str | None,
) -> str | None:
    if searched_at is None:
        return "never searched"
    if roster_fingerprint != stored_roster:
        return "roster changed"
    if price_fingerprint != stored_price:
        return "price changed"
    if _aware(now) - _aware(searched_at) >= timedelta(days=RESEARCH_DAYS):
        return "monthly"
    return None


@dataclass(frozen=True)
class Quote:
    name: str
    price: float
    funds: bool = True
    kind: str = "model"  # model | code | tool


@dataclass
class Ranked:
    name: str
    kind: str
    price: float
    screen_agreement: float | None
    shadow_n: int
    shadow_agreement: float | None
    status: str
    cost_per_call: float
    monthly_saving_usd: float | None
    fields_ok: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "price": self.price,
            "screen_agreement": self.screen_agreement,
            "shadow_n": self.shadow_n,
            "shadow_agreement": self.shadow_agreement,
            "status": self.status,
            "cost_per_call": self.cost_per_call,
            "monthly_saving_usd": self.monthly_saving_usd,
            "fields_ok": self.fields_ok,
        }


@dataclass
class SearchResult:
    ranked: list[Ranked]
    proposed: str | None
    next_shadow: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ranked": [row.as_dict() for row in self.ranked],
            "proposed": self.proposed,
            "next_shadow": self.next_shadow,
        }


def quotes_from_roster(models: list[dict[str, Any]]) -> list[Quote]:
    quotes: list[Quote] = []
    seen = set()
    for model in models:
        name = str(model["model_name"])
        if name in seen:
            continue
        seen.add(name)
        info = model.get("model_info") or {}
        provider_model = str((model.get("litellm_params") or {}).get("model") or "")
        # Perplexity presets use the dedicated research route, never generic chat/shadow.
        if provider_model.startswith("perplexity/preset/"):
            continue
        price = float(info.get("input_cost_per_token") or 0) + float(info.get("output_cost_per_token") or 0)
        quotes.append(Quote(name=str(model["model_name"]), price=price, funds=True, kind="model"))
    return quotes


def load_price_roster(path: Path) -> list[Quote]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return quotes_from_roster(list(raw.get("model_list") or []))


def fingerprints(quotes: list[Quote]) -> tuple[str, str]:
    names = "|".join(sorted(quote.name for quote in quotes))
    prices = "|".join(f"{quote.name}:{quote.price:.8f}" for quote in sorted(quotes, key=lambda quote: quote.name))
    return (
        hashlib.sha256(names.encode()).hexdigest()[:16],
        hashlib.sha256(prices.encode()).hexdigest()[:16],
    )


def search(
    *,
    serving: str,
    roster: list[Quote],
    screens: dict[str, float],
    shadows: dict[str, tuple[int, float | None]] | None = None,
    field_scores_by_name: dict[str, dict[str, float]] | None = None,
    calls_30d: int = 0,
    serving_cost: float | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    floors: dict[str, float] | None = None,
) -> SearchResult:
    """Cheapest first. A cheaper option that misses the bar is not proposed."""
    by_name = {quote.name: quote for quote in roster}
    serving_quote = by_name.get(serving)
    serving_price = serving_quote.price if serving_quote is not None else _float(serving_cost, 0.0)
    paid_cost = _float(serving_cost, serving_price)
    live = shadows or {}
    fields_by = field_scores_by_name or {}
    floor_map = floors or {}
    ranked: list[Ranked] = []
    for quote in roster:
        if quote.name == serving:
            continue
        if quote.kind != "code" and not quote.funds:
            continue
        if quote.kind != "code" and quote.price >= serving_price:
            continue
        screen = screens.get(quote.name)
        shadow = live.get(quote.name)
        shadow_n = int(shadow[0]) if shadow is not None else 0
        shadow_agreement = shadow[1] if shadow is not None else None
        scores = fields_by.get(quote.name) or {}
        fields_ok = all(_meets(score, floor_map.get(name, threshold)) for name, score in scores.items())
        if screen is None:
            status = "unscreened"
        elif not _meets(screen, threshold) or not fields_ok:
            status = "dropped"
        elif shadow is not None and shadow_n >= min_samples and (
            shadow_agreement is None or not _meets(shadow_agreement, threshold) or not fields_ok
        ):
            status = "dropped"
        elif shadow is not None and shadow_n >= min_samples:
            status = "cleared"
        else:
            status = "shadow"
        saving = (paid_cost - quote.price) * calls_30d
        ranked.append(Ranked(
            name=quote.name,
            kind=quote.kind,
            price=quote.price,
            screen_agreement=screen,
            shadow_n=shadow_n,
            shadow_agreement=shadow_agreement,
            status=status,
            cost_per_call=quote.price,
            monthly_saving_usd=saving,
            fields_ok=fields_ok,
        ))
    ranked.sort(key=lambda row: (row.price, row.name))
    proposed: str | None = None
    next_shadow: str | None = None
    for row in ranked:
        if row.status == "dropped":
            continue
        if row.status == "unscreened":
            break
        if row.status == "cleared":
            if proposed is None:
                proposed = row.name
            continue
        if next_shadow is None:
            next_shadow = row.name
        if proposed is None:
            proposed = row.name
        break
    return SearchResult(ranked=ranked, proposed=proposed, next_shadow=next_shadow)


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def semantic_lookup(
    vector: list[float],
    rows: list[dict[str, Any]],
    *,
    tenant_id: str,
    scope: str,
    threshold: float = SEMANTIC_THRESHOLD,
) -> dict[str, Any] | None:
    """Near-identical question, same scope rules as the exact cache. No paid call."""
    if scope not in {"tenant", "platform"}:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for row in rows:
        if scope == "tenant" and str(row.get("tenant_id")) != str(tenant_id):
            continue
        similarity = cosine(vector, list(row.get("vector") or []))
        if similarity >= threshold and (best is None or similarity > best[0]):
            best = (similarity, row)
    if best is None:
        return None
    return {"content": best[1].get("content"), "similarity": best[0], "tenant_id": best[1].get("tenant_id")}


def _step(
    label: str,
    name: str | None,
    *,
    samples: int,
    agreement: float | None,
    cost_per_call: float | None,
    monthly_saving_usd: float | None,
    current: bool,
) -> dict[str, Any]:
    return {
        "label": label,
        "name": name,
        "samples": samples,
        "agreement": agreement,
        "cost_per_call": cost_per_call,
        "monthly_saving_usd": monthly_saving_usd,
        "current": current,
    }


def _saving(before: float | None, after: float | None, calls: int) -> float | None:
    if before is None or after is None:
        return None
    return (before - after) * calls


def ladder_from_policy(
    *,
    model: str,
    previous_model: str | None,
    candidate_model: str | None,
    calls_30d: int,
    prim_cost: float | None,
    cand_cost: float | None,
    shadow_n: int,
    shadow_agreement: float | None,
    ranked: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Paid model, then a cheaper model, then code. The serving step is marked current."""
    rows = list(ranked or [])
    serving = str(model)
    paid_name = previous_model if (is_skill(serving) or is_semantic_cache(serving) or serving.startswith("cache:")) else serving
    cheaper = next((row for row in rows if row.get("kind") == "model" and row.get("status") != "dropped"), None)
    if cheaper is None and candidate_model and not is_skill(candidate_model) and not is_semantic_cache(candidate_model):
        cheaper = {
            "name": candidate_model,
            "shadow_n": shadow_n,
            "shadow_agreement": shadow_agreement,
            "cost_per_call": cand_cost,
            "monthly_saving_usd": _saving(prim_cost, cand_cost, calls_30d),
        }
    code = next((row for row in rows if row.get("kind") == "code" and row.get("status") != "dropped"), None)
    if is_skill(serving):
        code = {
            "name": serving,
            "shadow_n": calls_30d,
            "shadow_agreement": shadow_agreement,
            "cost_per_call": 0.0,
            "monthly_saving_usd": _saving(prim_cost, 0.0, calls_30d),
        }
    elif is_skill(candidate_model):
        code = {
            "name": candidate_model,
            "shadow_n": shadow_n,
            "shadow_agreement": shadow_agreement,
            "cost_per_call": 0.0,
            "monthly_saving_usd": _saving(prim_cost, 0.0, calls_30d),
        }
    cheaper_name = str(cheaper["name"]) if cheaper and cheaper.get("name") else None
    code_name = str(code["name"]) if code and code.get("name") else None
    cheaper_cost = _maybe_float(cheaper.get("cost_per_call") if cheaper else None)
    if cheaper_cost is None and cheaper_name and cheaper_name == candidate_model:
        cheaper_cost = cand_cost
    code_cost = 0.0 if code_name else None
    return [
        _step(
            "Paid model",
            paid_name,
            samples=calls_30d,
            agreement=None,
            cost_per_call=prim_cost,
            monthly_saving_usd=None,
            current=paid_name == serving,
        ),
        _step(
            "Cheaper model",
            cheaper_name,
            samples=_int(cheaper.get("shadow_n") if cheaper else 0, 0),
            agreement=_maybe_float(cheaper.get("shadow_agreement") if cheaper else None),
            cost_per_call=cheaper_cost,
            monthly_saving_usd=_saving(prim_cost, cheaper_cost, calls_30d) if cheaper_name else None,
            current=bool(cheaper_name) and cheaper_name == serving,
        ),
        _step(
            "Code",
            code_name,
            samples=_int(code.get("shadow_n") if code else 0, 0),
            agreement=_maybe_float(code.get("shadow_agreement") if code else None),
            cost_per_call=code_cost,
            monthly_saving_usd=_saving(prim_cost, code_cost, calls_30d) if code_name else None,
            current=bool(code_name) and code_name == serving,
        ),
    ]


def _maybe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


@dataclass
class CallResult:
    content: str
    model_calls: int
    cost_usd: float
    rung: int


async def answer(model: str, payload: dict[str, Any], *, llm: Any, run_code: Any) -> CallResult:
    """A promoted skill or the semantic cache answers with no model call."""
    if is_skill(model) or is_semantic_cache(model) or str(model).startswith("cache:"):
        content = await run_code(model, payload)
        if not isinstance(content, str):
            content = json.dumps(content)
        rung = 4 if str(model).startswith("cache:") else 3
        return CallResult(content=content, model_calls=0, cost_usd=0.0, rung=rung)
    content, cost = await llm(model, payload)
    return CallResult(content=str(content), model_calls=1, cost_usd=float(cost), rung=1)


HOLD_REGIONS = ("us-east", "za")


def heldout_score_for(scores: list[dict], *, region: str, skill: str, candidate: str | None) -> float | None:
    if not candidate:
        return None
    parsed = parse_skill(candidate)
    found = None
    for row in scores:
        if str(row.get("region") or "") != region or row.get("final_check") is False:
            continue
        if parsed:
            if row.get("skill_id") == parsed[0] and str(row.get("version")) == parsed[1]:
                found = row.get("score")
        elif row.get("candidate_model") == candidate and (
            (row.get("task_type") or row.get("skill_id")) == skill or row.get("skill_id") == skill
        ):
            found = row.get("score")
    if found is None:
        return None
    return float(found)


def skills_served(candidate: str, ranks: list[dict], scores: list[dict]) -> set[str]:
    skills: set[str] = set()
    for rank in ranks:
        if rank.get("candidate") == candidate and rank.get("task_type"):
            skills.add(str(rank["task_type"]))
    parsed = parse_skill(candidate)
    if parsed:
        skills.add(parsed[0])
    for row in scores:
        if row.get("candidate_model") == candidate:
            skill = row.get("task_type") or row.get("skill_id")
            if skill:
                skills.add(str(skill))
    return skills


def promotion_block(*, candidate: str | None, current_model: str | None, skills: set[str] | list[str],
                    scores: list[dict], removed_models: set[str]) -> str | None:
    """A candidate is promotable only when it is no worse on both regions, for every skill."""
    if not candidate:
        return "missing held-out score"
    if candidate in removed_models:
        return "trained on removed data"
    served = set(skills)
    if not served:
        return "missing held-out score"
    for skill in sorted(served):
        for region in HOLD_REGIONS:
            cand = heldout_score_for(scores, region=region, skill=skill, candidate=candidate)
            current = heldout_score_for(scores, region=region, skill=skill, candidate=current_model)
            if cand is None:
                return "missing held-out score"
            if current is not None and cand < current:
                return "dropped"
    return None


def training_candidate(policy: dict, candidates: list[dict], gold_count: int) -> dict | None:
    """Display a held-out winner only; screening/shadow scores are not holdout scores."""
    if gold_count < 100:
        return None
    eligible = [c for c in candidates if c.get('gold_count', gold_count) >= 100 and c.get('heldout_n', 0) > 0
                and c.get('heldout_score') is not None
                and float(c['heldout_score']) >= float(policy['eval_threshold'])
                and c.get('kind') in ('code', 'model', 'owned')
                and (c['kind'] != 'model' or c.get('cheaper') is True)]
    return min(eligible, key=lambda c: ({'code': 0, 'model': 1, 'owned': 2}[c['kind']],
                                       c.get('cost_per_call') if c.get('cost_per_call') is not None else float('inf'),
                                       c['candidate']), default=None)


async def training_table(conn, tenant, region: str) -> list[dict]:
    policies = await conn.fetch("""SELECT task_type,model,candidate_model,eval_threshold,auto_promote
        FROM tank.policy WHERE tenant_id IS NULL AND task_type<>'tank.judge' ORDER BY task_type""")
    ranks = await conn.fetch('SELECT * FROM tank.graduation_rank')
    skills = await conn.fetch("SELECT id,version,task_type,state,present FROM tank.skills")
    gold = await conn.fetch("""SELECT skill_id,count(DISTINCT input_hash) AS n FROM tank.gold_examples
        WHERE tenant_id=$1 AND region=$2 AND status='checked' AND (NOT auto_label OR auto_approved)
        GROUP BY skill_id""", tenant, region)
    scores = await conn.fetch("""SELECT DISTINCT ON (region,skill_id,version,task_type,candidate_model) * FROM tank.gold_scores
        WHERE tenant_id=$1 AND region IN ('us-east','za') AND final_check
        ORDER BY region,skill_id,version,task_type,candidate_model,ran_at DESC,id DESC""", tenant)
    removed_rows = await conn.fetch("""SELECT DISTINCT b.model_repo FROM tank.training_job_batches b
        JOIN tank.removed_batches r ON r.tenant_id=b.tenant_id AND r.batch_id=b.batch_id
        WHERE b.tenant_id=$1""", tenant)
    removed_models = {row["model_repo"] for row in removed_rows}
    costs = await conn.fetch("""SELECT task_type,model,percentile_cont(0.5) WITHIN GROUP (ORDER BY cost_usd) AS cost FROM tank.ledger
        WHERE tenant_id=$1 AND region=$2 AND ts>now()-interval '30 days' AND NOT cache_hit
        AND agent NOT IN ('bench','shadow') AND outcome='delivered'
        GROUP BY task_type,model""", tenant, region)
    counts = {r['skill_id']: r['n'] for r in gold}
    prices = {(r['task_type'], r['model']): float(r['cost']) for r in costs}
    rank_rows = [dict(rank) for rank in ranks]
    score_rows = [dict(score) for score in scores]
    result = []
    for policy in policies:
        task = policy['task_type']
        current_cost = prices.get((task, policy['model']))
        mapped = {task} | {s['id'] for s in skills if s['task_type'] == task}
        task_ranks = [dict(r) for r in rank_rows if r['task_type'] == task]
        region_scores = [row for row in score_rows if row.get('region') == region]
        for rank in task_ranks:
            parsed = parse_skill(rank['candidate'])
            if parsed:
                mapped.add(parsed[0])
                approved = any(s['id'] == parsed[0] and s['version'] == parsed[1]
                               and s['state'] == 'approved' and s['present'] and not s['task_type'] for s in skills)
                evidence = next((s for s in region_scores if s['skill_id'] == parsed[0] and s['version'] == parsed[1]), None)
                rank.update(gold_count=counts.get(parsed[0], 0), heldout_score=float(evidence['score']) if approved and evidence else None,
                            heldout_n=evidence['n'] if approved and evidence else 0, cost_per_call=0.0)
            else:
                # Model evaluations explicitly name the task and model in the score store.
                evidence = next((s for s in region_scores if s.get('task_type') == task
                                 and s.get('candidate_model') == rank['candidate']), None)
                rank.update(heldout_score=float(evidence['score']) if evidence else None,
                            heldout_n=evidence['n'] if evidence else 0,
                            cost_per_call=float(evidence['cost_per_call']) if evidence and evidence.get('cost_per_call') is not None else None)
                rank['cheaper'] = (rank['cost_per_call'] is not None and current_cost is not None
                                   and rank['cost_per_call'] < current_cost)
        count = max((counts.get(skill, 0) for skill in mapped), default=0)
        best = training_candidate(dict(policy), task_ranks, count)
        shown = best['candidate'] if best else policy['candidate_model']
        trained = bool(shown and shown in removed_models)
        can_promote = bool(best and best['candidate'] == policy['candidate_model']
                           and not router_cannot_serve(best['candidate']))
        blocked = 'trained on removed data' if trained else None
        if can_promote:
            served = skills_served(best['candidate'], rank_rows, score_rows)
            served.add(task)
            blocked = promotion_block(candidate=best['candidate'], current_model=policy['model'],
                                      skills=served, scores=score_rows, removed_models=removed_models)
            can_promote = blocked is None
        by_region = {}
        if best:
            for name in HOLD_REGIONS:
                by_region[name] = heldout_score_for(score_rows, region=name, skill=task, candidate=best['candidate'])
        result.append({'task_type': task, 'model': policy['model'], 'gold_count': count,
                       'candidate': best['candidate'] if best else None,
                       'heldout_score': best['heldout_score'] if best else None,
                       'heldout_by_region': by_region,
                       'current_cost': current_cost, 'candidate_cost': best['cost_per_call'] if best else None,
                       'can_promote': can_promote, 'trained_on_removed': trained,
                       'blocked_reason': blocked})
    return result


def moa_seat_proposal(rows: list[dict], *, current: str, candidate: str,
                      minimum: int = 100, threshold: float = .97) -> dict | None:
    """Propose only; caller must persist scoped rows and require founder promotion.

    Failed/unknown-price samples count against eligibility, never disappear from
    the denominator. Rows must be from one tenant/task/config/seat experiment.
    """
    minimum = max(100, minimum)
    if len(rows) < minimum or not current or not candidate or current == candidate:
        return None
    eligible = [r for r in rows if r.get('current_model') == current and r.get('candidate_model') == candidate]
    if len(eligible) != len(rows):
        return None
    import math
    for row in rows:
        for name in ('current_cost', 'candidate_cost', 'against_current', 'against_final'):
            value = row.get(name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                return None
        if row.get('status') != 'complete':
            return None
    matched = sum(min(r['against_current'], r['against_final']) >= threshold for r in rows)
    if matched / len(rows) < threshold:
        return None
    before = sum(r['current_cost'] for r in rows) / len(rows)
    after = sum(r['candidate_cost'] for r in rows) / len(rows)
    if after >= before:
        return None
    return {'current_model': current, 'candidate_model': candidate, 'samples': len(rows),
            'agreement': matched / len(rows), 'current_cost': before, 'candidate_cost': after,
            'status': 'proposed', 'requires_founder': True}
