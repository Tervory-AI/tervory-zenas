"""coolify-ops op=zenas-renormalise / zenas-renormalise-report.

Runs `python -m agents.zenas.pool.renormalise` inside the deployed ZA Zenas room
container, the same way the nightly pool job runs there: as a Coolify scheduled
task on that application (the container comes from the existing
zenas-pool-nightly task). Nothing is exec'd from a laptop or box; the Coolify
token stays in this job.

- Dry run unless the workflow input apply is exactly "true". Only then is
  --apply added to the command, and only for the run this op queues: as soon
  as that run has started (or on any failure) the stored command goes back to
  the same flags with --dry-run, so the dashboard's Execute button only ever
  runs a dry run.
- special-tribunal is not in the default sources, and apply=true on it is
  refused until #502.
- Refuses to start while an earlier renormalise execution is running, while a
  run it queued has not started yet, and while zenas-pool-nightly is running.
  The workflow also puts both renormalise ops in one fixed concurrency group.
- The task is kept disabled with a yearly frequency, so it never runs on a
  schedule; it runs only when this op (or a founder in the dashboard) queues it.
- A hard stop is covered too. The workflow runs the op with `exec`, so the
  runner's SIGINT/SIGTERM reach python: SIGINT is KeyboardInterrupt and SIGTERM
  becomes an exception, and the in-process restore then runs with both signals
  ignored. Just before it stores a command the op writes a marker file
  ($RUNNER_TEMP/zenas-renormalise-stored) and deletes it once a restore has
  succeeded; the workflow's "if: cancelled() || failure()" step runs `restore`,
  which acts only while that marker is there (so a refused dispatch writes
  nothing): it puts the marker's flags back with --dry-run and keeps the stored
  queued mark.
- Prints only a fixed projection of the job's JSON report (counts, resume id,
  changed rows' ids and suspect-space counts). The raw execution output and any
  API body are never printed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import shlex
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.request

HOST = "https://coolify.tervory.com"
# region -> (application uuid, application name, nightly task uuid). Only ZA has the job.
TARGETS = {"za": ("rslmzhfmq5tf9ipx385gmu6p", "agent-zenas-za", "m6ggpxv2iegsecpkakqzlwxd")}
NIGHTLY_NAME = "zenas-pool-nightly"
TASK_NAME = "zenas-za-renormalise"
FREQUENCY = "0 0 1 1 *"  # never reached: the task stays disabled
# The task's timeout doubles as a marker, because Coolify only creates an execution
# row when a worker starts the job: QUEUED while a run this op queued has not been
# seen to start, IDLE once it has (or when nothing was queued). 36000 s is
# Coolify's maximum; resume with --after if a run is cut.
QUEUED_TIMEOUT = 36000
IDLE_TIMEOUT = 35999
# The PDF court sources the op names by default: renormalise.PDF_CASE_ADAPTERS
# without WAIT_FOR_502 (special-tribunal waits for #502). Named explicitly so an
# older room image cannot widen the default. A test keeps the two in step.
DEFAULT_SOURCES = ("cac", "comptrib", "concourt", "labour-appeal", "labour-court", "landcourt",
                   "ocj-highcourt", "sars-taxcourt", "sca")
WAIT_FOR_502 = frozenset({"special-tribunal"})
HIDDEN = "output hidden: COOLIFY_TOKEN lacks read:sensitive"
BATCH_SIZE = 25
WAIT_SECONDS = int(os.environ.get("RENORMALISE_WAIT_SECONDS", "450") or 450)
POLL_SECONDS = 15

_UUID = re.compile(r"^[a-z0-9]{8,40}$")
_LIMIT = re.compile(r"[0-9]+", re.ASCII)
_SOURCE = re.compile(r"^[a-z0-9-]{1,40}$")
_AFTER = re.compile(r"^[A-Za-z0-9:._ ()\[\]/-]{1,200}$")
_CONTAINER = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_COUNT_KEY = re.compile(r"^[a-z :]{1,40}$")


class OpError(Exception):
    """A fixed message that is safe to print."""


STORED_MARKER = "zenas-renormalise-stored"


def _marker() -> str:
    """The file that says this job stored a command and has not restored it yet.
    RUNNER_TEMP is per job on the runner and is set by the runner, not by inputs."""
    return os.path.join(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(), STORED_MARKER)


def _drop_marker() -> None:
    try:
        os.remove(_marker())
    except FileNotFoundError:
        pass


class Terminated(Exception):
    """SIGTERM (a cancelled or timed-out workflow step): unwinds so the restore runs."""


def allowed_paths(app: str, task: str = "") -> set[str]:
    base = f"/applications/{app}"
    paths = {base, base + "/scheduled-tasks"}
    if task and task not in _NIGHTLY:
        paths |= {f"{base}/scheduled-tasks/{task}", f"{base}/scheduled-tasks/{task}/executions",
                  f"{base}/scheduled-tasks/{task}/execute"}
    return paths


_NIGHTLY = {t[2] for t in TARGETS.values()}


def allowed(method: str, path: str) -> bool:
    """The fixed calls this op may make. Of the nightly task, only its executions may be read."""
    parts = path.split("/")
    app = parts[2] if len(parts) > 2 else ""
    task = parts[4] if len(parts) > 4 else ""
    if app not in {t[0] for t in TARGETS.values()} or (task and not _UUID.match(task)):
        return False
    if task in _NIGHTLY:
        return method == "GET" and path == f"/applications/{app}/scheduled-tasks/{task}/executions"
    return path in allowed_paths(app, task)


def api(method: str, path: str, body: dict | None = None):
    """One Coolify call. Only fixed ZA-room paths; https host only; no redirects."""
    if os.environ.get("COOLIFY_URL", "").rstrip("/") != HOST:
        raise OpError("COOLIFY_URL must be " + HOST)
    if not allowed(method, path):
        raise OpError("path not allowed")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        HOST + "/api/v1" + path, method=method, data=data,
        headers={"Authorization": "Bearer " + os.environ["COOLIFY_TOKEN"], "Accept": "application/json",
                 **({"Content-Type": "application/json"} if data is not None else {})},
    )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None


def inputs(env=os.environ) -> dict:
    region = env.get("REGION", "").strip()
    if region not in TARGETS:
        raise OpError("region must be za: the re-normalise job exists only for ZA")
    apply_raw = env.get("APPLY", "").strip().lower()
    if apply_raw not in {"", "false", "true"}:
        raise OpError("apply must be true or false")
    sources = [s.strip() for s in env.get("SOURCE", "").split(",") if s.strip()]
    if any(not _SOURCE.match(s) for s in sources):
        raise OpError("source must be comma-separated source ids")
    limit_raw = env.get("LIMIT", "").strip()
    # ASCII digits only: str.isdigit() accepts '²', which int() then rejects.
    if limit_raw and (not _LIMIT.fullmatch(limit_raw) or not 1 <= int(limit_raw) <= 1_000_000):
        raise OpError("limit must be a whole number from 1")
    after = env.get("AFTER", "").strip()
    if after and not _AFTER.match(after):
        raise OpError("after must be a document_id from an earlier report")
    if apply_raw == "true" and WAIT_FOR_502.intersection(sources):
        raise OpError("apply=true is refused for special-tribunal: wait for #502")
    return {"region": region, "apply": apply_raw == "true", "sources": sources,
            "limit": int(limit_raw) if limit_raw else None, "after": after}


def command(spec: dict, apply: bool | None = None) -> str:
    """The exact command the room runs. Every value is validated, then shell-quoted.
    apply=False gives the same flags as a dry run (what the task holds when idle)."""
    apply = spec["apply"] if apply is None else apply
    argv = ["python", "-m", "agents.zenas.pool.renormalise", "--batch-size", str(BATCH_SIZE)]
    for source in spec["sources"] or DEFAULT_SOURCES:
        argv += ["--source", source]
    if spec["limit"] is not None:
        argv += ["--limit", str(spec["limit"])]
    if spec["after"]:
        argv += ["--after", spec["after"]]
    argv.append("--apply" if apply else "--dry-run")
    return " ".join(shlex.quote(part) for part in argv)


def _ok(code: int) -> bool:
    return 200 <= code < 300


def _room(call, region: str) -> tuple[str, str, str, dict]:
    app, name, nightly = TARGETS[region]
    code, resource = call("GET", f"/applications/{app}")
    if code != 200 or not isinstance(resource, dict) or resource.get("uuid") != app or resource.get("name") != name:
        raise OpError("the ZA Zenas room application could not be verified")
    code, tasks = call("GET", f"/applications/{app}/scheduled-tasks")
    if code != 200 or not isinstance(tasks, list) or any(not isinstance(row, dict) for row in tasks):
        raise OpError("the room's scheduled tasks could not be read")
    night = [row for row in tasks if row.get("uuid") == nightly and row.get("name") == NIGHTLY_NAME]
    if len(night) != 1:
        raise OpError("the nightly task that names the room container was not found")
    container = night[0].get("container")
    if container is not None and not (isinstance(container, str) and _CONTAINER.match(container)):
        raise OpError("the nightly task's container is not a plain name")
    mine = [row for row in tasks if row.get("name") == TASK_NAME]
    if len(mine) > 1:
        raise OpError("more than one renormalise task exists; remove the extra one in Coolify")
    task = str(mine[0].get("uuid") or "") if mine else ""
    if task and (not _UUID.match(task) or task == nightly):
        raise OpError("the renormalise task uuid is not plain")
    return app, task, container or "", (mine[0] if mine else {})


def _executions(call, app: str, task: str, what: str = "renormalise") -> list[dict]:
    code, rows = call("GET", f"/applications/{app}/scheduled-tasks/{task}/executions")
    if code != 200 or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise OpError(f"the {what} executions could not be read")
    return rows


def _stamp(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _queued_not_started(task_row: dict, rows: list[dict]) -> bool:
    """True when this op queued a run that Coolify has not started yet: the task
    still carries the QUEUED marker and no execution began after its last update.
    (Coolify creates the execution row only when a worker starts the job.)"""
    if not task_row or task_row.get("timeout") == IDLE_TIMEOUT:
        return False
    updated = _stamp(task_row.get("updated_at"))
    if updated is None:
        return True  # cannot tell: refuse rather than queue a second run
    starts = [t for row in rows for t in (_stamp(row.get("created_at")), _stamp(row.get("started_at"))) if t]
    return not starts or max(starts) < updated


def _key(row: dict) -> str:
    return str(row.get("id") or row.get("uuid") or row.get("created_at") or row.get("started_at") or "")


def _when(row: dict) -> datetime:
    for field in ("started_at", "created_at"):
        parsed = _stamp(row.get(field))
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def report_from_output(output) -> dict | None:
    """The job prints its report as indented JSON last. Return a fixed projection or None."""
    if not isinstance(output, str):
        return None
    lines = output.splitlines()
    for end in range(len(lines) - 1, -1, -1):
        if lines[end].rstrip() != "}":
            continue
        for start in range(end, -1, -1):
            if lines[start].strip() != "{":
                continue
            try:
                data = json.loads("\n".join(lines[start : end + 1]))
            except ValueError:
                continue
            if isinstance(data, dict) and isinstance(data.get("counts"), dict):
                return project(data)
        break
    return None


def project(data: dict) -> dict:
    counts = {str(k): int(v) for k, v in data["counts"].items()
              if isinstance(k, str) and _COUNT_KEY.match(k) and isinstance(v, int) and not isinstance(v, bool)}
    changed = []
    for row in data.get("changed") or []:
        if not isinstance(row, dict) or not _AFTER.match(str(row.get("document_id") or "")):
            continue
        item = {"document_id": str(row["document_id"])}
        for field in ("suspect_spaces_before", "suspect_spaces_after"):
            value = row.get(field)
            item[field] = value if isinstance(value, int) and not isinstance(value, bool) else None
        changed.append(item)
    last = str(data.get("last_document_id") or "")
    return {"dry_run": data.get("dry_run") is True, "counts": counts, "changed": changed[:50],
            "last_document_id": last if _AFTER.match(last) else ""}


def summary(row: dict | None) -> dict:
    if row is None:
        return {"execution": None}
    status = row.get("status") if row.get("status") in ("success", "failed", "running") else "unknown"
    out = {"execution": {"status": status, "started_at": row.get("started_at") if isinstance(row.get("started_at"), str) else None,
                         "finished_at": row.get("finished_at") if isinstance(row.get("finished_at"), str) else None}}
    if status != "running":
        if "message" not in row:
            # Coolify hides an execution's output from tokens without read:sensitive.
            out["report"] = None
            out["report_status"] = HIDDEN
        else:
            out["report"] = report_from_output(row.get("message"))
            out["report_status"] = "ok" if out["report"] is not None else "no report in the output"
    return out


def run(call=api, env=os.environ, sleep=time.sleep, clock=time.monotonic) -> dict:
    _drop_marker()  # a marker is only ever this run's own
    spec = inputs(env)
    app, task, container, task_row = _room(call, spec["region"])
    nightly = TARGETS[spec["region"]][2]
    if any(row.get("status") == "running" for row in _executions(call, app, nightly, NIGHTLY_NAME)):
        raise OpError("zenas-pool-nightly is running; dispatch again after it finishes")
    seen: set[str] = set()
    if task:
        rows = _executions(call, app, task)
        if any(row.get("status") == "running" for row in rows):
            raise OpError("a renormalise run is still running; read it with op=zenas-renormalise-report")
        if _queued_not_started(task_row, rows):
            raise OpError("a renormalise run was queued after the latest execution and has not started; "
                          "wait for it (op=zenas-renormalise-report)")
        seen = {_key(row) for row in rows}

    def body(apply: bool, timeout: int) -> dict:
        return {"name": TASK_NAME, "command": command(spec, apply), "frequency": FREQUENCY,
                "container": container or None, "timeout": timeout, "enabled": False}

    state = {"queued": False, "started": False}

    def settle() -> dict | None:
        """After a QUEUED restore: if a run started meanwhile (between the last poll
        and the restore), the mark is stale, so clear it. Returns that run or None."""
        try:
            new = [row for row in _executions(call, app, task) if _key(row) not in seen]
            if not new:
                return None
            code, _ = call("PATCH", f"/applications/{app}/scheduled-tasks/{task}", body(False, IDLE_TIMEOUT))
        except Exception:  # noqa: BLE001 - best effort; the doc says how to clear a stale mark
            return None
        return max(new, key=_when) if _ok(code) else None

    def restore(still_queued: bool) -> str:
        """Put the same flags back with --dry-run. Returns '' or a note of what failed."""
        uuid = task
        if not uuid:  # the create call failed: find the task if it was made anyway
            try:
                uuid = _room(call, spec["region"])[1]
            except OpError:
                return "; restoring the dry-run command could not be checked: check the task in Coolify"
            if not uuid:
                return ""
        code, _ = call("PATCH", f"/applications/{app}/scheduled-tasks/{uuid}",
                       body(False, QUEUED_TIMEOUT if still_queued else IDLE_TIMEOUT))
        if not _ok(code):
            return f"; restoring the dry-run command failed (HTTP {code}): set it in Coolify"
        _drop_marker()  # restored here, so the workflow's restore step has nothing to do
        return ""

    def mark_stored() -> None:
        """Written just before the storing PATCH/POST: from here until a restore
        succeeds, the workflow's restore step must put a dry run back."""
        flags = {"source": ",".join(spec["sources"]), "limit": "" if spec["limit"] is None else str(spec["limit"]),
                 "after": spec["after"]}
        try:
            with open(_marker(), "w", encoding="utf-8") as handle:
                json.dump(flags, handle)
        except OSError:
            raise OpError("writing the restore marker failed; nothing was stored") from None

    try:
        if task and task_row.get("timeout") != IDLE_TIMEOUT:
            # The guard above found no queued run, so the mark is stale. Clear it first:
            # Coolify moves updated_at only when a field changes, so the storing PATCH
            # below must change the timeout for updated_at to mark this dispatch.
            code, _ = call("PATCH", f"/applications/{app}/scheduled-tasks/{task}", body(False, IDLE_TIMEOUT))
            if not _ok(code):
                raise OpError(f"clearing the stale queued mark failed (HTTP {code})")
        mark_stored()
        if task:
            code, _ = call("PATCH", f"/applications/{app}/scheduled-tasks/{task}", body(spec["apply"], QUEUED_TIMEOUT))
            if not _ok(code):
                raise OpError(f"updating the renormalise task failed (HTTP {code})")
        else:
            code, created = call("POST", f"/applications/{app}/scheduled-tasks", body(spec["apply"], QUEUED_TIMEOUT))
            made = str((created or {}).get("uuid") or "") if isinstance(created, dict) else ""
            if not _ok(code) or not _UUID.match(made) or made == nightly:
                raise OpError(f"creating the renormalise task failed (HTTP {code})")
            task = made
        # From here a run may be queued: a 5xx or a network error does not prove that
        # Coolify did not queue it, so the mark stays. Only a definite 4xx clears it.
        state["queued"] = True
        code, _ = call("POST", f"/applications/{app}/scheduled-tasks/{task}/execute")
        if not _ok(code):
            if 400 <= code < 500:
                state["queued"] = False
                raise OpError(f"queuing the renormalise run failed (HTTP {code}); nothing was queued "
                              "(this Coolify may not support execute)")
            raise OpError(f"queuing the renormalise run failed (HTTP {code}); Coolify may still have queued it, "
                          "so the task stays marked: read it with op=zenas-renormalise-report")
        deadline = clock() + WAIT_SECONDS
        latest = None
        restored = False
        while True:
            new = [row for row in _executions(call, app, task) if _key(row) not in seen]
            latest = max(new, key=_when) if new else None
            if latest is not None and not restored:
                # The worker has started and read the command: back to a dry run now.
                note = restore(still_queued=False)
                if note:
                    raise OpError("the run started" + note)
                restored = True
                state["started"] = True
            if latest is not None and latest.get("status") in ("success", "failed"):
                break
            if clock() >= deadline:
                break
            sleep(POLL_SECONDS)
        if latest is None:
            # Queued but not started: the stored command becomes a dry run and the
            # QUEUED marker stays, so the next dispatch waits for it.
            note = restore(still_queued=True)
            if note:
                raise OpError("the run has not started" + note)
            latest = settle()
    except BaseException as exc:
        # A cancel sends SIGINT, then SIGTERM 7.5 s later: neither may cut the restore.
        held = _hold_signals()
        try:
            if isinstance(exc, OpError) and "restoring the dry-run command" in str(exc):
                raise
            still_queued = state["queued"] and not state["started"]
            note = restore(still_queued=still_queued)
            if still_queued and not note and task:
                settle()
        finally:
            _release_signals(held)
        if isinstance(exc, OpError):
            raise OpError(str(exc) + note) from None
        if note:
            raise OpError(f"failed ({type(exc).__name__})" + note) from None
        raise
    result = {"op": "zenas-renormalise", "region": spec["region"], "apply": spec["apply"],
              "command": command(spec), "task_uuid": task, **summary(latest)}
    if latest is None:
        result["next"] = ("queued, not started yet; the stored command is a dry run again and the next dispatch "
                          "waits for this run. Read it with op=zenas-renormalise-report (report.dry_run shows how it ran)")
    elif latest.get("status") == "running":
        result["next"] = "still running: read the result later with op=zenas-renormalise-report"
    return result


