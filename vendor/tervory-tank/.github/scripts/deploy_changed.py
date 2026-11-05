"""Deploy only the Coolify apps whose code changed in a push to main.

Runs after CI passes on main. Reads the changed paths between BEFORE and AFTER,
maps them to Coolify app names, looks up each app's uuid by name through the
Coolify API, and queues a deploy. The main app goes first and must finish with
success. The South Africa stack (tervory-tank-za) is next and must finish
before any room is deployed, so a broken main or SA deploy never fans out.
The token comes from COOLIFY_TOKEN and is never printed.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import re
from uuid import UUID
import urllib.error
import urllib.request
import urllib.parse

from backup_runtime_scope import verify_main_scope

URL = os.environ["COOLIFY_URL"].rstrip("/")
TOKEN = os.environ["COOLIFY_TOKEN"]
BEFORE = os.environ.get("BEFORE", "")
AFTER = os.environ.get("AFTER", "HEAD")
MAIN_APP = "xnmc0jkz8j9i5jh1jwanbsfv"
SA_APP = "tervory-tank-za"
# Repo variable ZA_OFFLINE=true: the ZA stack is switched off on purpose (box being moved).
# Skip it and every -za room with a notice, and do not fail the run. Unset: today's behaviour.
ZA_OFFLINE = os.environ.get("ZA_OFFLINE", "").strip().lower() == "true"
# The commit at the tip of main when this run started (set by the workflow). Coolify always
# builds the tip of main, so a queued run whose app already finished a deploy of that commit
# would only restart it. Five merges in six minutes (31 Oct) made three queued runs redeploy
# 3f57590c on Boston and ZA again; each ZA restart cut the ZA room's case loads mid-run.
# Empty: unknown, every wanted app deploys as before.
MAIN_HEAD = os.environ.get("MAIN_HEAD", "").strip().lower()

# Paths that rebuild the main stack (Tank, Workspace, LiteLLM, Hermes, SearXNG, site, shared compose).
MAIN_PREFIXES = (
    "tank/", "workspace/", "litellm/", "hermes/", "db/", "searxng/", "tiger/", "site/", "tor/",
    "docker-compose.yml",
    # Backup workers live in the main and ZA stacks (r2-backup, r2-backup-za).
    "docker-compose.za.yml", "jobs/train/backup.py", "jobs/train/r2_archive.py",
    "jobs/train/Dockerfile.backup",
)
# Shared room code: a change here redeploys every room.
ALL_ROOMS_PREFIXES = ("agents/_chassis/", "agents/_template/")


def radix_blocks(name: str) -> bool:
    """Only an authenticated, tenant-scoped confirmed critical can hold this app."""
    key=os.environ.get('RADIX_DEPLOY_TANK_KEY','')
    tenant=os.environ.get('RADIX_DEPLOY_TENANT_ID','')
    def note(text):
        safe=text.replace('%','%25').replace('\r','%0D').replace('\n','%0A')
        print('::notice title=Radix deploy gate::'+safe)
        summary=os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            try:
                with open(summary,'a',encoding='utf-8') as stream:
                    stream.write('\n'+text.replace('\r',' ').replace('\n',' ').replace('`','').replace('<','&lt;')+'\n')
            except OSError:
                pass
    try:
        UUID(tenant)
        if not key:
            raise ValueError('not configured')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        query=urllib.parse.urlencode({'tenant_id':tenant,'app':name})
        request=urllib.request.Request('https://tank.tervory.com/v1/command/radix/deploy/gate?'+query,
            headers={'X-Tank-Key':key,'X-Tervory-Tenant':tenant,'Accept':'application/json'})
        with urllib.request.build_opener(NoRedirect).open(request,timeout=10) as response:
            payload=response.read(256001)
            if len(payload)>256000: raise ValueError('response limit')
            data=json.loads(payload)
        if not isinstance(data,dict) or data.get('app')!=name or type(data.get('blocked')) is not bool:
            raise ValueError('invalid decision')
        findings=data.get('findings')
        if not isinstance(findings,list): raise ValueError('invalid findings')
        if data['blocked']:
            if not findings: raise ValueError('unconfirmed block')
            for row in findings:
                UUID(row['id'])
                if row.get('severity')!='critical' or not re.fullmatch(r'[a-f0-9]{40}',row.get('commit','')):
                    raise ValueError('unconfirmed critical')
            for row in findings:
                note(f"{name} held: critical {row.get('tool','')}:{row.get('rule','')} "
                     f"finding {row['id']} at {row['commit']} {row.get('file','')}")
            return True
        return False
    except (OSError,ValueError,TypeError,KeyError,AttributeError,http.client.HTTPException):
        note(f'{name}: gate could not be checked; deploy continues')
        return False


def api(method: str, path: str) -> tuple[int, object]:
    if URL != "https://coolify.tervory.com":
        raise ValueError("COOLIFY_URL must match the fixed https Coolify base")
    req = urllib.request.Request(f"https://coolify.tervory.com/api/v1{path}", method=method,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode() or "null"
            return r.status, json.loads(body)
    except urllib.error.HTTPError as e:
        # Reading the error body can fail the same ways as any read; the status is enough.
        try:
            text = e.read().decode(errors="replace")[:300]
        except (OSError, ValueError, http.client.HTTPException):
            text = ""
        return e.code, text
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        # A dropped connection, a socket timeout, a body cut off mid-read (IncompleteRead),
        # a garbled status line (BadStatusLine), or a 200 whose body is not JSON (a proxy's
        # HTML error page; json.JSONDecodeError is a ValueError) is one failed call, not
        # the end of the run: callers treat code 0 as "no answer" and go on.
        return 0, type(e).__name__


def changed_paths() -> list[str]:
    if not BEFORE or set(BEFORE) == {"0"}:
        rng = [f"{AFTER}~1", AFTER]
    else:
        rng = [BEFORE, AFTER]
    out = subprocess.run(["git", "diff", "--name-only", *rng], capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p]


def wanted(paths: list[str]) -> tuple[bool, set[str], bool]:
    main = any(p.startswith(MAIN_PREFIXES) for p in paths)
    rooms: set[str] = set()
    every_room = any(p.startswith(ALL_ROOMS_PREFIXES) for p in paths)
    for p in paths:
        parts = p.split("/")
        if len(parts) > 2 and parts[0] == "agents" and not parts[1].startswith("_"):
            rooms.add("agent-" + parts[1].replace("_", "-"))
    return main, rooms, every_room


# How long to wait for each Coolify deployment. ZA builds on a slower host: when
# tank/requirements.txt changes, Docker rebuilds the pip layer and downloads the
# models again, and ZA took 19 and 22 minutes (31 Oct, deployments 876 and 882)
# against the old 15-minute wait, so the run failed while ZA was still building.
MAIN_TIMEOUT_S = 1800
ZA_TIMEOUT_S = 2700
ROOM_TIMEOUT_S = 900
# A ZA room builds on the same slow ZA host: agent-zenas-za took 18m00s, 14m35s and
# 19m49s (deployments 767, 874 and 694), past the 15-minute room wait.
ZA_ROOM_TIMEOUT_S = 1800
POLL_S = 15
# The deploy job's own budget (deploy-on-merge.yml sets it below the job's
# timeout-minutes). A room whose full wait no longer fits is not started and is
# reported as an error. No later run retries it (last_failed() only retries an app
# whose newest Coolify deployment failed, and this room was never deployed): deploy
# it by hand (coolify-ops) or with the next change to its code.
DEPLOY_BUDGET_S = int(os.environ.get("DEPLOY_BUDGET_S") or 0)
_run_started = time.time()


def room_timeout(name: str) -> int:
    return ZA_ROOM_TIMEOUT_S if name.endswith("-za") else ROOM_TIMEOUT_S
# deploy() records the deployment Coolify queued, so wait_for follows that exact
# deployment rather than whichever deployment of the app is newest.
_queued: dict[str, str] = {}


def _rows(body: object) -> list[dict]:
    rows = body.get("deployments", body) if isinstance(body, dict) else body
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def wait_for(uuid: str, name: str, timeout_s: int = ROOM_TIMEOUT_S) -> bool:
    """Wait until the queued deployment ends, then report how it ended.

    A deployment still queued or building when the wait runs out is reported as
    still in progress, with its id, not as a failed deployment.
    """
    started = time.time()
    deadline = started + timeout_s
    deployment = _queued.get(uuid, "")
    status, last_note = "", 0.0
    time.sleep(20)
    while time.time() < deadline:
        if deployment:
            code, body = api("GET", f"/deployments/{deployment}")
            if code == 404:
                # "Deployment already queued for this commit": Coolify answers with an id it
                # minted but never saved (DeployController.php:517, :529-530), so the id
                # 404s. The deployment really running is the app's newest one: follow that.
                print(f"::notice title=queued id unknown::{name}: deployment {deployment} not found; "
                      "following the app's newest deployment")
                deployment = ""
                continue
            row = body if code == 200 and isinstance(body, dict) else None
        else:
            code, body = api("GET", f"/deployments/applications/{uuid}?take=1")
            rows = _rows(body) if code == 200 else []
            row = rows[0] if rows else None
        if row is not None:
            status = (row.get("status") or "").lower()
            if status in ("finished", "success"):
                print(f"::notice title=deployed::{name} finished after {time.time() - started:.0f}s")
                return True
            if status in ("failed", "cancelled-by-user", "error"):
                print(f"::error title=deploy failed::{name} ended with {status}")
                return False
        if time.time() - last_note >= 120:
            last_note = time.time()
            print(f"{name}: {status or 'no status yet'} after {time.time() - started:.0f}s")
        time.sleep(POLL_S)
    ref = f" (deployment {deployment})" if deployment else ""
    print(f"::error title=deploy timeout::{name} still {status or 'unknown'} after {timeout_s}s{ref}; "
          "it was not cancelled and may still finish in Coolify")
    return False


def last_failed(uuid: str) -> bool:
    """True when the app's most recent Coolify deployment failed, so it is retried on its own."""
    code, body = api("GET", f"/deployments/applications/{uuid}?take=1")
    if code != 200:
        return False
    rows = _rows(body)
    return bool(rows) and (rows[0].get("status") or "").lower() in ("failed", "error")


