"""GHOST-2 part 2: the advisory watcher.

Every 15 minutes Ghost pulls these legitimate, public sources only:
- CISA Known Exploited Vulnerabilities (JSON feed);
- OSV (api.osv.dev), queried by package and version from the inventory;
- GitHub Security Advisories (public REST, unauthenticated);
- NVD 2.0 (public rate limit, no key);
- vendor notes: PHP releases, Node security releases, Coolify GitHub releases
  and LiteLLM's GitHub advisories.

No dark web, no bought or leaked data, and no request to any other host:
``Feeds`` refuses every URL outside ``ALLOWED_HOSTS``. Each advisory is matched
against the inventory with OSV range semantics; a match becomes a Ghost issue
with a receipt that carries both timestamps (published, flagged).
"""
from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from uuid import UUID, uuid5

from .inventory import FLOATING_WATCHED, UNPINNED
from .models import PLATFORM_TENANT
from .versions import (first_fix_above, generic_key, in_constraints, in_cpe_bounds, in_osv_range,
                       normalise_ecosystem, normalise_name, ordering)

INTERVAL_SECONDS = 900
ALLOWED_HOSTS = frozenset({"www.cisa.gov", "api.osv.dev", "api.github.com", "services.nvd.nist.gov",
                           "www.php.net", "nodejs.org"})
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/"
GHSA_URL = "https://api.github.com/advisories"
NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
PHP_RELEASES_URL = "https://www.php.net/releases/index.php"
NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
COOLIFY_RELEASES_URL = "https://api.github.com/repos/coollabsio/coolify/releases"
LITELLM_ADVISORIES_URL = "https://api.github.com/repos/BerriAI/litellm/security-advisories"
REDIS_RELEASES_URL = "https://api.github.com/repos/redis/redis/releases"
REDIS_ADVISORIES_URL = "https://api.github.com/repos/redis/redis/security-advisories"
NAMESPACE = UUID("6f1d3c2e-8a51-4c3e-9a0b-6f2e1d0c9b7a")
UUID_NAMESPACE = NAMESPACE
MAX_BYTES = 25_000_000
NVD_PAUSE_SECONDS = 6.5  # Public NVD limit is 5 requests in a rolling 30 seconds.

# NVD CPE (vendor, product) -> inventory component name. Only these are kept from NVD.
CPE_PRODUCTS = {("php", "php"): "php", ("nodejs", "node.js"): "node", ("coollabs", "coolify"): "coolify",
                ("litellm", "litellm"): "litellm", ("berriai", "litellm"): "litellm",
                ("traefik", "traefik"): "traefik", ("chatwoot", "chatwoot"): "chatwoot",
                ("redis", "redis"): "redis", ("postgresql", "postgresql"): "postgresql",
                ("qdrant", "qdrant"): "qdrant", ("python", "python"): "python",
                ("docker", "engine"): "docker-engine", ("mobyproject", "moby"): "docker-engine",
                ("f5", "nginx"): "nginx", ("nginx", "nginx"): "nginx"}
