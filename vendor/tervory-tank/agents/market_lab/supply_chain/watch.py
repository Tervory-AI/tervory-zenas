"""Importable Autumbra feed: committed locks -> verified advisory matches and fixes.

No imports start network calls, mutate files, or open PRs. A failed source or an
unknown lock format makes the feed incomplete; it is never reported as clean.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import re
from urllib.parse import quote, urlencode

from agents.ghost.versions import in_constraints, in_osv_range, normalise_name, ordering
from .http import Client, SourceError
from .locks import Dependency, Inventory
from .sources import SOURCES, SOURCE_URLS

ID = re.compile(r"^(?:CVE-\d{4}-\d+|GHSA-[a-z0-9-]+|[A-Z][A-Z0-9-]*-\d[\w.-]*)$")


def pages(client: Client, url: str) -> list[dict]:
    rows = []
    for page in range(1, 101):
        result = client.json(url + ("&" if "?" in url else "?") + urlencode({"per_page": 100, "page": page}))
        if not isinstance(result, list):
            raise SourceError("GitHub advisory response is invalid.")
        rows.extend(result)
        if len(result) < 100:
            return rows
    raise SourceError("GitHub pagination limit reached; coverage incomplete.")


def _same_package(row: dict, dep: Dependency) -> bool:
    package = row.get("package", {})
    eco = package.get("ecosystem", "")
    return eco.lower() in {dep.ecosystem.lower(), "pip" if dep.ecosystem == "PyPI" else dep.ecosystem.lower()} and (
        normalise_name(dep.ecosystem, package.get("name", "")) == normalise_name(dep.ecosystem, dep.name))


def osv_affected(vuln: dict, dep: Dependency, version: str) -> bool:
    for affected in vuln.get("affected", []):
        if not _same_package(affected, dep):
            continue
        if version in affected.get("versions", []):
            return True
        for span in affected.get("ranges", []):
            if span.get("type") == "GIT":
                raise SourceError("Registry advisory contains an unevaluated Git range.")
            match = in_osv_range(version, span.get("events", []), ordering(dep.ecosystem, span.get("type", "ECOSYSTEM")))
            if match is None:
                raise SourceError("OSV range cannot be evaluated.")
            if match:
                return True
    return False


def github_affected(vuln: dict, dep: Dependency, version: str) -> bool:
    for affected in vuln.get("vulnerabilities", []):
        if not _same_package(affected, dep):
            continue
        for span in affected.get("vulnerable_version_range", "").split("||"):
            match = in_constraints(version, span.strip(), ordering(dep.ecosystem))
            if match is None:
                raise SourceError("GitHub advisory range cannot be evaluated.")
            if match:
                return True
    return False


def fixed_versions(vuln: dict, dep: Dependency, source: str) -> set[str]:
    fixes = set()
    for row in vuln.get("affected" if source == "osv" else "vulnerabilities", []):
        if not _same_package(row, dep):
            continue
        if source == "osv":
            fixes.update(event["fixed"] for span in row.get("ranges", []) for event in span.get("events", []) if "fixed" in event)
        elif row.get("first_patched_version"):
            fixes.add(row["first_patched_version"])
    return fixes


def _ids(vuln: dict) -> list[str]:
    ids = [vuln.get("id"), vuln.get("ghsa_id"), vuln.get("cve_id"), *vuln.get("aliases", [])]
    ids.extend(row.get("value") for row in vuln.get("identifiers", []))
    return sorted({value for value in ids if isinstance(value, str) and ID.fullmatch(value)})


def _query(client: Client, dep: Dependency) -> dict:
    if dep.ecosystem != "GIT":
        return {"package": {"name": dep.name, "ecosystem": dep.ecosystem}, "version": dep.version}
    commit = dep.revision
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        data = client.json(SOURCE_URLS["github"] + f"repos/{dep.repository}/commits/{quote(commit, safe='')}")
        commit = data.get("sha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise SourceError("Source revision could not be resolved to a commit.")
    return {"commit": commit}


def scan(items: Inventory, client: Client) -> dict:
    """Return market_lab.supply_chain.v1, suitable for Autumbra's backend intake."""
    result = {
        "schema_version": "market_lab.supply_chain.v1", "producer": "Market Lab", "paid": False,
        "observed_at": datetime.now(timezone.utc).isoformat(), "sources": [asdict(source) for source in SOURCES],
        "coverage": items.coverage, "dependency_count": len(items.dependencies),
        "errors": list(items.errors), "findings": [], "fixes": [], "complete": False,
    }
    deps = {}
    locations: dict[tuple, list[str]] = {}
    for dep in items.dependencies:
        key = (dep.ecosystem, dep.name, dep.version, dep.repository, dep.revision)
        deps[key] = dep
        locations.setdefault(key, []).append(dep.lock)
    try:
        kev_data = client.json(SOURCE_URLS["cisa-kev"])
        if not isinstance(kev_data.get("vulnerabilities"), list) or not kev_data.get("catalogVersion"):
            raise SourceError("CISA KEV catalog is invalid.")
        kev = {entry["cveID"] for entry in kev_data["vulnerabilities"]}
        result["kev_catalog_version"] = kev_data["catalogVersion"]
        keys = list(deps)
        queried: dict[tuple, list[str]] = {}
        for start in range(0, len(keys), 100):
            batch_keys = keys[start:start + 100]
            payload = {"queries": [_query(client, deps[key]) for key in batch_keys]}
            batch = client.json(SOURCE_URLS["osv"] + "querybatch", payload, method="POST")
            if len(batch.get("results", [])) != len(batch_keys):
                raise SourceError("OSV batch is incomplete.")
            for key, row in zip(batch_keys, batch["results"]):
                if row.get("next_page_token"):
                    raise SourceError("OSV batch is truncated; coverage incomplete.")
                queried[key] = [entry["id"] for entry in row.get("vulns", [])]
        ids = sorted({vid for values in queried.values() for vid in values})
        with ThreadPoolExecutor(max_workers=4) as pool:
            documents = list(pool.map(lambda vid: client.json(SOURCE_URLS["osv"] + "vulns/" + quote(vid, safe="")), ids))
        osv = dict(zip(ids, documents))
        # Query GitHub independently; an advisory missing from OSV still blocks a clean result.
        def github(key):
            dep = deps[key]
            if dep.ecosystem == "GIT":
                if not dep.repository:
                    return []  # Non-GitHub source: OSV commit lookup; GHSA aliases queried below.
                return pages(client, SOURCE_URLS["github"] + f"repos/{dep.repository}/security-advisories?state=published")
            params = {"ecosystem": "pip" if dep.ecosystem == "PyPI" else "npm", "affects": f"{dep.name}@{dep.version}"}
            return pages(client, SOURCE_URLS["github"] + "advisories?" + urlencode(params))
        with ThreadPoolExecutor(max_workers=4) as pool:
            gh_docs = dict(zip(keys, pool.map(github, keys)))
        for key in keys:
            dep = deps[key]
            evidence = [("osv", osv[vid]) for vid in queried[key] if not osv[vid].get("withdrawn")]
            for doc in gh_docs[key]:
                if doc.get("withdrawn_at"):
                    continue
                if dep.ecosystem == "GIT":
                    aliases = {alias for _, item in evidence for alias in _ids(item)}
                    if not set(_ids(doc)) & aliases:
                        raise SourceError("GitHub source advisory needs commit-range verification; coverage incomplete.")
                elif not github_affected(doc, dep, dep.version):
                    continue
                evidence.append(("github", doc))
            # Also look up GHSA aliases for GitLab/source-commit OSV matches.
            if dep.ecosystem == "GIT":
                known = {identifier for source, item in evidence if source == "github" for identifier in _ids(item)}
                for identifier in sorted({identifier for _, item in evidence for identifier in _ids(item)} - known):
                    if identifier.startswith("GHSA-"):
                        doc = client.json(SOURCE_URLS["github"] + "advisories/" + identifier)
                        if not doc.get("withdrawn_at"):
                            evidence.append(("github", doc))
            if not evidence:
                continue
            aliases = sorted({identifier for _, item in evidence for identifier in _ids(item)})
            if not aliases:
                raise SourceError("Matched advisory has no recognized identifier.")
            sources = sorted({source for source, _ in evidence})
            exploited = sorted(set(aliases) & kev)
            if exploited:
                sources.append("cisa-kev")
            target = None
            if dep.ecosystem != "GIT":
                candidates = {fix for source, item in evidence for fix in fixed_versions(item, dep, source)}
                order = ordering(dep.ecosystem)
                candidates = sorted((version for version in candidates if order(version) is not None
                                     and order(dep.version) is not None and order(version) > order(dep.version)), key=order)
                for candidate in candidates:
                    if not any((osv_affected if source == "osv" else github_affected)(item, dep, candidate)
                               for source, item in evidence):
                        target = candidate
                        break
            finding = {"ecosystem": dep.ecosystem, "package": dep.name, "version": dep.version,
                       "locks": sorted(set(locations[key])), "advisories": aliases, "source_ids": sources,
                       "known_exploited": exploited, "fixed_version": target}
            result["findings"].append(finding)
            if target:
                result["fixes"].append(finding)
        result["complete"] = not result["errors"]
    except (SourceError, KeyError, TypeError, ValueError, AttributeError) as error:
        result["errors"].append(str(error) if isinstance(error, SourceError) else "Advisory response schema is invalid.")
    return result
