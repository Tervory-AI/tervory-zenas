"""Isolation probe as seen from inside a per-client Enforce container.

Run on disposable GitHub CI containers only (automated checks):
  python -m ghost.enforce_isolation_probe

Automated runs use disposable GitHub CI containers only. The founder's read-only
live command after deployment is in docs/ops/AUTUMBRA-ENFORCE-ISOLATION.md.
Builders never run this probe on live Boston.
"""
from __future__ import annotations

import json
import socket
from typing import Any


# Hosts an Enforce container must NOT resolve (DNS only — not TCP reachability).
# This list does not include tervory.com; public DNS may resolve it without network path.
FORBIDDEN_RESOLVE = (
    "tank",
    "postgres",
    "hermes",
    "workspace",
    "redis",
    "qdrant",
    "litellm",
    "tiger",
    "base",
    "market_lab",
    "agent-other",
    "autumbra-enforce-other",
)


def try_resolve(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def try_connect(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def evaluate_probe(results: dict[str, Any]) -> dict[str, Any]:
    """Score a probe result dict. Used by live run and by fixture tests."""
    fails: list[str] = []
    passes: list[str] = []
    for host in FORBIDDEN_RESOLVE:
        resolved = bool(results.get("resolve", {}).get(host))
        if resolved:
            fails.append(f"{host} must not resolve from Enforce")
        else:
            passes.append(f"{host} does not resolve")
    # Public internet should be dark on internal:true networks (no edge egress
    # until a real connected hand needs a controlled path — dogfood uses dry-run).
    if results.get("public_443"):
        fails.append("public 1.1.1.1:443 must be unreachable on internal Enforce net")
    else:
        passes.append("public internet unreachable")
    if results.get("shares_network_with_other_client"):
        fails.append("must not share network with another client's Enforce")
    else:
        passes.append("no shared network with other client")
    if results.get("has_tank_key"):
        fails.append("Enforce must not hold TANK_KEY")
    else:
        passes.append("no TANK_KEY")
    if results.get("role") != "enforce":
        fails.append("AUTUMBRA_ROLE must be enforce")
    else:
        passes.append("role=enforce")
    return {
        "ok": not fails,
        "passes": passes,
        "fails": fails,
        "product": "Autumbra",
        "kind": "isolation_probe",
        "viewpoint": "inside_fixture_enforce",
    }


def collect_live_probe(*, other_client_network_member: bool = False) -> dict[str, Any]:
    import os

    resolve = {host: try_resolve(host) for host in FORBIDDEN_RESOLVE}
    public_443 = try_connect("1.1.1.1", 443, timeout=2.0)
    return {
        "resolve": resolve,
        "public_443": public_443,
        "shares_network_with_other_client": other_client_network_member,
        "has_tank_key": bool(os.environ.get("TANK_KEY")),
        "role": os.environ.get("AUTUMBRA_ROLE", ""),
    }


def main() -> int:
    probe = collect_live_probe()
    report = evaluate_probe(probe)
    print(json.dumps({"probe": probe, "report": report}, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