def report(call=api, env=os.environ) -> dict:
    region = env.get("REGION", "").strip()
    if region not in TARGETS:
        raise OpError("region must be za: the re-normalise job exists only for ZA")
    app, task, _, _ = _room(call, region)
    if not task:
        return {"op": "zenas-renormalise-report", "region": region, "task_uuid": "", "execution": None}
    rows = _executions(call, app, task)
    latest = max(rows, key=_when) if rows else None
    return {"op": "zenas-renormalise-report", "region": region, "task_uuid": task, **summary(latest)}


DEFAULT_DRY_RUN = {"apply": False, "sources": [], "limit": None, "after": ""}


def _marked_flags() -> dict | None:
    """The flags the run step stored, from its marker, or None when there is no marker
    (the run step stored nothing, or restored it itself). The flags are validated again
    and are always a dry run; an unreadable marker gives the default dry run."""
    try:
        with open(_marker(), encoding="utf-8") as handle:
            raw = handle.read(4096)
    except FileNotFoundError:
        return None
    except OSError:
        return dict(DEFAULT_DRY_RUN)
    try:
        flags = json.loads(raw)
        spec = inputs({"REGION": "za", "APPLY": "false", "SOURCE": str(flags.get("source", "")),
                       "LIMIT": str(flags.get("limit", "")), "AFTER": str(flags.get("after", ""))})
    except (ValueError, AttributeError, OpError):
        return dict(DEFAULT_DRY_RUN)
    return {"apply": False, "sources": spec["sources"], "limit": spec["limit"], "after": spec["after"]}