def already_live(uuid: str, name: str) -> bool:
    """True when the app's latest Coolify deployment finished on MAIN_HEAD, so a deploy would only restart it."""
    if len(MAIN_HEAD) != 40:
        return False
    code, body = api("GET", f"/deployments/applications/{uuid}?take=1")
    if code != 200:
        return False
    rows = _rows(body)
    if not rows:
        return False
    row = rows[0]
    live = (row.get("status") or "").lower() in ("finished", "success") \
        and (row.get("commit") or "").strip().lower() == MAIN_HEAD
    if live:
        print(f"::notice title=already live::{name} already runs {MAIN_HEAD[:8]}; not restarted")
    return live


def deploy(uuid: str, name: str) -> bool:
    code, body = api("POST", f"/deploy?uuid={uuid}&force=false")
    if not 200 <= code < 300:
        print(f"::error title=deploy {name}::HTTP {code} {str(body)[:200]}")
        return False
    rows = _rows(body)
    queued = [r.get("deployment_uuid") for r in rows if r.get("deployment_uuid")]
    if not queued:
        # Coolify answers 200 with only a message when it queued nothing ("Resource not
        # found", "Unauthorized to deploy", queue full; DeployController.php:433-434).
        # Waiting then would read the previous deployment and call it a success.
        message = "; ".join(str(r.get("message")) for r in rows if r.get("message")) or str(body)[:200]
        print(f"::error title=deploy {name}::Coolify queued no deployment: {message[:200]}")
        return False
    _queued[uuid] = queued[0]
    print(f"::notice title=queued::{name} (deployment {queued[0]})")
    return True


