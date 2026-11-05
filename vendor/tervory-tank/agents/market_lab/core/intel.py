"""Public, commercially licensed security intel. No model, key, or room-to-room call."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import UUID, uuid4

from feeds.collect import EvidenceClient
from feeds.keyless import MAX_BYTES
from feeds.licence import require_commercial_intel
from feeds.licences import CISA_KEV, INTEL_CHECKED, OSV_GHSA
from feeds.models import SourceSpec

SCHEMA = "market_lab.ioc.v1"
PLATFORM = "00000000-0000-0000-0000-000000000001"
KEV = "https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json"
OSV = "https://api.osv.dev/v1/querybatch"
REPO = "https://api.github.com/repos/Tervory-AI/tervory-tank/git/trees/main?recursive=1"
SOURCES = (
    SourceSpec("cisa_kev", "CISA Known Exploited Vulnerabilities", "copy", KEV, CISA_KEV,
               INTEL_CHECKED, "daily", True, True, "intel"),
    SourceSpec("osv_ghsa", "GitHub Advisory Database", "copy", OSV, OSV_GHSA,
               INTEL_CHECKED, "daily", True, True, "intel"),
)


def checked_json(raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise ValueError("feed limit")
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("feed shape")
    return body


def kev_indicators(body: dict) -> list[dict]:
    rows = body.get("vulnerabilities")
    if not isinstance(rows, list) or body.get("count") != len(rows):
        raise ValueError("incomplete KEV catalog")
    indicators = []
    for row in rows:
        ident = row.get("cveID", "")
        if not re.fullmatch(r"CVE-\d{4}-\d{4,}", ident):
            raise ValueError("invalid KEV identifier")
        indicators.append({"kind": "cve", "value": ident, "source_ids": ["cisa_kev"],
                           "component": str(row.get("product") or ""), "rating": "",
                           "url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog?field_cve=" + ident})
    return indicators


def locked_packages(name: str, raw: bytes) -> list[dict]:
    """Only exact pins; do not infer an installed version from a range."""
    from agents.radix.scanlib.dependencies import _packages_from_lock, _packages_from_requirements, normalize
    if len(raw) > MAX_BYTES:
        raise ValueError("feed limit")
    ecosystem = "npm" if name.endswith("package-lock.json") else "PyPI"
    parser = _packages_from_lock if ecosystem == "npm" else _packages_from_requirements
    rows = {(normalize(ecosystem, package), version) for package, version, _line in parser(raw.decode())
            if re.fullmatch(r"[0-9][A-Za-z0-9_.+!-]*", version)}
    return [{"ecosystem": ecosystem, "name": package, "version": version} for package, version in sorted(rows)]


def dependency_inventory(http: EvidenceClient) -> tuple[list[dict], str]:
    # The room image does not contain other products' lockfiles. Read our public
    # repository at one immutable tree SHA, without a GitHub key or a local clone.
    tree = checked_json(http.get(REPO))
    revision = tree.get("sha", "")
    if tree.get("truncated") or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("incomplete repository tree")
    paths = [r["path"] for r in tree["tree"] if r["type"] == "blob"
             and not r["path"].startswith(("tests/", "docs/")) and "fixture" not in r["path"] and
             (r["path"].endswith("package-lock.json") or re.search(r"requirements[^/]*\.(?:txt|lock)$", r["path"]))]
    if not paths or len(paths) > 200:
        raise ValueError("lockfile inventory limit")
    packages = {}
    for path in sorted(paths):
        raw = http.get(f"https://raw.githubusercontent.com/Tervory-AI/tervory-tank/{revision}/{quote(path, safe='/')}")
        for row in locked_packages(path, raw):
            key = (row["ecosystem"], row["name"], row["version"])
            packages.setdefault(key, row)
    if not packages or len(packages) > 10000:
        raise ValueError("dependency inventory limit")
    return list(packages.values()), revision


def osv_indicators(http: EvidenceClient, packages: list[dict]) -> list[dict]:
    result, details = {}, {}
    for offset in range(0, len(packages), 50):
        chunk = packages[offset:offset + 50]
        queries = [{"package": {"ecosystem": p["ecosystem"], "name": p["name"]}, "version": p["version"]} for p in chunk]
        pending = list(enumerate(queries))
        for _page in range(20):
            if not pending:
                break
            body = checked_json(http.post_json(OSV, {"queries": [q for _, q in pending]}))
            answers = body.get("results")
            if not isinstance(answers, list) or len(answers) != len(pending):
                raise ValueError("incomplete OSV response")
            following = []
            for (index, query), answer in zip(pending, answers):
                for vuln in answer.get("vulns") or []:
                    ident = vuln.get("id", "")
                    # OSV's aggregate is not under one licence. Unknown upstreams
                    # are never downloaded or redistributed as if they were CC BY.
                    if not re.fullmatch(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", ident):
                        continue
                    key = (ident, chunk[index]["ecosystem"], chunk[index]["name"], chunk[index]["version"])
                    if key in result:
                        continue
                    if ident not in details:
                        details[ident] = checked_json(http.get("https://api.osv.dev/v1/vulns/" + ident))
                    detail = details[ident]
                    if detail.get("id") != ident or detail.get("withdrawn"):
                        continue
                    severity = str((detail.get("database_specific") or {}).get("severity") or "").lower()
                    result[key] = {"kind": "advisory", "value": ident, "source_ids": ["osv_ghsa"],
                                   "component": chunk[index]["name"],
                                   "rating": severity if severity in {"low", "moderate", "high", "critical"} else "",
                                   "url": "https://github.com/advisories/" + ident}
                token = answer.get("next_page_token")
                if token:
                    following.append((index, {**query, "page_token": token}))
            pending = following
        if pending:
            raise ValueError("OSV pagination limit")
    return list(result.values())


def produce(http: EvidenceClient | None = None) -> dict:
    for source in SOURCES:
        require_commercial_intel(source)
    http = http or EvidenceClient(public_feeds=True, allowed_domains={"github.com", "githubusercontent.com", "osv.dev"})
    # Ensure both GET and POST share the other public feeds' byte bound.
    http.client.max_bytes = MAX_BYTES
    packages, revision = dependency_inventory(http)
    indicators = kev_indicators(checked_json(http.get(KEV))) + osv_indicators(http, packages)
    bundle = {"schema_version": SCHEMA, "bundle_id": str(uuid4()),
              "published_at": datetime.now(timezone.utc).isoformat(), "paid": False,
              "sources": [{"id": s.id, "name": s.name, "credit": s.name, "url": s.url,
                           "licence": s.licence, "terms_checked_at": s.terms_checked_at} for s in SOURCES],
              "indicators": indicators, "lock_commit": revision, "pinned_packages": len(packages)}
    if len(json.dumps(bundle).encode()) > MAX_BYTES:
        raise ValueError("bundle limit")
    return bundle


async def refresh(ledger) -> dict:
    bundle = await asyncio.to_thread(produce)
    await ledger.save_report({"id": bundle["bundle_id"], "tenant_id": PLATFORM,
                              "made_at": datetime.now(timezone.utc), "question": SCHEMA,
                              "answer": json.dumps(bundle, separators=(",", ":")),
                              "citations": bundle["sources"]})
    return {"bundle_id": bundle["bundle_id"], "count": len(bundle["indicators"])}


async def latest(ledger) -> dict | None:
    raw = await ledger.conn.fetchval("SELECT answer FROM agent_market_lab.reports WHERE tenant_id=$1 AND question=$2 AND made_at > now() - interval '2 days' ORDER BY made_at DESC,id DESC LIMIT 1", UUID(PLATFORM), SCHEMA)
    return json.loads(raw) if raw else None
