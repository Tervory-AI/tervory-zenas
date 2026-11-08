"""Base passes 1–7. Same steps the worker used inside Tank.

Skills stay in Skala. This module only orders them and merges what they return.
A skill call is a function the room supplies (the chassis, through Tank).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from base_gold.observations import observation
from base_source_scores import prefer, select_observations

BASE_CORE = [
    "base.normalize",
    "base.resolve",
    "base.find_site",
    "base.scrape_site",
    "base.extract",
    "base.contacts",
    "base.socials",
    "base.business_data",
    "base.verify",
]
OPTIONAL_SKILLS = ["base.site_signals"]

# A vertical engine is two skills: <engine>.ingest and <engine>.profile.
ENGINE_SKILLS = ("ingest", "profile")

SkillCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

_ACCOUNT_ERRORS = ("credit balance", "authentication_error", "permission_error", "invalid x-api-key", "billing")


class ModelAccountBlocked(Exception):
    """The model provider refused the account. The record stays queued."""

    def __init__(self, reason: str) -> None:
        self.reason = "Model account unavailable; completed work was kept."
        super().__init__(self.reason)


@dataclass
class Outcome:
    status: str
    profile: dict[str, Any] = field(default_factory=dict)
    passes: dict[str, Any] = field(default_factory=dict)
    confidence: int = 0
    error: str | None = None
    cost_usd: float = 0.0


def skill_ids(engine: str | None) -> list[str]:
    ids = list(BASE_CORE)
    if engine:
        ids += [f"{engine}.{name}" for name in ENGINE_SKILLS]
    return ids


def is_account_error(exc: Exception) -> bool:
    if getattr(exc, "account_blocked", False):
        return True
    msg = str(getattr(exc, "detail", "") or exc).lower()
    return any(token in msg for token in _ACCOUNT_ERRORS)


def merge(profile: dict[str, Any], fields: dict[str, Any],
          table: dict[tuple[str, str], float] | None = None,
          evidence: dict[str, Any] | None = None) -> None:
    """Measured source quality wins; without measurements retain confidence fallback."""
    for key, value in fields.items():
        if not isinstance(value, dict):
            continue
        old = profile.get(key)
        if old and table and old.get("value") != value.get("value"):
            website = val(profile, "website")
            candidates = [{**item, "website": website} for item in (old, value)]
            from base_source_scores import classify
            if any((classify(item.get("source_url"), website,
                             item.get("source_kind") or item.get("source")), key) in table for item in candidates):
                winner = prefer(candidates, key, table)
                profile[key] = {k: v for k, v in winner.items() if k != "website"}
                if evidence is not None:
                    evidence[key] = {"selected": winner, "candidates": candidates}
                continue
        if old is None or int(value.get("confidence") or 0) >= int(old.get("confidence") or 0):
            profile[key] = value


def val(profile: dict[str, Any], key: str, default: Any = None) -> Any:
    field_value = profile.get(key)
    return (field_value.get("value") if isinstance(field_value, dict) else field_value) or default


def ref_of(skill_id: str, out: dict[str, Any]) -> str:
    ref = out.get("_skill")
    return ref if isinstance(ref, str) and ref else skill_id


def cost_of(out: dict[str, Any]) -> float:
    try:
        return float(out.get("_cost_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def answer_from_code(ref: str, out: dict[str, Any]) -> bool:
    """Code wrote the answer when the skill is 2.x, or when no model cost was attached.

    Tank stamps `_cost_usd` on every skill, including a code skill whose cost is 0.
    A 2.x ref is the code version. A 1.x answer with no `_cost_usd` is the same.
    """
    version = ref.rsplit("@", 1)[-1] if "@" in ref else ""
    if version.startswith("2."):
        return True
    return "_cost_usd" not in out or out.get("_cost_usd") is None


def page_html(scraped: dict[str, Any]) -> str:
    html = scraped.get("html")
    return html if isinstance(html, str) else ""


def engine_id(engine: str | None) -> str:
    return engine if isinstance(engine, str) else ""


_ENGINE_ID = re.compile(r"[a-z][a-z0-9_]{0,31}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def _skills_root() -> Path:
    """Engine data files. SKILLS_DIR in the room image; the repo's tank/skills in tests."""
    configured = os.environ.get("SKILLS_DIR")
    if configured:
        return Path(configured)
    source = Path(__file__).resolve()
    if source.parent.name == "base" and source.parent.parent.name == "agents":
        candidate = source.parents[2] / "tank" / "skills"
        if candidate.is_dir():
            return candidate
    return Path("/nonexistent-skills")


