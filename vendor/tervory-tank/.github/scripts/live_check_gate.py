"""Run the signed-out live check only while Coolify is quiet, and re-check after an overlap.

31 Oct: #471's live check probed while a separate Coolify deploy of main (started through
the API, not by this workflow) was replacing containers, so every Workspace host returned
HTTP 503. A single probe after one health read cannot tell that apart from a broken site.

This gate:
1. Waits until main, agent-rig and every Boston room report running:healthy AND none of
   them has a Coolify deployment queued or in progress, on two polls in a row with the same
   latest deployment (the stack is settled, not just momentarily green).
2. Runs ops/live_check.mjs.
3. If hosts failed, reads Coolify again. When a deployment started (or an app left healthy)
   during the probe, the result is about a deploy in flight, not the site: wait to settle
   and probe again (bounded). When Coolify stayed quiet, the failure is real and is reported.

The token comes from COOLIFY_TOKEN through deploy_changed.api and is never printed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable

ACTIVE = {"queued", "in_progress"}
REQUIRED = ("main", "agent-rig")
POLL_S = 15
STABLE_POLLS = 2
ATTEMPTS = 3
TOTAL_S = 30 * 60

Api = Callable[[str, str], "tuple[int, object]"]


def _status(value: object) -> str:
    from watchdog import norm_status
    return norm_status(value)


def watched(apps: list, main_uuid: str) -> dict[str, str]:
    """uuid -> label for the apps behind the Boston hosts the live check opens."""
    out: dict[str, str] = {}
    for app in apps:
        if not isinstance(app, dict) or not app.get("uuid"):
            continue
        name = str(app.get("name") or "")
        if app["uuid"] == main_uuid:
            out[app["uuid"]] = "main"
        elif name.startswith("agent-") and not name.endswith("-za") and name != "agent-template":
            out[app["uuid"]] = name
    return out


def snapshot(api: Api, main_uuid: str) -> tuple[list[str], dict[str, str]]:
    """Return (reasons not settled, app label -> latest deployment id)."""
    code, apps = api("GET", "/applications")
    if code != 200 or not isinstance(apps, list):
        return [f"Coolify applications unreadable (HTTP {code})"], {}
    targets = watched(apps, main_uuid)
    pending = [f"{label} missing" for label in REQUIRED if label not in targets.values()]
    status = {a["uuid"]: _status(a.get("status")) for a in apps if isinstance(a, dict) and a.get("uuid") in targets}
    marks: dict[str, str] = {}
    for uuid, label in sorted(targets.items(), key=lambda item: item[1]):
        if status.get(uuid) != "running:healthy":
            pending.append(f"{label} {status.get(uuid) or 'unknown'}")
        code, body = api("GET", f"/deployments/applications/{uuid}?take=1")
        rows = body.get("deployments", body) if isinstance(body, dict) else body
        if code != 200 or not isinstance(rows, list):
            pending.append(f"{label} deployments unreadable")
            continue
        latest = rows[0] if rows and isinstance(rows[0], dict) else {}
        if str(latest.get("status") or "").lower() in ACTIVE:
            pending.append(f"{label} deploying")
        marks[label] = str(latest.get("deployment_uuid") or latest.get("id") or "")
    return pending, marks


def settle(api: Api, main_uuid: str, deadline: float, *, clock=time.monotonic, sleep=time.sleep,
           poll_s: float = POLL_S, stable_polls: int = STABLE_POLLS) -> dict[str, str]:
    """Block until Coolify is healthy and quiet on stable_polls consecutive reads."""
    streak, last = 0, None
    while True:
        pending, marks = snapshot(api, main_uuid)
        if pending:
            streak, last = 0, None
        else:
            streak = streak + 1 if marks == last else 1
            last = marks
            if streak >= stable_polls:
                print("Coolify settled: main, agent-rig and Boston rooms healthy, no deploy in progress", flush=True)
                return marks
        if clock() >= deadline:
            print("::error title=Coolify not settled::" + (", ".join(pending) or "deployments still changing"))
            raise SystemExit(1)
        print("Waiting for Coolify: " + (", ".join(pending) or "confirming quiet"), flush=True)
        sleep(poll_s)


def changed(before: dict[str, str], pending: list[str], after: dict[str, str]) -> list[str]:
    """What moved in Coolify between the settled read before the probe and the read after it."""
    moved = [label + " redeployed" for label in sorted(set(before) | set(after))
             if before.get(label) != after.get(label)]
    return moved + pending


def run(api: Api, main_uuid: str, probe: Callable[[], int], *, clock=time.monotonic, sleep=time.sleep,
        attempts: int = ATTEMPTS, total_s: float = TOTAL_S, poll_s: float = POLL_S) -> int:
    deadline = clock() + total_s
    for attempt in range(1, attempts + 1):
        before = settle(api, main_uuid, deadline, clock=clock, sleep=sleep, poll_s=poll_s)
        code = probe()
        if code == 0:
            return 0
        pending, after = snapshot(api, main_uuid)
        overlap = changed(before, pending, after)
        if not overlap:
            print("::error title=Live check failed::hosts failed while Coolify was healthy and no deploy ran")
            return code
        print(f"::warning title=Deploy overlapped live check::attempt {attempt}: " + ", ".join(overlap)
              + "; waiting for Coolify to settle and checking again", flush=True)
    print(f"::error title=Live check failed::a Coolify deploy overlapped all {attempts} attempts")
    return 1


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from deploy_changed import MAIN_APP, api

    command = ["node", "ops/live_check.mjs", *sys.argv[1:]]
    return run(api, MAIN_APP, lambda: subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    sys.exit(main())