def restore_after_stop(call=api) -> dict:
    """The workflow's cancelled()/failure() step. Reads no workflow inputs. Acts only
    while the run step's marker is there (it stored a command and did not restore it):
    puts the marker's flags back with --dry-run and keeps the stored timeout (the queued
    mark), because the stopped run step may already have queued a run. Without the
    marker (a refused dispatch, a failed Check inputs, a run that restored itself) it
    makes no Coolify call at all."""
    region = "za"
    spec = _marked_flags()
    if spec is None:
        return {"op": "zenas-renormalise-restore", "region": region, "restored": False,
                "reason": "the run step stored nothing that needs restoring"}
    app, task, container, row = _room(call, region)
    if not task:
        _drop_marker()
        return {"op": "zenas-renormalise-restore", "region": region, "task_uuid": "", "restored": False}
    timeout = row.get("timeout")
    if not (isinstance(timeout, int) and not isinstance(timeout, bool) and 1 <= timeout <= QUEUED_TIMEOUT):
        timeout = QUEUED_TIMEOUT  # unknown: mark it, so the next dispatch checks before queuing
    code, _ = call("PATCH", f"/applications/{app}/scheduled-tasks/{task}",
                   {"name": TASK_NAME, "command": command(spec), "frequency": FREQUENCY,
                    "container": container or None, "timeout": timeout, "enabled": False})
    if not _ok(code):
        raise OpError(f"restoring the dry-run command failed (HTTP {code}): set it in Coolify")
    _drop_marker()
    return {"op": "zenas-renormalise-restore", "region": region, "task_uuid": task, "restored": True,
            "command": command(spec), "queued_mark": timeout != IDLE_TIMEOUT}