_SKILLS = _skills_root()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item).lower().split())
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def engine_terms(engine: str | None, version: str | None = None) -> dict[str, list[str]]:
    """Words and hosts an engine asks the core skills to apply.

    Read only from the data folder of `version`, the engine profile version Skala has approved
    (the worker asks Tank, without preview). No version, or no words in it: empty. Never another version.
    """
    empty = {"generic_words": [], "blocked_hosts": [], "generic_locals": []}
    if not isinstance(engine, str) or _ENGINE_ID.fullmatch(engine) is None:
        return empty
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        return empty
    root = _SKILLS / f"{engine}.profile" / version / "data"
    if not root.is_dir():
        return empty
    pack: dict[str, Any] = {}
    for path in sorted(root.glob("*.json")):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            pack.update(loaded)
    return {
        "generic_words": _string_list(pack.get("generic_words")),
        "blocked_hosts": _string_list(pack.get("blocked_hosts")),
        "generic_locals": _string_list(pack.get("generic_locals")),
    }


def result_line(done: int, ready: int, failed: int, cost: float) -> str:
    return f"{done} enriched ({ready} ready), {failed} failed, model spend ${float(cost):.4f}"


def order_states(groups: dict[str, list[dict[str, Any]]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Smallest state first, then the state code. That is the order the batches run."""
    return sorted(groups.items(), key=lambda item: (len(item[1]), item[0]))


async def state_of(raw: dict[str, Any], engine: str | None, call: SkillCall) -> str:
    record = raw
    if engine:
        ingested = await call(f"{engine}.ingest", raw)
        record = ingested.get("record") or raw
    state = str(record.get("state") or record.get("premise_state") or "").strip().upper()
    return state[:2] if state else "??"


async def prepare_batch(
    raws: list[dict[str, Any]],
    rec_ids: list[str],
    engine: str | None,
    registry: list[dict[str, Any]] | None,
    call: SkillCall,
) -> tuple[list[dict[str, Any]], list[dict[str, Any] | None], list[dict[str, Any]], float]:
    """Pass 0 and the registry for pass 1b. Returns inputs, engine rows, registry, cost."""
    cost = 0.0
    inputs: list[dict[str, Any]] = []
    engine_rows: list[dict[str, Any] | None] = []
    if engine:
        for index, raw in enumerate(raws):
            ingested = await call(f"{engine}.ingest", {**raw, "_base_record_id": str(rec_ids[index])})
            cost += cost_of(ingested)
            inputs.append(ingested.get("record") or dict(raw))
            engine_rows.append(ingested.get("engine") or None)
    else:
        inputs = [dict(raw) for raw in raws]
        engine_rows = [None] * len(inputs)

    if registry is None:
        built: list[dict[str, Any]] = []
        for index, record in enumerate(inputs):
            normalised = await call("base.normalize", {**record, "_base_record_id": str(rec_ids[index])})
            cost += cost_of(normalised)
            fields = normalised.get("fields") or {}
            built.append({
                "id": str(rec_ids[index]),
                "name_key": fields.get("name_key", {}).get("value", ""),
                "city": str(fields.get("city", {}).get("value", "")).lower(),
                "state": str(fields.get("state", {}).get("value", "")).lower(),
            })
        registry = built
    return inputs, engine_rows, registry, cost


async def enrich_one(
    raw_input: dict[str, Any],
    engine_in: dict[str, Any] | None,
    registry: list[dict[str, Any]],
    engine: str | None,
    call: SkillCall,
    refs: dict[str, str] | None = None,
    engine_version: str | None = None,
) -> Outcome:
    """One record through passes 1a–7. A blocked model account leaves the record queued.

    engine_version is the approved engine profile version; its data folder supplies the word lists.
    """
    profile: dict[str, Any] = {}
    passes: dict[str, Any] = {}
    cost = 0.0

    def named(skill_id: str, out: dict[str, Any] | None = None) -> str:
        if out is not None:
            found = ref_of(skill_id, out)
            if found != skill_id or not refs:
                return found
        if refs and refs.get(skill_id):
            return refs[skill_id]
        return skill_id

    original_call = call
    record_id = raw_input.get("_base_record_id")

    async def attributed_call(skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await original_call(skill_id, {**payload, **({"_base_record_id": record_id} if record_id else {})})

    call = attributed_call

    try:
        norm = await call("base.normalize", raw_input)
        cost += cost_of(norm)
        reliability = {(row["source"], row["field"]): float(row["wilson_lower"])
                       for row in norm.get("_source_reliability", []) if row.get("wilson_lower") is not None}
        decisions: dict[str, Any] = {}
        normalized = norm.get("fields") or {}
        reference = raw_input.get("_base_reference") or {}
        if reference and reliability:
            normalized = {key: {**value, "source_kind": "reference_file",
                                "source_url": reference.get("source_url"), "checked_at": reference.get("checked_at")}
                          for key, value in normalized.items()}
        merge(profile, normalized)
        passes["1a"] = {"skill": named("base.normalize", norm), "ms": norm.get("_ms")}

        res = await call("base.resolve", {"record": norm, "registry": registry})
        cost += cost_of(res)
        if res.get("match"):
            profile["registry_match"] = {
                "value": res["match"].get("id"),
                "source": f"registry:{res['verdict']}",
                "confidence": int(res.get("score") or 0),
            }
        passes["1b"] = {
            "skill": named("base.resolve", res),
            "ms": res.get("_ms"),
            "verdict": res.get("verdict"),
            "score": res.get("score"),
        }
        if engine and engine_in and engine_in.get("licence_number"):
            profile["registry_match"] = {
                "value": engine_in["licence_number"],
                "source": f"registry:{engine}",
                "confidence": 100,
            }
            if res.get("match"):
                passes["1b"]["duplicate_of"] = res["match"].get("id")

        name = val(profile, "name") or raw_input.get("name") or ""
        city = val(profile, "city") or ""
        state = val(profile, "state") or ""
        terms = engine_terms(engine, engine_version)

        site_in: dict[str, Any] = {
            "name": name,
            "city": city,
            "state": state,
            "phone": str(val(profile, "phone") or raw_input.get("phone") or ""),
            "zip": str(val(profile, "zip") or raw_input.get("zip") or ""),
            "address": str(val(profile, "street") or raw_input.get("address") or ""),
            "website": raw_input.get("website") or raw_input.get("url") or "",
        }
        if terms["generic_words"]:
            site_in["generic_words"] = terms["generic_words"]
        if terms["blocked_hosts"]:
            site_in["blocked_hosts"] = terms["blocked_hosts"]
        site = await call("base.find_site", site_in)
        cost += cost_of(site)
        if site.get("website"):
            merge(profile, {"website": {**site["website"],
                                       **({"source_url": site["website"].get("source_url") or site["website"].get("value")} if reliability else {})}},
                  reliability, decisions)
        passes["2a"] = {
            "skill": named("base.find_site", site),
            "ms": site.get("_ms"),
            "source": (site.get("website") or {}).get("source"),
            **({"evidence": site["evidence"]} if site.get("evidence") else {}),
            **({"blocked_by_check": site["blocked_by_check"]} if site.get("blocked_by_check") else {}),
        }

        url = val(profile, "website")
        scr: dict[str, Any] = {}
        if url:
            scr = await call("base.scrape_site", {"website": url})
            cost += cost_of(scr)
            if refs and refs.get("base.site_signals") and not scr.get("archived"):
                signals = await call("base.site_signals", {**scr, "website": url})
                cost += cost_of(signals)
                merge(profile, signals.get("fields") or {}, reliability, decisions)
                passes["2b_signals"] = {"skill": named("base.site_signals", signals),
                                       "ms": signals.get("_ms")}
            src = "site"
            if scr.get("archived"):
                profile["archived_site"] = {"value": {"text": scr.get("text", ""), "date": scr.get("archive_date"),
                    "archive": scr.get("archive_source")}, "source": "archived, dated", "confidence": 40}
                # Historical facts stay dated; never label them current or use them as gold.
                scr["text"] = ""
            if scr.get("emails"):
                profile["emails"] = {"value": scr["emails"], "source": src, "confidence": 80}
            if scr.get("phones"):
                page = next((item for item in scr.get("_base_observations") or [] if scr["phones"][0] in item.get("phones", [])), {})
                merge(profile, {"phone": {"value": scr["phones"][0], "source": src, "confidence": 80,
                                         **({"source_url": page.get("source_url") or url,
                                             "checked_at": page.get("checked_at")} if reliability else {})}},
                      reliability, decisions)
                profile["phones"] = {"value": scr["phones"], "source": src, "confidence": 80}
            if scr.get("socials"):
                profile["socials"] = {"value": scr["socials"], "source": src, "confidence": 85}
            # Legacy primary values still feed existing checks. The complete
            # collections retain independently observed contacts for the client.
            for target, key in (("websites", "websites"), ("social_links", "socials")):
                found = [item for page in scr.get("_base_observations") or [] for item in page.get(key, [])]
                if found:
                    profile[target] = {"value": list(dict.fromkeys(([url] if target == "websites" else []) + found)),
                                       "source": src, "confidence": 85}
            passes["2b"] = {
                "skill": named("base.scrape_site", scr),
                "ms": scr.get("_ms"),
                "pages": len(scr.get("pages") or []),
                "error": scr.get("error"),
                "via": scr.get("via"),
                "field_observations": scr.get("_base_observations") or [],
                **{key: scr[key] for key in ("archive_date", "archive_source", "access_error") if scr.get(key)},
            }
            observed = None if scr.get("archived") else observation(url, scr)
            if observed:
                passes["2b"]["gold_observation"] = observed
            if (scr.get("text") or "") and len(scr["text"]) >= 40:
                try:
                    ex = await call("base.extract", {
                        "name": name,
                        "text": scr["text"],
                        "html": page_html(scr),
                        "pages": scr.get("page_responses") or [],
                        "engine": engine_id(engine),
                    })
                    cost += cost_of(ex)
                except Exception as exc:  # noqa: BLE001 — a model-gateway blip must not fail the record
                    if is_account_error(exc):
                        raise ModelAccountBlocked(str(getattr(exc, "detail", "") or exc)) from exc
                    passes["2c"] = {
                        "skill": named("base.extract"),
                        "skipped": "model call failed: completed work was kept",
                    }
                else:
                    conf = int(ex.get("confidence") or 0)
                    source = "site:code" if answer_from_code(named("base.extract", ex), ex) else "site:model"
                    for key in ("categories", "services", "description", "people"):
                        if ex.get(key):
                            profile[key] = {"value": ex[key], "source": source, "confidence": conf}
                    products = [product for product in (ex.get("products") or []) if isinstance(product, dict)
                                and all(isinstance(product.get(key), str) and product[key].strip()
                                        for key in ("source_url", "checked_at"))]
                    if products:
                        profile["products"] = {"value": products, "source": "site:code", "confidence": conf}
                    passes["2c"] = {
                        "skill": named("base.extract", ex),
                        "ms": ex.get("_ms"),
                        "cost_usd": round(float(ex.get("_cost_usd") or 0), 6),
                    }

        if not scr.get("archived"):
            candidates = list(scr.get("_base_observations") or [])
            if reference and (normalized.get("phone") or {}).get("value"):
                candidates.append({"source_url": reference.get("source_url"), "checked_at": reference.get("checked_at"),
                                   "source_kind": "reference_file", "phones": [normalized["phone"]["value"]]})
            decisions.update(select_observations(profile, candidates, reliability))
        if decisions:
            passes["source_reliability"] = decisions

        con_in: dict[str, Any] = {
            "website": url or "",
            "emails": val(profile, "emails", []),
            "people": val(profile, "people", []),
        }
        # Preserve each contact's own observation. Raw licence phones are also
        # checked, even when a site phone won the normal profile merge.
        phone_inputs = []
        observations = scr.get("_base_observations") or []
        for number in list(val(profile, "phones", [])) + [val(profile, "phone")]:
            if not number:
                continue
            page = next((page for page in observations if number in page.get("phones", [])), {})
            source = profile.get("phone") if number == val(profile, "phone") else profile.get("phones")
            phone_inputs.append({**(source or {}), "value": number,
                                 **({"source": "site", "confidence": 80, "source_url": page.get("source_url"),
                                     "checked_at": page.get("checked_at")} if page else {})})
        for number in (raw_input.get("phone"), raw_input.get("voice_phone"), (engine_in or {}).get("phone")):
            if number:
                phone_inputs.append({"value": number, "source": "input", "confidence": 90,
                                     "source_url": reference.get("source_url"), "checked_at": reference.get("checked_at")})
        email_inputs = []
        for email in val(profile, "emails", []):
            page = next((page for page in observations if email.lower() in
                         [str(item).lower() for item in page.get("emails", [])]), {})
            email_inputs.append({**(profile.get("emails") or {}), "value": email,
                                 "source_url": page.get("source_url"), "checked_at": page.get("checked_at")})
        con_in.update(phones=phone_inputs, country=val(profile, "country") or raw_input.get("country")
                      or (engine_in or {}).get("country") or "",
                      primary_phone=val(profile, "phone", ""),
                      primary_email=next(iter(val(profile, "emails", [])), ""), email_evidence=email_inputs)
        if terms["generic_locals"]:
            con_in["generic_words"] = terms["generic_locals"]
        con = await call("base.contacts", con_in)
        cost += cost_of(con)
        merge(profile, con.get("fields") or {}, reliability, decisions)
        if con.get("confidence"):
            profile["contacts"] = {
                "value": {
                    key: con[key]
                    for key in (
                        "domain",
                        "domain_accepts_mail",
                        "generic_emails",
                        "personal_emails",
                        "email_pattern",
                        "contacts",
                        "decision_maker",
                    )
                },
                "source": "site+pattern",
                "confidence": int(con["confidence"]),
            }
            if con.get("decision_maker"):
                dm = con["decision_maker"]
                profile["decision_maker"] = {
                    "value": f"{dm['name']} ({dm['role']})" if dm.get("role") else dm["name"],
                    "source": "site",
                    "confidence": 80,
                }
        passes["3"] = {
            "skill": named("base.contacts", con),
            "ms": con.get("_ms"),
            "pattern": con.get("email_pattern"),
            "people": len(con.get("contacts") or []),
        }

        soc = await call("base.socials", {"socials": val(profile, "socials", {})})
        cost += cost_of(soc)
        if soc.get("official_business_pages"):
            profile["public_business_pages"] = {"value": soc["official_business_pages"], "source": "Meta official API", "confidence": 85}
        if soc.get("count"):
            profile["socials_profiles"] = {
                "value": {"profiles": soc["profiles"], "count": soc["count"], "presence": soc["presence"]},
                "source": "site",
                "confidence": int(soc["confidence"]),
            }
        passes["4"] = {"skill": named("base.socials", soc), "ms": soc.get("_ms"), "count": soc.get("count"),
                       **({"access_error": soc["access_error"]} if soc.get("access_error") else {})}

        eng_prof: dict[str, Any] = {}
        if engine and engine_in:
            eng_prof = await call(f"{engine}.profile", {"engine": engine_in, "profile": profile})
            cost += cost_of(eng_prof)
            for key in (
                "licence_number",
                "licence_type",
                "licence_type_label",
                "licence_expires",
                "days_to_expiry",
                "renewal_window",
                "lapsed",
                "segment",
                "inventory_signal",
            ):
                if eng_prof.get(key) not in (None, "", []):
                    official = key.startswith("licence") or key in ("days_to_expiry", "renewal_window", "lapsed")
                    profile[f"{engine}_{key}"] = {
                        "value": eng_prof[key],
                        "source": f"engine:{engine}" if official else f"engine:{engine}:inferred",
                        "confidence": 95 if official else 60,
                    }
            passes["6"] = {
                "skill": named(f"{engine}.profile", eng_prof),
                "ms": eng_prof.get("_ms"),
                "segment": eng_prof.get("segment"),
            }

        try:
            biz = await call("base.business_data", {
                "name": name,
                "state": state,
                "description": val(profile, "description", ""),
                "categories": val(profile, "categories", []),
                "services": val(profile, "services", []),
                "people": val(profile, "people", []),
                "socials_count": soc.get("count") or 0,
                "pages": len(scr.get("pages") or []),
                "licence_type_label": eng_prof.get("licence_type_label"),
                "html": page_html(scr),
                "engine": engine_id(engine),
            })
            cost += cost_of(biz)
        except Exception as exc:  # noqa: BLE001 — the record keeps its code-only layers
            if is_account_error(exc):
                raise ModelAccountBlocked(str(getattr(exc, "detail", "") or exc)) from exc
            passes["5"] = {
                "skill": named("base.business_data"),
                "skipped": "model call failed: completed work was kept",
            }
        else:
            if biz.get("confidence"):
                source = "site:code" if answer_from_code(named("base.business_data", biz), biz) else "model:estimate"
                for key in ("size_band", "revenue_band", "business_model", "naics", "naics_label"):
                    if biz.get(key):
                        profile[key] = {"value": biz[key], "source": source, "confidence": int(biz["confidence"])}
            passes["5"] = {
                "skill": named("base.business_data", biz),
                "ms": biz.get("_ms"),
                "cost_usd": round(float(biz.get("_cost_usd") or 0), 6),
                "basis": biz.get("basis"),
                **({"raw": biz.get("raw")} if not biz.get("business_model") and biz.get("raw") else {}),
            }

        ver = await call("base.verify", {"profile": profile, "raw": raw_input, "engine_profile": eng_prof})
        cost += cost_of(ver)
        confidence = int(ver.get("confidence") or 0)
        profile["tier"] = {"value": ver.get("tier"), "source": "verify", "confidence": confidence}
        if ver.get("flags"):
            profile["flags"] = {"value": ver["flags"], "source": "verify", "confidence": confidence}
        passes["7"] = {
            "skill": named("base.verify", ver),
            "ms": ver.get("_ms"),
            "layers": ver.get("layers"),
            "checks": ver.get("checks"),
        }
        return Outcome(status="done", profile=profile, passes=passes, confidence=confidence, cost_usd=cost)
    except ModelAccountBlocked as exc:
        return Outcome(status="queued", error=exc.reason, cost_usd=cost)
    except Exception as exc:  # noqa: BLE001
        return Outcome(
            status="failed",
            profile=profile,
            passes=passes,
            error="Base could not complete this request. Completed work was kept.",
            cost_usd=cost,
        )


def concurrency_limit(value: int | None) -> int:
    return max(1, min(int(value or 8), 12))


async def enrich_queued(
    indexes: list[int],
    rec_ids: list[str],
    inputs: list[dict[str, Any]],
    engine_rows: list[dict[str, Any] | None],
    registry: list[dict[str, Any]],
    supplied_registry: bool,
    engine: str | None,
    call: SkillCall,
    paused: Callable[[], bool],
    limit: int,
    refs: dict[str, str] | None = None,
    engine_version: str | None = None,
    on_result: Callable[[int, Outcome], Awaitable[None]] | None = None,
) -> list[tuple[int, Outcome | None]]:
    """Run the still-queued indexes. A paused model leaves the record untouched (None)."""
    sem = asyncio.Semaphore(concurrency_limit(limit))
    results: list[tuple[int, Outcome | None]] = []

    async def one(index: int) -> None:
        async with sem:
            if paused():
                results.append((index, None))
                return
            if supplied_registry:
                reg = registry
            else:
                reg = [row for row in registry if row.get("id") != str(rec_ids[index])]
            outcome = await enrich_one({**inputs[index], "_base_record_id": str(rec_ids[index])}, engine_rows[index], reg, engine, call, refs, engine_version)
            if on_result is not None:
                await on_result(index, outcome)
            results.append((index, outcome))

    tasks = [asyncio.create_task(one(index)) for index in indexes]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    results.sort(key=lambda item: item[0])
    return results