def main() -> int:
    # GitHub stamps each log line when it arrives; unbuffered lines keep real times.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    paths = changed_paths()
    want_main, rooms, every_room = wanted(paths)
    code, apps = api("GET", "/applications")
    if code != 200 or not isinstance(apps, list):
        print(f"::error title=coolify::could not list applications (HTTP {code})")
        return 1
    by_name = {a.get("name", ""): a.get("uuid") for a in apps}
    room_apps = {n: u for n, u in by_name.items() if n.startswith("agent-") and n != "agent-template"}
    if every_room:
        targets = dict(room_apps)
    else:
        # A room folder deploys its app and any regional copy (e.g. agent-zenas-za).
        targets = {n: u for n, u in room_apps.items() if any(n == r or n.startswith(r + "-") for r in rooms)}
    # An app whose last deployment failed is retried by itself; nothing else is redeployed for it.
    for name, uuid in sorted(room_apps.items()):
        if name not in targets and last_failed(uuid):
            print(f"::notice title=retry::{name} failed last time and is retried")
            targets[name] = uuid
    if not want_main and last_failed(MAIN_APP):
        print("::notice title=retry::main failed last time and is retried")
        want_main = True
    # tervory-tank-za is deployed with main; if its last deployment failed it is
    # retried on its own, as main and the rooms are.
    za_retry = not want_main and not ZA_OFFLINE and bool(by_name.get(SA_APP)) and last_failed(by_name[SA_APP])
    if za_retry:
        print(f"::notice title=retry::{SA_APP} failed last time and is retried")
    if not want_main and not za_retry and not targets:
        print("::notice title=nothing to deploy::no app code changed")
        return 0
    if want_main and not radix_blocks("main"):
        # Coolify injects the app's runtime .env into every Compose service.
        # Refuse drift before replacing the live stack, rather than losing backups.
        if not verify_main_scope(api):
            return 1
        if already_live(MAIN_APP, "main"):
            pass
        elif not deploy(MAIN_APP, "main") or not wait_for(MAIN_APP, "main", MAIN_TIMEOUT_S):
            print("::error title=stopped::main did not deploy; rooms were not touched")
            return 1
    za_ok = True
    if ZA_OFFLINE:
        skipped = sorted(n for n in targets if n.endswith("-za"))
        targets = {n: u for n, u in targets.items() if not n.endswith("-za")}
        print("::notice title=za offline::ZA_OFFLINE is set: tervory-tank-za"
              + (" and " + ", ".join(skipped) if skipped else "") + " skipped on purpose")
    elif want_main or za_retry:
        za_uuid = by_name.get(SA_APP)
        if radix_blocks(SA_APP):
            pass
        elif za_uuid and already_live(za_uuid, SA_APP):
            pass
        elif not za_uuid or not deploy(za_uuid, SA_APP) or not wait_for(za_uuid, SA_APP, ZA_TIMEOUT_S):
            # The ZA box failing (for example a power outage there) must not hold back
            # Boston's rooms. ZA rooms wait until tervory-tank-za deploys again.
            za_ok = False
            print("::error title=za not deployed::tervory-tank-za did not finish deploying in this run; "
                  "ZA rooms skipped, Boston rooms continue")
            targets = {n: u for n, u in targets.items() if not n.endswith("-za")}
    ok = za_ok
    # One room at a time: deploy, then wait until it is healthy before the next.
    # Starting every room at once took every product page down together (28 Oct, #403).
    for name, uuid in sorted(targets.items()):
        if radix_blocks(name):
            continue
        if already_live(uuid, name):
            continue
        wait = room_timeout(name)
        if DEPLOY_BUDGET_S and time.time() - _run_started + wait > DEPLOY_BUDGET_S:
            print(f"::error title=out of time::{name} not started: its {wait}s wait no longer fits the "
                  f"{DEPLOY_BUDGET_S}s job budget; deploy it by hand (coolify-ops) or with the next change to its code")
            ok = False
            continue
        ok = (deploy(uuid, name) and wait_for(uuid, name, wait)) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