def _on_sigterm(signum, frame):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)  # let the restore finish
    raise Terminated("SIGTERM")


_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def _hold_signals() -> dict:
    """Ignore SIGINT and SIGTERM (a restore must finish); returns the handlers to put back."""
    held = {}
    for signum in _STOP_SIGNALS:
        try:
            held[signum] = signal.signal(signum, signal.SIG_IGN)
        except ValueError:  # not the main thread: signals are not delivered here anyway
            pass
    return held


def _release_signals(held: dict) -> None:
    for signum, handler in held.items():
        signal.signal(signum, handler if handler is not None else signal.SIG_DFL)


def main(argv=None, call=api) -> int:
    mode = (argv or sys.argv[1:] or [""])[0]
    previous = {signum: signal.getsignal(signum) for signum in _STOP_SIGNALS}
    if mode == "restore":
        _hold_signals()  # the restore step itself must not be cut either
    else:
        signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        if mode == "run":
            result = run(call)
        elif mode == "report":
            result = report(call)
        elif mode == "restore":
            result = restore_after_stop(call)
        else:
            raise OpError("mode must be run, report or restore")
    except OpError as exc:
        print(f"::error title=zenas-renormalise::{exc}")
        return 1
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - never print bodies or messages
        print(f"::error title=zenas-renormalise::failed ({type(exc).__name__})")
        return 1
    finally:
        _release_signals(previous)
    if mode == "restore":
        print(json.dumps(result, sort_keys=True))
        return 0
    print(json.dumps(result, sort_keys=True))
    if result.get("report_status") == HIDDEN:
        print(f"::error title=zenas-renormalise::{HIDDEN}")
        return 1
    execution = result.get("execution") or {}
    print(f"::notice title=zenas-renormalise::{json.dumps({k: result.get(k) for k in ('apply', 'task_uuid')} | {'status': execution.get('status'), 'counts': (result.get('report') or {}).get('counts')}, sort_keys=True)}")
    return 1 if execution.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
