"""GHOST-2 Shields Up: propose / dry-run / withdraw for Autumbra Watch.

Watch has no edge write credential and never calls a provider API. Founder tap
moves a proposal to dry_run (shows the plan) or withdrawn. Live apply/undo of
shields runs in per-client Enforce against a fixture control until a real
connected hand exists. Every step has a receipt.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .inventory import WATCHED_SITES
from .models import PLATFORM_TENANT

# Named zones only — plan shape for dogfood own sites. Watch does not write.
ALLOWED_ZONES = frozenset({"tervory.com"})
# Historical env name kept so older tests can clear it; Watch never reads it for writes.
TOKEN_ENV = "CLOUDFLARE_API_TOKEN_GHOST"
CF_API = ""  # No Watch write path to any edge API.
SITE_HOSTS = frozenset(WATCHED_SITES.values())
# Paths that legitimately take file uploads; the multipart block leaves them alone.
UPLOAD_PATHS: dict[str, tuple[str, ...]] = {"coolify.tervory.com": ("/upload/",), "app.tervory.com": ("/api/upload",),
                                            "tervory.com": (), "rig.tervory.com": ()}
# Cloudflare Managed Ruleset (the WAF managed rules; needs a plan that includes it).
CF_MANAGED_RULESET = "efb7b8c949ac4650a09736fc376e9aee"
KINDS = ("waf_managed", "rate_limit", "block_multipart_post", "challenge_bots", "block_ip", "revoke_sessions")
PHASES = {"waf_managed": "http_request_firewall_managed", "rate_limit": "http_ratelimit",
          "block_multipart_post": "http_request_firewall_custom", "challenge_bots": "http_request_firewall_custom",
          "block_ip": "http_request_firewall_custom", "revoke_sessions": "http_request_firewall_custom"}
PLAIN = {"waf_managed": "Turn on the managed firewall rules for {site}.",
         "rate_limit": "Slow down repeated requests to {path} on {site}.",
         "block_multipart_post": "Block file-upload style POST requests on {site}, except on its real upload paths.",
         "challenge_bots": "Ask likely-bad bots on {site} to prove they are a browser.",
         "block_ip": "Block the attacking address {address} on {site}.",
         "revoke_sessions": "Revoke active sessions on {site} after a confirmed account takeover signal."}
# Scanner and exploit-kit user agents seen against PHP and Coolify hosts; verified bots are never challenged.
BAD_AGENTS = ("sqlmap", "nikto", "nuclei", "masscan", "zgrab", "wpscan", "dirbuster", "gobuster")
SETTLE_SECONDS = 5.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token() -> str:
    """Watch never holds an edge write credential (ignores TOKEN_ENV)."""
    return ""


def mode() -> str:
    return "dry_run"


def zone_of(host: str) -> str | None:
    labels = host.lower().split(".")
    zone = ".".join(labels[-2:]) if len(labels) >= 2 else None
    return zone if zone in ALLOWED_ZONES else None


class ShieldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str = Field(max_length=80)
    kind: Literal["waf_managed", "rate_limit", "block_multipart_post", "challenge_bots", "block_ip", "revoke_sessions"]
    path: str | None = Field(default=None, max_length=120, pattern=r"^/[A-Za-z0-9._~/-]*$")
    address: str | None = Field(default=None, max_length=64, pattern=r"^[0-9A-Fa-f:.]*$")
    match_id: str | None = Field(default=None, max_length=200)
    reason: str = Field(default="", max_length=300)


def _quote(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._~/:-]*", value):
        raise ValueError("Unsafe value in a shield expression.")
    return '"' + value + '"'


def rule_for(kind: str, site: str, path: str | None, ref: str, address: str | None = None) -> dict:
    """The exact Cloudflare rule Autumbra would add. Expressions are built from fixed parts only."""
    host = f"(http.host eq {_quote(site)})"
    rule: dict[str, Any] = {"ref": ref, "description": f"Autumbra shield {ref}", "enabled": True}
    if kind == "waf_managed":
        rule.update(action="execute", expression=host, action_parameters={"id": CF_MANAGED_RULESET})
    elif kind == "rate_limit":
        if not path:
            raise ValueError("A rate limit needs a path.")
        rule.update(action="block", expression=f"({host[1:-1]} and starts_with(http.request.uri.path, {_quote(path)}))",
                    ratelimit={"characteristics": ["cf.colo.id", "ip.src"], "period": 10,
                               "requests_per_period": 20, "mitigation_timeout": 10})
    elif kind == "block_multipart_post":
        uploads = "".join(f" and not starts_with(http.request.uri.path, {_quote(p)})" for p in UPLOAD_PATHS.get(site, ()))
        rule.update(action="block", expression=(
            f"({host[1:-1]} and http.request.method eq \"POST\""
            f" and any(lower(http.request.headers[\"content-type\"][*])[*] contains \"multipart/form-data\"){uploads})"))
    elif kind == "challenge_bots":
        agents = " or ".join(f"lower(http.user_agent) contains {_quote(a)}" for a in BAD_AGENTS)
        rule.update(action="managed_challenge",
                    expression=f"({host[1:-1]} and not cf.client.bot and (http.user_agent eq \"\" or {agents}))")
    elif kind == "block_ip":
        if not address:
            raise ValueError("Blocking an address needs the address.")
        # Address already gate-checked by never_block before propose/raise.
        rule.update(action="block", expression=f"({host[1:-1]} and ip.src eq {address})")
    elif kind == "revoke_sessions":
        # Cloudflare Access / session revoke is planned as a brief when Access is not wired.
        rule.update(action="managed_challenge",
                    expression=f"({host[1:-1]} and cf.tls_client_auth.cert_verified eq false)")
        rule["autumbra_note"] = "revoke_sessions: prefer Access session revoke API when connected"
    else:
        raise ValueError("Unknown shield.")
    return rule


def plan_for(kind: str, site: str, path: str | None, ref: str, address: str | None = None) -> dict:
    """What Autumbra would change, in the same words for dry-run and live."""
    zone = zone_of(site)
    phase = PHASES[kind]
    rule = rule_for(kind, site, path, ref, address=address)
    return {"zone": zone, "phase": phase, "rule": rule, "address": address,
            "calls": [{"method": "GET", "path": f"/zones?name={zone}"},
                      {"method": "GET", "path": f"/zones/{{zone_id}}/rulesets/phases/{phase}/entrypoint"},
                      {"method": "POST", "path": "/zones/{zone_id}/rulesets/{ruleset_id}/rules",
                       "body": {**{k: v for k, v in rule.items() if k != "autumbra_note"}, "position": {"before": ""}}},
                      {"method": "GET", "path": f"https://{site}/", "why": "health check"}],
            "undo": {"method": "DELETE", "path": "/zones/{zone_id}/rulesets/{ruleset_id}/rules/{rule_id}"},
            "plain": PLAIN[kind].format(site=site, path=path or "/", address=address or "")}


def proposals_for_match(match: dict, edges: dict[str, str]) -> list[ShieldRequest]:
    """Shields worth proposing for an open advisory match. Patching stays the real fix."""
    app = match["affected"]["app"]
    site = WATCHED_SITES.get(app)
    if site is None:
        return []
    severity = match.get("severity") or {}
    serious = severity.get("known_exploited") or severity.get("label") in {"critical", "high"}
    if not serious:
        return []
    reason = f"{match['advisory_id']} on {app}"
    out = []
    text = (match.get("summary") or "").lower()
    if match["affected"]["name"] in {"php", "coolify"} or any(w in text for w in ("upload", "multipart", "file write")):
        out.append(ShieldRequest(site=site, kind="block_multipart_post", match_id=match["id"], reason=reason))
    out.append(ShieldRequest(site=site, kind="waf_managed", match_id=match["id"], reason=reason))
    return out


class CloudflareError(RuntimeError):
    pass


class Cloudflare:
    """Removed Watch write path. Instantiating or calling raises.

    Live edge apply lives in Autumbra Enforce (fixture control until a real hand).
    """

    def __init__(self, http, api_token: str):
        raise CloudflareError("Autumbra Watch has no edge write path.")

    async def call(self, method: str, path: str, *, params=None, body=None) -> Any:
        raise CloudflareError("Autumbra Watch has no edge write path.")

    async def zone_id(self, zone: str) -> str:
        raise CloudflareError("Autumbra Watch has no edge write path.")

    async def add_rule(self, zone_id: str, phase: str, rule: dict) -> tuple[str, str]:
        raise CloudflareError("Autumbra Watch has no edge write path.")

    async def delete_rule(self, zone_id: str, ruleset_id: str, rule_id: str) -> None:
        raise CloudflareError("Autumbra Watch has no edge write path.")



async def site_health(http, site: str) -> bool:
    """Our own site answers its home page (redirects to a login count as up)."""
    if site not in SITE_HOSTS:
        return False
    from .probes import public_addresses
    try:
        await asyncio.wait_for(asyncio.to_thread(public_addresses, site), timeout=10)
        response = await http.get(f"https://{site}/", follow_redirects=False, timeout=15,
                                  headers={"User-Agent": "tervory-ghost-health"})
    except Exception:
        return False
    return response.status_code < 400 or response.status_code == 401


class Shields:
    def __init__(self, store, http, receipts, *, tenant: str = PLATFORM_TENANT,
                 health: Callable[[str], Awaitable[bool]] | None = None,
                 pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], str] = now_iso, edges: Callable[[], dict[str, str]] | None = None):
        self.store, self.http, self.receipts, self.tenant = store, http, receipts, tenant
        self.health = health or (lambda site: site_health(self.http, site))
        self.pause, self.clock = pause, clock
        self.edges = edges or self._edges_from_inventory

    def _edges_from_inventory(self) -> dict[str, str]:
        out = {}
        for c in self.store.inventory(self.tenant):
            if c["kind"] == "edge" and c["app"] in WATCHED_SITES:
                out[WATCHED_SITES[c["app"]]] = c["name"]
        return out

    async def _receipt(self, shield: dict, step: str, outcome: str) -> None:
        rid = str(uuid.uuid5(uuid.UUID(shield["id"]), f"{step}:{len(shield['receipts'])}"))
        entry = {"step": step, "at": self.clock(), "ledger_id": rid, "status": "pending"}
        try:
            await self.receipts.record(rid, f"shield_{step}", outcome, task_type="ghost.shield")
            entry["status"] = "recorded"
        except Exception:
            entry["status"] = "pending"
        shield["receipts"].append(entry)
        self.store.save_shield(self.tenant, shield)

    async def propose(self, request: ShieldRequest) -> dict:
        if request.site not in SITE_HOSTS:
            raise ValueError("Autumbra Watch only shields our own sites.")
        if request.kind == "block_ip" and not request.address:
            raise ValueError("Blocking an address needs the address.")
        # block_ip uniqueness: store a synthetic path key so two addresses do not collapse.
        key_path = request.path or ""
        if request.kind == "block_ip":
            key_path = f"/__block_ip__/{request.address}"
        existing = self.store.open_shield(self.tenant, request.site, request.kind, key_path)
        if existing:
            return existing
        sid = str(uuid.uuid4())
        ref = "ghost-" + sid.replace("-", "")[:20]
        edge = self.edges().get(request.site)
        on_cloudflare = edge == "cloudflare" and zone_of(request.site) is not None
        shield = {"id": sid, "site": request.site, "kind": request.kind, "path": key_path,
                  "address": request.address or "",
                  "match_id": request.match_id, "reason": request.reason, "ref": ref,
                  "state": "proposed" if on_cloudflare else "brief", "proposed_at": self.clock(),
                  "edge": edge or "unknown",
                  "plan": plan_for(request.kind, request.site, request.path, ref, address=request.address),
                  "applied": None, "up_at": None, "ended_at": None, "receipts": []}
        if not on_cloudflare:
            shield["brief"] = {"for": "Grok", "title": f"Edge protection for {request.site}",
                               "steps": [f"{request.site} is not confirmed behind our edge network, so Autumbra Watch will not change it.",
                                         shield["plan"]["plain"],
                                         "Apply the equivalent at the site's own edge after founder approval, "
                                         "then post a fresh host report."]}
        self.store.save_shield(self.tenant, shield)
        await self._receipt(shield, "proposed", "delivered")
        return shield

    async def propose_for_matches(self) -> list[dict]:
        """Open matches → ThreatPayload air-gap → propose only. Burns skip; never raise."""
        # Lazy import: threat_payload imports ShieldRequest from this module.
        from .threat_payload import ThreatBurned, match_to_threat_payload, threat_to_shield_request

        out = []
        edges = self.edges()
        # One proposal per match and kind, ever: a withdrawn proposal is not proposed again.
        seen = {(s.get("match_id"), s["kind"]) for s in self.store.shields(self.tenant)}
        for match in self.store.matches(self.tenant, "open"):
            for request in proposals_for_match(match, edges):
                if (request.match_id, request.kind) in seen:
                    continue
                try:
                    payload = match_to_threat_payload(match, request.kind, request.path)
                    shield = await self.propose(threat_to_shield_request(payload))
                except ThreatBurned:
                    continue  # Schema burn — no propose, no echo.
                except ValueError:
                    continue
                out.append(shield)
                seen.add((request.match_id, request.kind))
        return out

    async def settle_resolved(self) -> list[dict]:
        """Once the patch is in, unused proposals are closed. A live shield stays until the founder taps Undo."""
        resolved = {m["id"] for m in self.store.matches(self.tenant, "resolved")}
        out = []
        for shield in self.store.shields(self.tenant):
            if shield.get("match_id") not in resolved:
                continue
            if shield["state"] in {"proposed", "dry_run", "brief"}:
                shield["state"], shield["ended_at"] = "not_needed", self.clock()
                shield["note"] = "The fix is installed, so this shield is no longer needed."
                await self._receipt(shield, "not_needed", "delivered")
                out.append(shield)
            elif shield["state"] == "up" and not shield.get("note"):
                shield["note"] = "The fix is installed. You can Undo this shield when you are ready."
                self.store.save_shield(self.tenant, shield)
        return out

    async def after_cycle(self) -> None:
        await self.settle_resolved()
        await self.propose_for_matches()

    async def raise_shield(self, shield_id: str) -> dict:
        """Watch dry-run only. Live apply is Enforce + fixture/real hand."""
        shield = self.store.shield(self.tenant, shield_id)
        if shield is None:
            raise LookupError("Shield not found.")
        if shield["state"] not in {"proposed", "dry_run"}:
            return shield
        # Dry-run: show exactly what would change. No provider network call.
        shield["state"], shield["dry_run_at"] = "dry_run", self.clock()
        await self._receipt(shield, "dry_run", "delivered")
        return shield

    async def _take_down(self, shield: dict, state: str) -> dict:
        """Watch cannot take down a live edge rule — no write path."""
        shield["state"], shield["ended_at"] = "undo_failed", self.clock()
        shield["note"] = (
            f"Autumbra Watch has no edge write path for rule {shield['ref']}. "
            "Undo live shields from Enforce."
        )
        await self._receipt(shield, "undo_failed", "failed")
        return shield

    async def undo(self, shield_id: str) -> dict:
        shield = self.store.shield(self.tenant, shield_id)
        if shield is None:
            raise LookupError("Shield not found.")
        if shield["state"] in {"proposed", "dry_run", "brief"}:
            shield["state"], shield["ended_at"] = "withdrawn", self.clock()
            await self._receipt(shield, "withdrawn", "delivered")
            return shield
        if shield["state"] not in {"up", "undo_failed"} or not shield.get("applied"):
            return shield
        return await self._take_down(shield, "undone")

    def summary(self) -> dict:
        """The numbers for the founder tile."""
        shields = self.store.shields(self.tenant)
        matches = self.store.matches(self.tenant, None)
        open_ = [m for m in matches if m["state"] == "open"]
        by_severity: dict[str, int] = {}
        for m in open_:
            label = "known_exploited" if m["severity"].get("known_exploited") else m["severity"].get("label", "unknown")
            by_severity[label] = by_severity.get(label, 0) + 1
        ttk = [m["receipt"]["time_to_know_seconds"] for m in matches
               if isinstance(m.get("receipt", {}).get("time_to_know_seconds"), (int, float))]
        flagged = {m["id"]: m["receipt"]["flagged_at"] for m in matches if m.get("receipt", {}).get("flagged_at")}
        tts = []
        for s in shields:
            mid = s.get("match_id")
            if mid not in flagged:
                continue
            # Founder Shield tap in dry-run (Watch never writes the edge).
            stamped = None
            if s.get("up_at") and s["state"] in {"up", "undone"}:
                stamped = s["up_at"]
            elif s.get("dry_run_at"):
                stamped = s["dry_run_at"]
            if stamped:
                tts.append(max(0, (_parse(stamped) - _parse(flagged[mid])).total_seconds()))
        return {"mode": mode(), "open_by_severity": by_severity, "open_total": len(open_),
                "time_to_know_seconds": {"median": _median(ttk), "worst": max(ttk) if ttk else None, "target": 3600},
                "time_to_shield_seconds": {"median": _median(tts), "worst": max(tts) if tts else None, "target": 86400},
                "sites_broken_by_a_shield": sum(1 for s in shields if s["state"] in {"auto_undone", "undo_failed"}),
                "shields": {state: sum(1 for s in shields if s["state"] == state)
                            for state in sorted({s["state"] for s in shields})}}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _median(values: list) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return float(ordered[mid]) if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def public_view(shield: dict) -> dict:
    """What leaves Ghost: no Cloudflare ids beyond what the founder needs, never a token."""
    view = {k: shield.get(k) for k in ("id", "site", "kind", "path", "address", "match_id", "reason", "ref", "state",
                                       "proposed_at", "dry_run_at", "up_at", "ended_at", "edge", "note", "brief",
                                       "shield_mode", "fix_mode")}
    view["plan"] = shield["plan"]
    view["receipts"] = shield["receipts"]
    return json.loads(json.dumps(view))