KEV_PRODUCTS = {("php", "php"): "php", ("nodejs", "node.js"): "node", ("coollabs", "coolify"): "coolify",
                ("coolify", "coolify"): "coolify", ("berriai", "litellm"): "litellm", ("traefik", "traefik"): "traefik",
                ("redis", "redis"): "redis", ("postgresql", "postgresql"): "postgresql", ("docker", "engine"): "docker-engine"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text += "T00:00:00+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                moment = datetime.strptime(value.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def severity_label(score: float | None, label: str | None = None) -> str:
    if score is not None:
        if score >= 9:
            return "critical"
        if score >= 7:
            return "high"
        if score >= 4:
            return "medium"
        return "low" if score > 0 else "none"
    label = (label or "").strip().lower()
    return {"moderate": "medium"}.get(label, label if label in {"critical", "high", "medium", "low"} else "unknown")


# ---------- CVSS 3.x base score (for OSV, which ships vectors only) ----------

_CVSS3 = {"AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}, "AC": {"L": 0.77, "H": 0.44},
          "UI": {"N": 0.85, "R": 0.62}, "C": {"H": 0.56, "L": 0.22, "N": 0}, "I": {"H": 0.56, "L": 0.22, "N": 0},
          "A": {"H": 0.56, "L": 0.22, "N": 0}}


def _roundup(value: float) -> float:
    integer = round(value * 100000)
    return integer / 100000.0 if integer % 10000 == 0 else (math.floor(integer / 10000) + 1) / 10.0


def cvss3_score(vector: str) -> float | None:
    if not isinstance(vector, str) or not vector.startswith("CVSS:3."):
        return None
    try:
        metrics = dict(part.split(":", 1) for part in vector.split("/")[1:])
        scope_changed = metrics["S"] == "C"
        pr = {"N": 0.85, "L": 0.68 if scope_changed else 0.62, "H": 0.5 if scope_changed else 0.27}[metrics["PR"]]
        iss = 1 - (1 - _CVSS3["C"][metrics["C"]]) * (1 - _CVSS3["I"][metrics["I"]]) * (1 - _CVSS3["A"][metrics["A"]])
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if scope_changed else 6.42 * iss
        exploit = 8.22 * _CVSS3["AV"][metrics["AV"]] * _CVSS3["AC"][metrics["AC"]] * pr * _CVSS3["UI"][metrics["UI"]]
    except (KeyError, ValueError):
        return None
    if impact <= 0:
        return 0.0
    total = 1.08 * (impact + exploit) if scope_changed else impact + exploit
    return _roundup(min(total, 10))


# ---------- normalised advisories ----------

def _clip(text: Any, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit] if isinstance(text, str) else ""


def _refs(urls: list[Any]) -> list[str]:
    out = []
    for url in urls:
        if isinstance(url, str) and url.startswith("https://") and len(url) < 400 and url not in out:
            out.append(url)
    return out[:5]


def advisory(id: str, source: str, published: str, *, aliases=(), modified: str = "", summary: str = "",
             cvss: float | None = None, severity: str | None = None, affected: list[dict] | None = None,
             references=(), shield_hint: str | None = None) -> dict:
    return {"id": id, "aliases": sorted({a for a in aliases if isinstance(a, str) and a != id}),
            "source": source, "published": published, "modified": modified or published,
            "summary": _clip(summary), "cvss": cvss, "severity": severity_label(cvss, severity),
            "affected": affected or [], "references": _refs(list(references)), "shield_hint": shield_hint}


def parse_osv(vuln: dict) -> dict | None:
    published = parse_time(vuln.get("published")) or parse_time(vuln.get("modified"))
    if not isinstance(vuln.get("id"), str) or published is None:
        return None
    affected = []
    for item in vuln.get("affected") or []:
        package = item.get("package") or {}
        ecosystem = normalise_ecosystem(package.get("ecosystem", ""))
        name = package.get("name")
        if not isinstance(name, str):
            continue
        ranges = [{"type": r.get("type", "ECOSYSTEM"), "events": r.get("events") or []}
                  for r in item.get("ranges") or [] if r.get("type") in {"ECOSYSTEM", "SEMVER"}]
        versions = [v for v in item.get("versions") or [] if isinstance(v, str)][:2000]
        if ranges or versions:
            affected.append({"ecosystem": ecosystem, "name": normalise_name(ecosystem, name),
                             "ranges": ranges, "versions": versions})
    score = None
    for entry in vuln.get("severity") or []:
        if isinstance(entry, dict) and str(entry.get("type", "")).startswith("CVSS_V3"):
            score = cvss3_score(entry.get("score", ""))
    label = (vuln.get("database_specific") or {}).get("severity")
    return advisory(vuln["id"], "osv", iso(published), aliases=vuln.get("aliases") or [],
                    modified=vuln.get("modified", ""), summary=vuln.get("summary") or vuln.get("details", ""),
                    cvss=score, severity=label, affected=affected,
                    references=[r.get("url") for r in vuln.get("references") or [] if isinstance(r, dict)])


_GHSA_ECOSYSTEMS = {"pip": "pypi", "npm": "npm", "composer": "packagist"}


def parse_ghsa(item: dict, source: str = "github", product: str | None = None) -> dict | None:
    """A GitHub advisory. product: for a project's own advisories (e.g. redis/redis) whose package has no
    ecosystem, the rule targets that product as installed software."""
    published = parse_time(item.get("published_at"))
    if not isinstance(item.get("ghsa_id"), str) or published is None or item.get("withdrawn_at"):
        return None
    affected = []
    for vuln in item.get("vulnerabilities") or []:
        package = (vuln or {}).get("package") or {}
        raw_ecosystem = str(package.get("ecosystem") or "").lower()
        ecosystem = _GHSA_ECOSYSTEMS.get(raw_ecosystem)
        name, spec = package.get("name"), vuln.get("vulnerable_version_range")
        if ecosystem is None and product and raw_ecosystem in {"", "other"}:
            ecosystem, name = "generic", product
        if ecosystem is None or not isinstance(name, str) or not isinstance(spec, str) or not spec.strip():
            continue
        fixes = vuln.get("first_patched_version") or vuln.get("patched_versions")
        if isinstance(fixes, dict):
            fixes = fixes.get("identifier")
        affected.append({"ecosystem": ecosystem, "name": normalise_name(ecosystem, name), "constraints": spec,
                         "fixes": [f.strip() for f in str(fixes or "").split(",") if f.strip()]})
    cvss = item.get("cvss") or {}
    score = cvss.get("score") if isinstance(cvss.get("score"), (int, float)) and cvss.get("score") else None
    if score is None:
        for version in ("cvss_v4", "cvss_v3"):
            value = ((item.get("cvss_severities") or {}).get(version) or {}).get("score")
            if isinstance(value, (int, float)) and value:
                score = float(value)
                break
    aliases = [item.get("cve_id")] + [i.get("value") for i in item.get("identifiers") or [] if isinstance(i, dict)]
    return advisory(item["ghsa_id"], source, iso(published), aliases=aliases,
                    modified=item.get("updated_at", ""), summary=item.get("summary", ""), cvss=score,
                    severity=item.get("severity"), affected=affected,
                    references=[item.get("html_url"), *(item.get("references") or [])])


def parse_nvd(entry: dict) -> dict | None:
    cve = (entry or {}).get("cve") or {}
    published = parse_time(cve.get("published"))
    if not isinstance(cve.get("id"), str) or published is None or cve.get("vulnStatus") == "Rejected":
        return None
    affected: dict[str, dict] = {}
    for config in cve.get("configurations") or []:
        for node in config.get("nodes") or []:
            for match in node.get("cpeMatch") or []:
                parts = str(match.get("criteria", "")).split(":")
                if not match.get("vulnerable") or len(parts) < 6 or parts[2] not in {"a", "o"}:
                    continue
                name = CPE_PRODUCTS.get((parts[3].lower(), parts[4].lower()))
                if name is None:
                    continue
                bounds = {k: match[k] for k in ("versionStartIncluding", "versionStartExcluding",
                                                 "versionEndIncluding", "versionEndExcluding") if match.get(k)}
                if parts[5] not in {"*", "-"}:
                    bounds["exact"] = parts[5]
                rule = affected.setdefault(name, {"ecosystem": "generic", "name": name, "cpe": []})
                rule["cpe"].append(bounds)
                if match.get("versionEndExcluding"):
                    rule.setdefault("fixes", []).append(match["versionEndExcluding"])
    score, label = None, None
    metrics = cve.get("metrics") or {}
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30"):
        for metric in metrics.get(key) or []:
            data = metric.get("cvssData") or {}
            if isinstance(data.get("baseScore"), (int, float)):
                score, label = float(data["baseScore"]), data.get("baseSeverity")
                break
        if score is not None:
            break
    summary = next((d.get("value") for d in cve.get("descriptions") or [] if d.get("lang") == "en"), "")
    return advisory(cve["id"], "nvd", iso(published), modified=cve.get("lastModified", ""), summary=summary,
                    cvss=score, severity=label, affected=list(affected.values()),
                    references=[r.get("url") for r in cve.get("references") or [] if isinstance(r, dict)])


def parse_kev(feed: dict) -> dict[str, dict]:
    out = {}
    for item in (feed or {}).get("vulnerabilities") or []:
        cve = item.get("cveID")
        if isinstance(cve, str) and re.fullmatch(r"CVE-\d{4}-\d{4,}", cve):
            out[cve] = {"date_added": item.get("dateAdded", ""),
                        "product": KEV_PRODUCTS.get((str(item.get("vendorProject", "")).strip().lower(),
                                                     str(item.get("product", "")).strip().lower()))}
    return out


def _branch_rule(name: str, version: str, published: str, source: str, id: str, summary: str) -> dict:
    """A vendor security release X.Y.Z means everything in the X.Y line before it is affected."""
    numbers = re.match(r"^v?(\d+)\.(\d+)", version)
    events = [{"introduced": f"{numbers.group(1)}.{numbers.group(2)}.0"} if numbers else {"introduced": "0"},
              {"fixed": version.lstrip("v")}]
    return advisory(id, source, published, summary=summary, severity="unknown",
                    affected=[{"ecosystem": "generic", "name": name, "ranges": [{"type": "ECOSYSTEM", "events": events}],
                               "fixes": [version.lstrip("v")]}])


def parse_php_releases(feed: dict) -> list[dict]:
    newest: dict[str, tuple] = {}
    for version, meta in (feed or {}).items():
        if not isinstance(meta, dict) or not re.fullmatch(r"\d+\.\d+\.\d+", str(version)):
            continue
        tags = [str(t).lower() for t in meta.get("tags") or []]
        published = parse_time(meta.get("date"))
        if "security" not in tags or published is None:
            continue
        line = ".".join(version.split(".")[:2])
        if line not in newest or generic_key(version) > generic_key(newest[line][0]):
            newest[line] = (version, published)
    return [_branch_rule("php", v, iso(p), "vendor:php", f"PHP-{v}", f"PHP {v} is a security release.")
            for v, p in newest.values()]


def parse_node_index(index: list) -> list[dict]:
    newest: dict[str, tuple] = {}
    for item in index or []:
        version = str((item or {}).get("version", ""))
        published = parse_time(item.get("date"))
        if not item.get("security") or published is None or not re.fullmatch(r"v\d+\.\d+\.\d+", version):
            continue
        major = version[1:].split(".")[0]
        if major not in newest or generic_key(version) > generic_key(newest[major][0]):
            newest[major] = (version, published)
    out = []
    for version, published in newest.values():
        rule = _branch_rule("node", version, iso(published), "vendor:node", f"NODE-{version[1:]}",
                            f"Node.js {version[1:]} is a security release.")
        rule["affected"][0]["ranges"][0]["events"][0] = {"introduced": f"{version[1:].split('.')[0]}.0.0"}
        out.append(rule)
    return out


# A release only counts when its notes name a fix: a CVE/GHSA id, or "security fix/release", or a vulnerability.
# "automatic security updates" (a feature) is not a fix.
_SECURITY_FIX = re.compile(r"(CVE-\d{4}-\d+|GHSA-[\w-]+|security[ -](fix|release|patch|advisor)\w*|vulnerabilit\w*"
                           r"|urgency:?\s*security)",
                           re.IGNORECASE)


def parse_github_releases(product: str, label: str, repo: str, releases: list) -> list[dict]:
    """Newest security fix release per X.Y line: it only flags older versions in that same line."""
    newest: dict[str, tuple] = {}
    for item in releases or []:
        if not isinstance(item, dict) or item.get("prerelease") or item.get("draft"):
            continue
        tag, body = str(item.get("tag_name", "")), str(item.get("body") or "")
        published = parse_time(item.get("published_at"))
        if not re.fullmatch(r"v?\d+\.\d+\.\d+", tag) or published is None or not _SECURITY_FIX.search(body):
            continue
        line = ".".join(tag.lstrip("v").split(".")[:2])
        if line not in newest or generic_key(tag) > generic_key(newest[line][0]):
            newest[line] = (tag, published, sorted(set(re.findall(r"CVE-\d{4}-\d{4,}", body)))[:20])
    out = []
    for tag, published, cves in newest.values():
        version = tag.lstrip("v")
        rule = _branch_rule(product, version, iso(published), f"vendor:{product}", f"{product.upper()}-{version}",
                            f"{label} {version} includes security fixes.")
        rule["aliases"] = cves
        rule["references"] = _refs([f"https://github.com/{repo}/releases/tag/{tag}"])
        out.append(rule)
    return out


def parse_coolify_releases(releases: list) -> list[dict]:
    return parse_github_releases("coolify", "Coolify", "coollabsio/coolify", releases)


def parse_redis_releases(releases: list) -> list[dict]:
    return parse_github_releases("redis", "Redis", "redis/redis", releases)


# ---------- matching ----------

MATCHABLE_KINDS = {"package", "runtime", "software"}


def version_line(version: str) -> tuple[str | None, str | None] | None:
    """A floating version ('7.x', '7.4.x', 'unpinned') as a half-open [low, high) window; None if exact."""
    if version == UNPINNED:
        return (None, None)
    match = re.fullmatch(r"(\d+)\.x", version)
    if match:
        return (f"{match.group(1)}.0.0", f"{int(match.group(1)) + 1}.0.0")
    match = re.fullmatch(r"(\d+)\.(\d+)\.x", version)
    if match:
        major, minor = match.groups()
        return (f"{major}.{minor}.0", f"{major}.{int(minor) + 1}.0")
    return None


def precise(component: dict) -> bool:
    """Runtimes and apps need an exact x.y.z: a base image line like python:3.12 can't be range-checked.
    Floating tags of software we watch (redis:7, redis:alpine) are matched conservatively instead."""
    if component["ecosystem"] != "generic":
        return True
    if component["name"] in FLOATING_WATCHED and version_line(component["version"]) is not None:
        return True
    return re.match(r"^\d+\.\d+\.\d+", component["version"]) is not None


def _rule_versions(rule: dict) -> set[str]:
    found: set[str] = set(rule.get("versions") or []) | set(rule.get("fixes") or [])
    for entry in rule.get("ranges") or []:
        found.update(str(v) for e in entry.get("events") or [] if isinstance(e, dict) for v in e.values())
    found.update(re.findall(r"\d+(?:\.\d+)+", str(rule.get("constraints") or "")))
    for bounds in rule.get("cpe") or []:
        found.update(str(v) for v in bounds.values())
    return found


def _bump(version: str) -> str:
    parts = version.split(".")
    return ".".join([*parts[:-1], str(int(parts[-1]) + 1)]) if parts[-1].isdigit() else version


def line_applies(rule: dict, component: dict, window: tuple[str | None, str | None]) -> bool | None:
    """Could any version in the window be affected? Each affected stretch starts at the window's low end
    or at a version named in the rule, so probing those (and just past them) is enough."""
    key = ordering("generic")
    low, high = window
    probes = {low or "0.0.0"}
    for version in _rule_versions(rule):
        if key(version) is not None:
            probes.update({version, _bump(version)})
    inside = [p for p in probes if key(p) is not None and (low is None or key(p) >= key(low))
              and (high is None or key(p) < key(high))]
    results = [rule_applies(rule, {**component, "version": p}) for p in inside]
    if any(r is True for r in results):
        return True
    if results and all(r is False for r in results):
        return False
    return None


def rule_applies(rule: dict, component: dict) -> bool | None:
    """True / False, or None when it can't be decided (never treated as safe or as a match)."""
    version = component["version"]
    window = version_line(version) if component["name"] in FLOATING_WATCHED else None
    if window is not None:
        return line_applies(rule, component, window)
    results: list[bool | None] = []
    if version in (rule.get("versions") or []):
        return True
    for entry in rule.get("ranges") or []:
        results.append(in_osv_range(version, entry.get("events") or [], ordering(rule["ecosystem"], entry.get("type", ""))))
    if rule.get("constraints"):
        results.append(in_constraints(version, rule["constraints"], ordering(rule["ecosystem"])))
    for bounds in rule.get("cpe") or []:
        results.append(in_cpe_bounds(version, bounds, ordering("generic")))
    if any(r is True for r in results):
        return True
    if results and all(r is False for r in results):
        return False
    if not results and rule.get("versions"):
        return False
    return None


def rule_targets(rule: dict, component: dict) -> bool:
    if component["kind"] not in MATCHABLE_KINDS or not precise(component):
        return False
    if rule["ecosystem"] in {"pypi", "npm", "packagist"}:
        return (component["kind"] == "package" and component["ecosystem"] == rule["ecosystem"]
                and normalise_name(rule["ecosystem"], component["name"]) == rule["name"])
    return component["kind"] in {"runtime", "software"} and component["name"] == rule["name"]


def canonical_id(item: dict) -> str:
    cves = sorted(a for a in [item["id"], *item.get("aliases", [])] if re.fullmatch(r"CVE-\d{4}-\d{4,}", a))
    return cves[0] if cves else item["id"]


def what_to_do(item: dict, rule: dict, component: dict) -> dict:
    fixes = [*(rule.get("fixes") or [])]
    for entry in rule.get("ranges") or []:
        fixes.extend(e["fixed"] for e in entry.get("events") or [] if isinstance(e, dict) and "fixed" in e)
    window = version_line(component["version"]) if component["name"] in FLOATING_WATCHED else None
    if window is not None:
        # Floating tag: the newest fix that keeps the same line (or any fix when unpinned), across every
        # range this advisory lists for the product.
        for other in item.get("affected") or []:
            if other is not rule and other.get("name") == rule.get("name"):
                fixes.extend(other.get("fixes") or [])
                for entry in other.get("ranges") or []:
                    fixes.extend(e["fixed"] for e in entry.get("events") or [] if isinstance(e, dict) and "fixed" in e)
        key = ordering("generic")
        low, high = window
        fits = [f for f in fixes if key(f) is not None and (low is None or key(f) >= key(low))
                and (high is None or key(f) < key(high))]
        fix = max(fits, key=key) if fits else None
        action = {"patch_to": fix, "shield": item.get("shield_hint"), "exact_version_known": False}
        tag = "an unpinned image tag" if low is None else f"a floating {component['version']} image tag"
        action["plain"] = (f"{component['name']} on {component['app']} runs {tag}, so its exact version isn't known. "
                           + (f"Pin it to {fix} or later and redeploy." if fix else
                              "Pin it to a fixed version once one is published."))
        return action
    fix = first_fix_above(component["version"], fixes, ordering(rule["ecosystem"]))
    action = {"patch_to": fix, "shield": item.get("shield_hint")}
    if fix:
        action["plain"] = f"Update {component['name']} on {component['app']} to {fix} or later."
    else:
        action["plain"] = f"No fixed version is published yet for {component['name']}; raise a shield and watch for a fix."
    return action


def evaluate(advisories: list[dict], inventory: list[dict], kev: dict[str, str]) -> list[dict]:
    """Every (advisory, app, component) that is affected now, grouped by canonical (CVE) id."""
    found: dict[str, dict] = {}
    collapsed: set[str] = set()
    newest_fix: dict[str, str] = {}
    for item in advisories:
        cid = canonical_id(item)
        for rule in item.get("affected") or []:
            for component in inventory:
                if not rule_targets(rule, component) or rule_applies(rule, component) is not True:
                    continue
                match_id = cid
                if (component["version"] == UNPINNED and item["source"].startswith("vendor:")
                        and item["id"].startswith(f"{rule['name'].upper()}-")):
                    # An unpinned tag could be on any line, so every line's security release would match;
                    # one finding ("move to a pinned, patched release") says it once.
                    match_id = f"{rule['name'].upper()}-SECURITY-RELEASES"
                    collapsed.add(f"{match_id}|{component['app']}|{component['kind']}|{component['name']}")
                fingerprint = f"{match_id}|{component['app']}|{component['kind']}|{component['name']}"
                ids = {item["id"], *item.get("aliases", [])}
                on_kev = any(i in kev for i in ids)
                current = found.get(fingerprint)
                if current is None or item["published"] < current["advisory_published"]:
                    found[fingerprint] = {
                        "fingerprint": fingerprint, "advisory_id": match_id, "source_ids": sorted(ids),
                        "first_source": item["source"], "advisory_published": item["published"],
                        "summary": item.get("summary", ""), "references": item.get("references", []),
                        "affected": {k: component[k] for k in ("app", "kind", "name", "version", "ecosystem")},
                        "severity": {"cvss": item.get("cvss"), "label": item.get("severity", "unknown"),
                                     "known_exploited": on_kev},
                        "what_to_do": what_to_do(item, rule, component)}
                else:
                    current["source_ids"] = sorted({*current["source_ids"], *ids})
                    current["severity"]["known_exploited"] |= on_kev
                    if current["severity"]["cvss"] is None and item.get("cvss") is not None:
                        current["severity"].update(cvss=item["cvss"], label=item["severity"])
                if fingerprint in collapsed:
                    fix = what_to_do(item, rule, component)["patch_to"]
                    best = newest_fix.get(fingerprint)
                    if fix and (best is None or generic_key(fix) > generic_key(best)):
                        newest_fix[fingerprint] = fix
    for fingerprint, fix in newest_fix.items():
        match = found[fingerprint]
        match["summary"] = f"{match['affected']['name']} security releases are out; this tag isn't pinned."
        match["what_to_do"] = {**match["what_to_do"], "patch_to": fix,
                               "plain": (f"{match['affected']['name']} on {match['affected']['app']} runs an unpinned "
                                         f"image tag, so its exact version isn't known. Pin it to {fix} or later "
                                         "and redeploy.")}
    return list(found.values())


def relevant(item: dict, names: set[tuple[str, str]]) -> bool:
    return any((rule.get("ecosystem"), rule.get("name")) in names for rule in item.get("affected") or [])


def inventory_names(inventory: list[dict]) -> set[tuple[str, str]]:
    out = set()
    for c in inventory:
        if c["kind"] in MATCHABLE_KINDS:
            eco = c["ecosystem"] if c["kind"] == "package" else "generic"
            out.add((eco, normalise_name(eco, c["name"])))
    return out


# ---------- fetching (allow-listed hosts only) ----------

class Feeds:
    def __init__(self, http, pause: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.http, self.pause = http, pause

    async def request(self, method: str, url: str, *, params=None, json=None, headers=None):
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password
                or method not in {"GET", "POST"} or (method == "POST" and url != OSV_BATCH_URL)):
            raise PermissionError("Autumbra Watch only reads its named public advisory sources.")
        merged = {"Accept": "application/json", "User-Agent": "tervory-ghost-advisory-watcher"}
        merged.update(headers or {})
        try:
            response = await self.http.request(method, url, params=params, json=json, headers=merged,
                                               follow_redirects=False, timeout=60)
        except Exception:
            raise RuntimeError("The advisory source did not answer.") from None
        if response.status_code == 304:
            return None, response
        if response.status_code != 200 or len(response.content) > MAX_BYTES:
            raise RuntimeError("The advisory source did not answer.")
        try:
            return response.json(), response
        except ValueError:
            raise RuntimeError("The advisory source sent something unreadable.") from None

    async def get(self, url: str, **kwargs):
        return (await self.request("GET", url, **kwargs))[0]


@dataclass
class Cycle:
    started: datetime
    sources: dict[str, str] = field(default_factory=dict)
    new: list[dict] = field(default_factory=list)
    resolved: list[dict] = field(default_factory=list)


class Watcher:
    """One pull cycle: fetch each source, keep what touches the inventory, match, receipt."""

    def __init__(self, store, feeds: Feeds, receipts, tenant: str = PLATFORM_TENANT,
                 clock: Callable[[], datetime] = now_utc):
        self.store, self.feeds, self.receipts, self.tenant, self.clock = store, feeds, receipts, tenant, clock

    # Each puller returns advisories; failures are counted, never echoed (bodies may hold anything).
    async def pull_kev(self, cycle: Cycle) -> list[dict]:
        etag = self.store.get_state(self.tenant, "kev_etag")
        data, response = await self.feeds.request("GET", KEV_URL, headers={"If-None-Match": etag} if etag else None)
        if data is not None:
            entries = parse_kev(data)
            self.store.replace_kev(self.tenant, {cve: e["date_added"] for cve, e in entries.items()})
            self.store.set_state(self.tenant, "kev_products", {cve: e["product"] for cve, e in entries.items() if e["product"]})
            if response.headers.get("etag"):
                self.store.set_state(self.tenant, "kev_etag", response.headers["etag"])
        return []

    async def pull_osv(self, cycle: Cycle) -> list[dict]:
        packages = [c for c in self.store.inventory(self.tenant)
                    if c["kind"] == "package" and c["ecosystem"] in {"pypi", "npm"}]
        unique = sorted({(c["ecosystem"], c["name"], c["version"]) for c in packages})
        known = self.store.advisory_ids(self.tenant)
        wanted: dict[str, str] = {}
        for start in range(0, len(unique), 1000):
            chunk = unique[start:start + 1000]
            body = {"queries": [{"package": {"ecosystem": "PyPI" if e == "pypi" else "npm", "name": n}, "version": v}
                                for e, n, v in chunk]}
            data, _ = await self.feeds.request("POST", OSV_BATCH_URL, json=body)
            for result in (data or {}).get("results") or []:
                for vuln in (result or {}).get("vulns") or []:
                    if isinstance(vuln.get("id"), str) and known.get(vuln["id"]) != vuln.get("modified"):
                        wanted[vuln["id"]] = vuln.get("modified", "")
        out = []
        for vid in sorted(wanted)[:150]:
            if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", vid):
                continue
            item = parse_osv(await self.feeds.get(OSV_VULN_URL + vid))
            if item:
                out.append(item)
        return out

    async def pull_github(self, cycle: Cycle) -> list[dict]:
        since = self.store.get_state(self.tenant, "ghsa_cursor") or iso(cycle.started - timedelta(days=7))
        data = await self.feeds.get(GHSA_URL, params={"sort": "updated", "direction": "desc", "per_page": 100,
                                                      "updated": f">={since[:10]}"})
        out = [a for a in (parse_ghsa(i) for i in data or [] if isinstance(i, dict)) if a]
        self.store.set_state(self.tenant, "ghsa_cursor", iso(cycle.started - timedelta(hours=1)))
        return out

    async def pull_nvd(self, cycle: Cycle) -> list[dict]:
        start = parse_time(self.store.get_state(self.tenant, "nvd_cursor")) or cycle.started - timedelta(days=1)
        start = max(start, cycle.started - timedelta(days=119))
        stamp = lambda m: m.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # noqa: E731
        out, index = [], 0
        for page in range(3):
            if page:
                await self.feeds.pause(NVD_PAUSE_SECONDS)
            data = await self.feeds.get(NVD_URL, params={"lastModStartDate": stamp(start), "lastModEndDate": stamp(cycle.started),
                                                         "resultsPerPage": 2000, "startIndex": index})
            rows = (data or {}).get("vulnerabilities") or []
            out.extend(a for a in (parse_nvd(r) for r in rows) if a)
            index += len(rows)
            if not rows or index >= int((data or {}).get("totalResults") or 0):
                break
        # KEV entries for products we run, looked up by id (bounded, rate limit respected).
        known = self.store.advisory_ids(self.tenant)
        products = self.store.get_state(self.tenant, "kev_products", {})
        names = {name for _eco, name in inventory_names(self.store.inventory(self.tenant))}
        for cve in sorted(c for c, p in products.items() if p in names and c not in known)[:4]:
            await self.feeds.pause(NVD_PAUSE_SECONDS)
            data = await self.feeds.get(NVD_URL, params={"cveId": cve})
            out.extend(a for a in (parse_nvd(r) for r in (data or {}).get("vulnerabilities") or []) if a)
        self.store.set_state(self.tenant, "nvd_cursor", iso(cycle.started - timedelta(minutes=5)))
        return out

    async def pull_vendors(self, cycle: Cycle) -> list[dict]:
        out: list[dict] = []
        failures = 0
        for url, params, parser in ((PHP_RELEASES_URL, {"json": "", "version": "8", "max": "40"}, parse_php_releases),
                                    (NODE_INDEX_URL, None, lambda d: parse_node_index(d)[:20]),
                                    (COOLIFY_RELEASES_URL, {"per_page": 30}, parse_coolify_releases),
                                    (LITELLM_ADVISORIES_URL, {"state": "published", "per_page": 50},
                                     lambda d: [a for a in (parse_ghsa(i, "vendor:litellm") for i in d or [] if isinstance(i, dict)) if a]),
                                    (REDIS_RELEASES_URL, {"per_page": 30}, parse_redis_releases),
                                    (REDIS_ADVISORIES_URL, {"state": "published", "per_page": 50},
                                     lambda d: [a for a in (parse_ghsa(i, "vendor:redis", product="redis")
                                                            for i in d or [] if isinstance(i, dict)) if a])):
            try:
                out.extend(parser(await self.feeds.get(url, params=params)))
            except Exception:
                failures += 1
        if failures == 6:
            raise RuntimeError("No vendor note answered.")
        cycle.sources["vendor_notes_failed"] = str(failures)
        return out

    async def cycle(self) -> dict:
        cycle = Cycle(started=self.clock())
        inventory = self.store.inventory(self.tenant)
        names = inventory_names(inventory)
        pulled: list[dict] = []
        for name, puller in (("cisa_kev", self.pull_kev), ("osv", self.pull_osv), ("github", self.pull_github),
                             ("nvd", self.pull_nvd), ("vendor_notes", self.pull_vendors)):
            try:
                pulled.extend(await puller(cycle))
                cycle.sources[name] = "ok"
            except Exception:
                cycle.sources[name] = "unavailable"
        fetched = iso(cycle.started)
        for item in pulled:
            if relevant(item, names):
                self.store.save_advisory(self.tenant, item, fetched)
        return await self.match(cycle)

    async def match(self, cycle: Cycle) -> dict:
        inventory = self.store.inventory(self.tenant)
        current = evaluate(self.store.advisories(self.tenant), inventory, self.store.kev(self.tenant))
        flagged = self.clock()
        live = set()
        for found in current:
            live.add(found["fingerprint"])
            published = parse_time(found["advisory_published"])
            item, created = self.store.open_match(self.tenant, found["fingerprint"], {
                **found, "flagged_at": iso(flagged),
                "receipt": {"advisory_published": found["advisory_published"], "flagged_at": iso(flagged),
                            "time_to_know_seconds": max(0, int((flagged - published).total_seconds())) if published else None,
                            "ledger_id": None, "status": "pending"}})
            if not created:
                item["severity"] = found["severity"]
                item["source_ids"] = found["source_ids"]
                self.store.save_match(self.tenant, item)
            else:
                cycle.new.append(item)
            await self.receipt(item, "flagged")
        for item in self.store.matches(self.tenant, "open"):
            if item["fingerprint"] in live:
                continue
            still_there = any(c["app"] == item["affected"]["app"] and c["name"] == item["affected"]["name"]
                              and c["kind"] == item["affected"]["kind"] for c in inventory)
            item.update(state="resolved", resolved_at=iso(flagged),
                        resolution="updated" if still_there else "component_gone")
            item["resolved_receipt"] = {"resolved_at": iso(flagged), "ledger_id": None, "status": "pending"}
            self.store.save_match(self.tenant, item)
            cycle.resolved.append(item)
            await self.receipt(item, "resolved")
        summary = {"started_at": iso(cycle.started), "finished_at": iso(self.clock()), "sources": cycle.sources,
                   "new_matches": len(cycle.new), "resolved": len(cycle.resolved),
                   "open": len(self.store.matches(self.tenant, "open"))}
        self.store.set_state(self.tenant, "last_cycle", summary)
        return summary

    async def receipt(self, item: dict, step: str) -> None:
        """Tank receipt per match step. A failed write stays pending and is retried next cycle."""
        slot = "receipt" if step == "flagged" else "resolved_receipt"
        if item.get(slot, {}).get("status") == "recorded":
            return
        ledger_id = str(uuid5(UUID(item["id"]), "advisory_" + step))
        try:
            await self.receipts.record(ledger_id, "advisory_" + step, "delivered", task_type="ghost.advisory")
            item[slot].update(ledger_id=ledger_id, status="recorded")
        except Exception:
            item[slot].update(status="pending")
        self.store.save_match(self.tenant, item)


async def watch(app, interval: int = INTERVAL_SECONDS) -> None:
    while True:
        try:
            async with app.state.advisory_lock:
                await app.state.watcher.cycle()
                if getattr(app.state, "shields", None) is not None:
                    await app.state.shields.after_cycle()
        except Exception:
            pass  # Status is in last_cycle; exception text may carry response content.
        await asyncio.sleep(interval)
