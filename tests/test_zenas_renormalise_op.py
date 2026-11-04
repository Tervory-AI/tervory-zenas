"""coolify-ops op=zenas-renormalise: fake Coolify, no network.

The op runs the re-normalise job inside the deployed ZA room as a Coolify
scheduled task (the nightly task's container), dry run unless apply=true, and
prints only a fixed projection of the job's report.
"""
import importlib.util
import json
import os
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("renorm_op", ROOT / ".github/scripts/zenas_renormalise_op.py")
op = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(op)

APP, NAME, NIGHTLY = op.TARGETS["za"]
BASE = f"/applications/{APP}"
TASK = "renormtask0001"
REPORT = {
    "dry_run": True,
    "counts": {"rows": 20, "batches": 2, "changed": 1, "unchanged": 19},
    "changed": [{"document_id": "cac:cac 103", "citation": "CAC 103", "suspect_spaces_before": 9,
                 "suspect_spaces_after": 0, "secret": "DO_NOT_PRINT"}],
    "last_document_id": "cac:cac 120",
}
OUTPUT = ("2026-10-31 renormalise batch 1: {\"rows\": 10}\nDATABASE_URL=DO_NOT_PRINT\n"
          + json.dumps(REPORT, indent=2) + "\n")


class FakeCoolify:
    """Coolify as the op sees it. An execution row exists only once a worker starts
    the job (start_after polls after the execute call; None = never starts).

    Like Coolify (Eloquent update on a clean model), a PATCH that changes no field
    does not move updated_at. The clock only moves forward and is always later
    than every execution already started."""

    def __init__(self, existing=True, running=False, finish="success", execute_code=200, patch_code=200,
                 create_code=201, nightly_running=False, start_after=1, hidden=False, poll_error_at=None,
                 queue_on_5xx=False):
        self.calls = []
        self.tick = 0
        self.tasks = [{"uuid": NIGHTLY, "name": op.NIGHTLY_NAME, "container": "agent-zenas", "command": "DO_NOT_PRINT"}]
        if existing:
            self.tasks.append({"uuid": TASK, "name": op.TASK_NAME, "command": "old", "timeout": op.IDLE_TIMEOUT,
                               "updated_at": "2026-10-30T00:00:00Z"})
        self.executions = [{"id": 1, "status": "running" if running else "success",
                            "created_at": "2026-10-30T01:00:00Z", "started_at": "2026-10-30T01:00:00Z",
                            "finished_at": None if running else "2026-10-30T01:10:00Z",
                            "message": "old run DO_NOT_PRINT"}]
        if not existing:
            self.executions = []  # a task that does not exist yet has no executions
        self.nightly = [{"id": 9, "status": "running" if nightly_running else "success",
                         "created_at": "2026-10-31T00:00:00Z", "message": "DO_NOT_PRINT"}]
        self.finish, self.execute_code, self.patch_code, self.create_code = finish, execute_code, patch_code, create_code
        self.start_after, self.hidden, self.poll_error_at = start_after, hidden, poll_error_at
        self.queue_on_5xx = queue_on_5xx
        self.queued_at_poll = None
        self.started_row = None
        self.polls = 0
        self.clock = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)

    def now(self):
        stamps = [op._stamp(row.get(field)) for row in self.executions for field in ("created_at", "started_at")]
        self.clock = max([self.clock, *[s for s in stamps if s]]) + timedelta(minutes=1)
        return self.clock.strftime("%Y-%m-%dT%H:%M:%SZ")

    def mine(self):
        return next((t for t in self.tasks if t["name"] == op.TASK_NAME), None)

    def __call__(self, method, path, body=None):
        assert op.allowed(method, path), (method, path)
        self.calls.append((method, path, body))
        if (method, path) == ("GET", BASE):
            return 200, {"uuid": APP, "name": NAME}
        if (method, path) == ("GET", BASE + "/scheduled-tasks"):
            return 200, [dict(t) for t in self.tasks]
        if (method, path) == ("GET", f"{BASE}/scheduled-tasks/{NIGHTLY}/executions"):
            return 200, self.nightly
        if (method, path) == ("POST", BASE + "/scheduled-tasks"):
            if self.create_code >= 300:
                return self.create_code, None
            self.tasks.append({"uuid": TASK, "name": op.TASK_NAME, **body, "updated_at": self.now()})
            return self.create_code, {"uuid": TASK}
        if (method, path) == ("PATCH", f"{BASE}/scheduled-tasks/{TASK}"):
            first = not any(m == "PATCH" for m, _, _ in self.calls[:-1])
            code = self.patch_code if first else 200  # only the first PATCH is made to fail
            mine = self.mine()
            if any(mine.get(k) != v for k, v in body.items()):  # a no-op PATCH keeps updated_at
                mine.update(body, updated_at=self.now())  # Coolify may store it and still answer 5xx
            return code, None
        if (method, path) == ("POST", f"{BASE}/scheduled-tasks/{TASK}/execute"):
            if self.execute_code == 200 or self.execute_code >= 500 and self.queue_on_5xx:
                self.queued_at_poll = self.polls
                self.started_row = None
                self.exec_id = 100 + len([c for c in self.calls if c[1].endswith("/execute")])
                self.queued_command = self.mine()["command"]
            return self.execute_code, None
        if (method, path) == ("GET", f"{BASE}/scheduled-tasks/{TASK}/executions"):
            self.polls += 1
            if self.poll_error_at is not None and self.polls >= self.poll_error_at:
                return 500, None
            if self.queued_at_poll is not None and self.start_after is not None:
                age = self.polls - self.queued_at_poll
                if age >= self.start_after:
                    done = age >= self.start_after + 1
                    if self.started_row is None:  # the worker starts it now
                        stamp = self.now()
                        self.started_row = {"id": self.exec_id, "created_at": stamp, "started_at": stamp}
                        self.executions.append(self.started_row)
                    self.started_row.update(status=self.finish if done else "running",
                                            finished_at=self.now() if done else None)
                    if not self.hidden:
                        self.started_row["message"] = OUTPUT if done else ""
            return 200, [dict(r) for r in self.executions]
        raise AssertionError((method, path))


@pytest.fixture(autouse=True)
def runner_temp(tmp_path, monkeypatch):
    """Each test gets its own $RUNNER_TEMP, as each workflow job does."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    return tmp_path


def marker_file():
    return Path(os.environ["RUNNER_TEMP"]) / op.STORED_MARKER


def left_marker(**flags):
    """What a run step that stored a command and was then stopped leaves behind."""
    marker_file().write_text(json.dumps({"source": "", "limit": "", "after": "", **flags}))


def env(**kw):
    base = {"REGION": "za", "APPLY": "false", "SOURCE": "", "LIMIT": "", "AFTER": ""}
    base.update(kw)
    return base


def run(fake, **kw):
    return op.run(fake, env(**kw), sleep=lambda s: None, clock=iter(range(0, 10_000, 5)).__next__)


def test_dry_run_by_default_in_the_nightly_container():
    fake = FakeCoolify()
    result = run(fake, SOURCE="cac", LIMIT="20")
    patch = next(body for method, path, body in fake.calls if method == "PATCH")
    assert patch["command"] == ("python -m agents.zenas.pool.renormalise --batch-size 25 "
                                "--source cac --limit 20 --dry-run")
    assert "--apply" not in patch["command"]
    assert patch["container"] == "agent-zenas"  # the same container the nightly job runs in
    assert patch["enabled"] is False  # never on a schedule
    assert result["apply"] is False
    assert result["execution"]["status"] == "success"
    assert result["report"] == {
        "dry_run": True,
        "counts": {"batches": 2, "changed": 1, "rows": 20, "unchanged": 19},
        "changed": [{"document_id": "cac:cac 103", "suspect_spaces_before": 9, "suspect_spaces_after": 0}],
        "last_document_id": "cac:cac 120",
    }
    assert "DO_NOT_PRINT" not in json.dumps(result)


@pytest.mark.parametrize("value", ["", "false", "FALSE"])
def test_anything_but_true_is_a_dry_run(value):
    fake = FakeCoolify()
    run(fake, APPLY=value)
    patch = next(body for method, path, body in fake.calls if method == "PATCH")
    assert patch["command"].endswith("--dry-run")


def test_apply_only_with_explicit_true():
    fake = FakeCoolify()
    result = run(fake, APPLY="true", AFTER="cac:cac 120")
    patch = next(body for method, path, body in fake.calls if method == "PATCH")
    assert patch["command"].endswith("--after 'cac:cac 120' --apply")
    assert result["apply"] is True


def test_creates_the_task_once_when_missing():
    fake = FakeCoolify(existing=False)
    result = run(fake)
    creates = [body for method, path, body in fake.calls if method == "POST" and path.endswith("/scheduled-tasks")]
    assert len(creates) == 1 and creates[0]["name"] == op.TASK_NAME and creates[0]["enabled"] is False
    assert result["task_uuid"] == TASK


def test_refuses_while_a_run_is_still_running():
    fake = FakeCoolify(running=True)
    with pytest.raises(op.OpError, match="still running"):
        run(fake)
    assert not any(method in {"PATCH", "POST"} for method, _, _ in fake.calls)


@pytest.mark.parametrize("bad", [
    {"REGION": "us"}, {"REGION": ""}, {"APPLY": "yes"}, {"SOURCE": "cac;rm -rf /"},
    {"LIMIT": "0"}, {"LIMIT": "-5"}, {"LIMIT": "10 --apply"}, {"AFTER": "x$(id)"}, {"AFTER": "a`b`"},
    # Review nit: ASCII digits only. str.isdigit() accepts these, then int() raised ValueError.
    {"LIMIT": "\u00b2"}, {"LIMIT": "1\u00b2"}, {"LIMIT": "\u0661\u0662"},
    {"APPLY": "true", "SOURCE": "special-tribunal"}, {"APPLY": "true", "SOURCE": "cac,special-tribunal"},
])
def test_bad_inputs_stop_before_any_call(bad):
    fake = FakeCoolify()
    with pytest.raises(op.OpError):
        run(fake, **bad)
    assert fake.calls == []


def test_wrong_application_stops_before_any_write():
    fake = FakeCoolify()
    original = fake.__call__

    def wrong(method, path, body=None):
        if (method, path) == ("GET", BASE):
            return 200, {"uuid": APP, "name": "agent-zenas"}  # Boston's name
        return original(method, path, body)

    with pytest.raises(op.OpError):
        op.run(wrong, env(), sleep=lambda s: None, clock=lambda: 0)
    assert not any(method != "GET" for method, _, _ in fake.calls)


def test_execute_unsupported_fails_cleanly():
    fake = FakeCoolify(execute_code=404)
    with pytest.raises(op.OpError, match="HTTP 404"):
        run(fake)


def test_still_running_at_the_wait_limit_points_to_report(monkeypatch):
    fake = FakeCoolify()
    monkeypatch.setattr(op, "WAIT_SECONDS", 0)
    result = op.run(fake, env(), sleep=lambda s: None, clock=lambda: 0)
    assert result["execution"]["status"] == "running"
    assert "zenas-renormalise-report" in result["next"]


def test_report_reads_the_latest_run_only():
    fake = FakeCoolify()
    fake.executions.append({"id": 3, "status": "failed", "started_at": "2026-10-31T21:00:00Z",
                            "finished_at": "2026-10-31T21:01:00Z", "message": "Traceback DO_NOT_PRINT"})
    out = op.report(fake, env())
    assert out["execution"]["status"] == "failed" and out["report"] is None
    assert "DO_NOT_PRINT" not in json.dumps(out)
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_main_prints_no_bodies(capsys, monkeypatch):
    monkeypatch.setenv("REGION", "za")
    fake = FakeCoolify()
    fake.executions[0]["message"] = OUTPUT
    assert op.main(["report"], call=lambda m, p, b=None: fake(m, p, b)) == 0
    printed = capsys.readouterr().out
    assert '"rows": 20' in printed
    assert "DO_NOT_PRINT" not in printed


def test_transport_only_fixed_paths_and_host(monkeypatch):
    monkeypatch.setenv("COOLIFY_URL", "https://untrusted.invalid")
    monkeypatch.setenv("COOLIFY_TOKEN", "offline-placeholder")
    with pytest.raises(op.OpError):
        op.api("GET", BASE)
    monkeypatch.setenv("COOLIFY_URL", op.HOST)
    for path in (f"/applications/{NIGHTLY}", "/applications/xnmc0jkz8j9i5jh1jwanbsfv/envs", "/deploy?uuid=" + APP,
                 BASE + "/envs", f"{BASE}/scheduled-tasks/../envs"):
        with pytest.raises(op.OpError):
            op.api("POST", path)


def test_workflow_wires_the_op_without_generic_api_shell():
    workflow = yaml.safe_load((ROOT / ".github/workflows/coolify-ops.yml").read_text())
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["apply"]["type"] == "boolean" and inputs["apply"]["default"] is False
    assert inputs["region"]["default"] == ""
    steps = workflow["jobs"]["op"]["steps"]
    run_step = next(s for s in steps if s.get("if") == "inputs.op == 'zenas-renormalise'")
    assert run_step["run"] == "exec python3 .github/scripts/zenas_renormalise_op.py run"
    report_step = next(s for s in steps if s.get("if") == "inputs.op == 'zenas-renormalise-report'")
    assert report_step["run"] == "exec python3 .github/scripts/zenas_renormalise_op.py report"
    generic = next(s for s in steps if s.get("name") == "Run")
    assert "inputs.op != 'zenas-renormalise'" in generic["if"]
    assert "inputs.op != 'zenas-renormalise-report'" in generic["if"]
    check = next(s for s in steps if s.get("name") == "Check inputs")
    assert "zenas-renormalise|zenas-renormalise-report" in check["run"]
    assert 'test "$REGION" = za' in check["run"]
    # Inputs reach the script through env only, never interpolated into a shell line.
    env_block = workflow["jobs"]["op"]["env"]
    assert env_block["APPLY"] == "${{ inputs.apply }}" and env_block["AFTER"] == "${{ inputs.after }}"
    for step in steps:
        assert "inputs.after" not in str(step.get("run", "")) and "inputs.source" not in str(step.get("run", ""))
    # Not a deploy (never the coolify-deploy group), but both renormalise ops share one
    # fixed group, so two dispatches never run side by side.
    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    assert ("(inputs.op == 'zenas-renormalise' || inputs.op == 'zenas-renormalise-report') "
            "&& 'coolify-zenas-renormalise'") in concurrency["group"]


def test_doc_describes_the_op():
    doc = (ROOT / "docs/ops/ZENAS-ZA-RENORMALISE.md").read_text()
    assert "op=zenas-renormalise" in doc and "apply=true" in doc and "zenas-renormalise-report" in doc


# --- Review of #512 -----------------------------------------------------------------

def stored(fake):
    return fake.mine()


def test_limit_superscript_is_a_clean_refusal():
    with pytest.raises(op.OpError, match="whole number"):
        op.inputs(env(LIMIT="\u00b2"))


# Point 2: queued-but-not-started, nightly running, fixed group (workflow test above).
def test_refuses_when_the_task_was_queued_after_its_latest_execution():
    fake = FakeCoolify()
    stored(fake).update(timeout=op.QUEUED_TIMEOUT, updated_at="2026-10-31T09:00:00Z")  # after execution 1
    with pytest.raises(op.OpError, match="queued after the latest execution"):
        run(fake)
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_a_run_that_never_starts_blocks_the_next_dispatch(monkeypatch):
    fake = FakeCoolify(start_after=None)
    monkeypatch.setattr(op, "WAIT_SECONDS", 0)
    first = op.run(fake, env(APPLY="true"), sleep=lambda s: None, clock=lambda: 0)
    assert first["execution"] is None and "not started" in first["next"]
    assert stored(fake)["command"].endswith("--dry-run")  # the dashboard button stays a dry run
    assert stored(fake)["timeout"] == op.QUEUED_TIMEOUT  # still marked: a run is queued
    writes = len([c for c in fake.calls if c[0] != "GET"])
    with pytest.raises(op.OpError, match="queued after the latest execution"):
        run(fake)
    assert len([c for c in fake.calls if c[0] != "GET"]) == writes
    # Once the queued run starts (here: an execution newer than the task update), it is allowed again.
    fake.executions.append({"id": 5, "status": "success", "created_at": "2026-10-31T23:00:00Z",
                            "started_at": "2026-10-31T23:00:00Z", "message": OUTPUT})
    fake.start_after = 1
    monkeypatch.setattr(op, "WAIT_SECONDS", 450)
    assert run(fake)["execution"]["status"] == "success"


def test_an_updated_but_idle_task_is_not_mistaken_for_a_queued_run():
    fake = FakeCoolify()
    stored(fake).update(timeout=op.IDLE_TIMEOUT, updated_at="2026-10-31T09:00:00Z")  # restored after a failure
    assert run(fake)["execution"]["status"] == "success"


def test_unknown_update_time_on_a_marked_task_is_refused():
    fake = FakeCoolify()
    stored(fake).update(timeout=op.QUEUED_TIMEOUT, updated_at=None)
    with pytest.raises(op.OpError, match="queued after"):
        run(fake)


def test_refuses_while_the_nightly_pool_job_runs():
    fake = FakeCoolify(nightly_running=True)
    with pytest.raises(op.OpError, match="zenas-pool-nightly is running"):
        run(fake)
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_of_the_nightly_task_only_its_executions_may_be_read():
    nightly = f"{BASE}/scheduled-tasks/{NIGHTLY}"
    assert op.allowed("GET", nightly + "/executions")
    for method, path in [("POST", nightly + "/execute"), ("PATCH", nightly), ("GET", nightly),
                         ("POST", nightly + "/executions"), ("DELETE", nightly)]:
        assert not op.allowed(method, path), (method, path)
    assert nightly + "/execute" not in op.allowed_paths(APP, NIGHTLY)


def test_two_dispatches_share_one_fixed_concurrency_group():
    sys_path = str(ROOT / "tests")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from test_live_check import group_for

    group = yaml.safe_load((ROOT / ".github/workflows/coolify-ops.yml").read_text())["concurrency"]["group"]
    assert group_for(group, "zenas-renormalise") == group_for(group, "zenas-renormalise-report") == "coolify-zenas-renormalise"
    assert group_for(group, "deploy") == "coolify-deploy" and group_for(group, "status") == "coolify-ops-RUN"


# Point 3: special-tribunal waits for #502.
def test_default_sources_leave_out_special_tribunal_and_match_the_job():
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from agents.zenas.pool import renormalise

    assert "special-tribunal" not in op.DEFAULT_SOURCES
    assert set(op.DEFAULT_SOURCES) == {s.source_id for s in renormalise.pdf_case_sources("za")}
    assert op.WAIT_FOR_502 == renormalise.WAIT_FOR_502
    fake = FakeCoolify()
    run(fake, APPLY="true")
    command = next(body for method, path, body in fake.calls if method == "PATCH")["command"]
    assert "special-tribunal" not in command and "--source cac" in command and command.endswith("--apply")
    with pytest.raises(op.OpError, match="wait for #502"):
        op.inputs(env(APPLY="true", SOURCE="special-tribunal"))
    assert op.inputs(env(SOURCE="special-tribunal"))["sources"] == ["special-tribunal"]  # dry run may name it


# Point 4: after the command is stored, every exit leaves the same flags with --dry-run.
FLAGS = dict(APPLY="true", SOURCE="cac", LIMIT="20", AFTER="cac:cac 120")
DRY = "python -m agents.zenas.pool.renormalise --batch-size 25 --source cac --limit 20 --after 'cac:cac 120' --dry-run"


@pytest.mark.parametrize("fault,marker", [
    ({"execute_code": 403}, op.IDLE_TIMEOUT),  # a definite 4xx: nothing was queued
    ({"execute_code": 500}, op.QUEUED_TIMEOUT),  # a 5xx may still have queued it
    ({"patch_code": 500}, op.IDLE_TIMEOUT),
    ({"start_after": None, "poll_error_at": 2}, op.QUEUED_TIMEOUT),  # read 1 is the pre-check
])
def test_any_failure_after_storing_restores_the_dry_run(fault, marker):
    fake = FakeCoolify(**fault)
    with pytest.raises(op.OpError):
        run(fake, **FLAGS)
    assert stored(fake)["command"] == DRY
    assert stored(fake)["timeout"] == marker


def test_failure_after_a_create_restores_the_dry_run():
    fake = FakeCoolify(existing=False, execute_code=500)
    with pytest.raises(op.OpError, match="HTTP 500"):
        run(fake, **FLAGS)
    assert stored(fake)["command"] == DRY and stored(fake)["timeout"] == op.QUEUED_TIMEOUT


def test_unexpected_error_still_restores(monkeypatch):
    fake = FakeCoolify()
    original = fake.__call__

    def flaky(method, path, body=None):
        if path.endswith("/execute"):
            raise TimeoutError("network")
        return original(method, path, body)

    with pytest.raises(TimeoutError):
        op.run(flaky, env(**FLAGS), sleep=lambda s: None, clock=iter(range(0, 10_000, 5)).__next__)
    assert stored(fake)["command"] == DRY
    assert stored(fake)["timeout"] == op.QUEUED_TIMEOUT  # a network error does not prove nothing was queued


def test_a_successful_apply_runs_apply_then_leaves_a_dry_run():
    fake = FakeCoolify()
    result = run(fake, **FLAGS)
    assert fake.queued_command.endswith("--apply")  # what the worker ran
    assert result["execution"]["status"] == "success"
    assert stored(fake)["command"] == DRY and stored(fake)["timeout"] == op.IDLE_TIMEOUT


def test_restore_failure_is_reported():
    fake = FakeCoolify(execute_code=500)
    original = fake.__call__
    patches = []

    def failing_restore(method, path, body=None):
        if method == "PATCH":
            patches.append(body)
            if len(patches) > 1:
                return 500, None
        return original(method, path, body)

    with pytest.raises(op.OpError, match="restoring the dry-run command failed"):
        op.run(failing_restore, env(**FLAGS), sleep=lambda s: None, clock=iter(range(0, 10_000, 5)).__next__)


def test_doc_says_the_dashboard_button_only_runs_a_dry_run():
    doc = " ".join((ROOT / "docs/ops/ZENAS-ZA-RENORMALISE.md").read_text().split())
    assert "Execute** button in the Coolify dashboard only ever runs a dry run" in doc
    assert "`apply` goes only through this op" in doc
    assert "wait for #502" in doc and "coolify-zenas-renormalise" in doc


# Point 5: hidden output is an error, not a success without a report.
def test_hidden_output_fails_the_run(capsys, monkeypatch):
    fake = FakeCoolify(hidden=True)
    result = run(fake)
    assert result["report"] is None and result["report_status"] == op.HIDDEN
    for name, value in env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(op, "WAIT_SECONDS", 10_000)
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    assert op.main(["run"], call=FakeCoolify(hidden=True)) == 1
    assert "output hidden: COOLIFY_TOKEN lacks read:sensitive" in capsys.readouterr().out


def test_hidden_output_fails_the_report(capsys, monkeypatch):
    monkeypatch.setenv("REGION", "za")
    fake = FakeCoolify()
    del fake.executions[0]["message"]
    assert op.report(fake, env())["report_status"] == op.HIDDEN
    assert op.main(["report"], call=fake) == 1
    assert "output hidden: COOLIFY_TOKEN lacks read:sensitive" in capsys.readouterr().out


def test_visible_output_without_a_report_is_named():
    fake = FakeCoolify()
    fake.executions[0]["message"] = "Traceback DO_NOT_PRINT"
    out = op.report(fake, env())
    assert out["report"] is None and out["report_status"] == "no report in the output"


# --- Review of #512, round 2 ---------------------------------------------------------

def nowait(fake, **kw):
    """One dispatch that stops waiting at once (the run has not started yet)."""
    saved = op.WAIT_SECONDS
    op.WAIT_SECONDS = 0
    try:
        return op.run(fake, env(**kw), sleep=lambda s: None, clock=lambda: 0)
    finally:
        op.WAIT_SECONDS = saved


def started_later(fake, run_id):
    """The queued run starts and finishes after the op has left (nobody clears the mark)."""
    stamp = fake.now()
    fake.executions.append({"id": run_id, "status": "success", "created_at": stamp, "started_at": stamp,
                            "finished_at": fake.now(), "message": OUTPUT})
    fake.queued_at_poll = None


# Point 1: a PATCH that changes nothing does not move updated_at.
def test_fake_keeps_updated_at_on_a_no_op_patch():
    fake = FakeCoolify()
    before = dict(stored(fake))
    fake("PATCH", f"{BASE}/scheduled-tasks/{TASK}", {"command": before["command"], "timeout": before["timeout"]})
    assert stored(fake)["updated_at"] == before["updated_at"]


def test_a_second_queued_run_is_refused_even_when_the_storing_patch_would_be_a_no_op():
    fake = FakeCoolify(start_after=None)
    assert "not started" in nowait(fake)["next"]  # run 1 queued, never seen to start
    assert stored(fake)["timeout"] == op.QUEUED_TIMEOUT
    started_later(fake, 7)  # run 1 starts later; the mark stays
    second = nowait(fake)  # allowed: run 1 has started. Run 2 is queued and does not start.
    assert "not started" in second["next"]
    patches = [body for method, _, body in fake.calls if method == "PATCH"]
    # The stale mark is cleared before storing, so the storing PATCH changes the
    # timeout and moves updated_at past run 1's start.
    assert [p["timeout"] for p in patches[-3:]] == [op.IDLE_TIMEOUT, op.QUEUED_TIMEOUT, op.QUEUED_TIMEOUT]
    assert op._stamp(stored(fake)["updated_at"]) > op._stamp(fake.executions[-1]["started_at"])
    writes = len([c for c in fake.calls if c[0] != "GET"])
    with pytest.raises(op.OpError, match="queued after the latest execution"):
        nowait(fake)  # run 3 is refused while run 2 is still queued
    assert len([c for c in fake.calls if c[0] != "GET"]) == writes


def test_no_clearing_patch_when_the_task_is_idle():
    fake = FakeCoolify()
    run(fake)
    patches = [body for method, _, body in fake.calls if method == "PATCH"]
    assert patches[0]["timeout"] == op.QUEUED_TIMEOUT  # straight to storing: IDLE -> QUEUED always moves updated_at


# Point 2: a run that starts between the last poll and the "not started" restore.
class StartsDuringRestore(FakeCoolify):
    def __call__(self, method, path, body=None):
        if (method == "PATCH" and body and body["timeout"] == op.QUEUED_TIMEOUT
                and body["command"].endswith("--dry-run") and not any(r["id"] == 8 for r in self.executions)):
            stamp = self.now()
            self.executions.append({"id": 8, "status": "running", "created_at": stamp, "started_at": stamp})
        return super().__call__(method, path, body)


def test_a_run_that_starts_during_the_restore_clears_the_mark():
    fake = StartsDuringRestore(start_after=None)
    result = nowait(fake, APPLY="true")
    assert stored(fake)["timeout"] == op.IDLE_TIMEOUT and stored(fake)["command"].endswith("--dry-run")
    assert result["execution"]["status"] == "running"
    fake.executions[-1].update(status="success", finished_at=fake.now(), message=OUTPUT)
    fake.start_after = 1
    assert run(fake)["execution"]["status"] == "success"  # not wedged


def test_doc_says_how_to_clear_a_stuck_mark():
    doc = " ".join((ROOT / "docs/ops/ZENAS-ZA-RENORMALISE.md").read_text().split())
    assert "set the task's timeout to 35999" in doc
    assert "press **Execute**" in doc and "always a dry run" in doc
    assert "ends with `--apply`" in doc  # a lost runner: check the command first
    assert "store.ingest_snapshot" in doc  # the nightly-overlap follow-up


# Point 3: hard stops. SIGTERM unwinds through the restore; the workflow step covers the rest.
def test_sigterm_while_waiting_restores_the_dry_run(capsys, monkeypatch):
    for name, value in env(**FLAGS).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    fake = FakeCoolify(start_after=None)
    original = fake.__call__

    def terminated(method, path, body=None):
        if path.endswith(f"{TASK}/executions") and fake.queued_at_poll is not None:
            os.kill(os.getpid(), signal.SIGTERM)
        return original(method, path, body)

    handler = signal.getsignal(signal.SIGTERM)
    assert op.main(["run"], call=terminated) == 1
    assert "failed (Terminated)" in capsys.readouterr().out
    assert stored(fake)["command"] == DRY
    assert stored(fake)["timeout"] == op.QUEUED_TIMEOUT  # queued, not seen to start
    assert signal.getsignal(signal.SIGTERM) is handler  # put back after the op


@pytest.mark.parametrize("timeout", [op.QUEUED_TIMEOUT, op.IDLE_TIMEOUT])
def test_restore_step_puts_the_default_dry_run_back_and_keeps_the_mark(timeout, capsys):
    left_marker()
    fake = FakeCoolify()
    stored(fake).update(command=DRY.replace("--dry-run", "--apply"), timeout=timeout)
    assert op.main(["restore"], call=fake) == 0
    default = "python -m agents.zenas.pool.renormalise --batch-size 25 " + " ".join(
        f"--source {s}" for s in op.DEFAULT_SOURCES) + " --dry-run"
    assert stored(fake)["command"] == default and "special-tribunal" not in default
    assert stored(fake)["timeout"] == timeout and stored(fake)["enabled"] is False
    writes = [(m, p) for m, p, _ in fake.calls if m != "GET"]
    assert writes == [("PATCH", f"{BASE}/scheduled-tasks/{TASK}")]  # nothing else is touched
    assert "DO_NOT_PRINT" not in capsys.readouterr().out
    assert not marker_file().exists()  # done: a second restore step would do nothing


def test_restore_step_marks_a_task_with_an_unknown_timeout_and_ignores_inputs(monkeypatch):
    monkeypatch.setenv("SOURCE", "special-tribunal")
    monkeypatch.setenv("APPLY", "true")
    left_marker()
    fake = FakeCoolify()
    stored(fake).pop("timeout")
    assert op.main(["restore"], call=fake) == 0
    assert stored(fake)["timeout"] == op.QUEUED_TIMEOUT and stored(fake)["command"].endswith("--dry-run")
    assert "special-tribunal" not in stored(fake)["command"]


def test_restore_step_without_a_task_writes_nothing():
    left_marker()
    fake = FakeCoolify(existing=False)
    assert op.restore_after_stop(fake)["restored"] is False
    assert all(m == "GET" for m, _, _ in fake.calls)


def test_workflow_restores_after_a_cancel_or_failure():
    workflow = yaml.safe_load((ROOT / ".github/workflows/coolify-ops.yml").read_text())
    job = workflow["jobs"]["op"]
    steps = job["steps"]
    run_step = next(s for s in steps if s.get("if") == "inputs.op == 'zenas-renormalise'")
    restore_step = next(s for s in steps if s.get("if") == "(cancelled() || failure()) && inputs.op == 'zenas-renormalise'")
    assert restore_step["run"] == "exec python3 .github/scripts/zenas_renormalise_op.py restore"
    # exec: the runner signals the step's entry process, so python must be it.
    assert run_step["run"].startswith("exec python3 ")
    assert steps.index(restore_step) == steps.index(run_step) + 1
    # The run step times out before the job does, so the restore step still gets to run.
    assert run_step["timeout-minutes"] + restore_step["timeout-minutes"] < job["timeout-minutes"]
    assert op.WAIT_SECONDS + 60 < run_step["timeout-minutes"] * 60


# Point 4: only a definite 4xx on execute means nothing was queued.
@pytest.mark.parametrize("code,marker", [(400, op.IDLE_TIMEOUT), (404, op.IDLE_TIMEOUT), (409, op.IDLE_TIMEOUT),
                                         (500, op.QUEUED_TIMEOUT), (502, op.QUEUED_TIMEOUT), (503, op.QUEUED_TIMEOUT)])
def test_execute_error_keeps_the_mark_unless_a_definite_4xx(code, marker):
    fake = FakeCoolify(execute_code=code)
    with pytest.raises(op.OpError, match=f"HTTP {code}"):
        run(fake, **FLAGS)
    assert stored(fake)["command"] == DRY and stored(fake)["timeout"] == marker


def test_a_5xx_that_did_queue_blocks_the_next_dispatch_until_it_starts():
    fake = FakeCoolify(execute_code=502, queue_on_5xx=True, start_after=None)
    with pytest.raises(op.OpError, match="may still have queued it"):
        run(fake, **FLAGS)
    with pytest.raises(op.OpError, match="queued after the latest execution"):
        run(fake)
    started_later(fake, 11)
    fake.execute_code, fake.start_after = 200, 1
    assert run(fake)["execution"]["status"] == "success"


# --- Review of #512, round 3 ---------------------------------------------------------

# Point 1: the restore step acts only when the run step stored a command and did not
# restore it itself (the $RUNNER_TEMP marker).
def test_the_marker_is_written_before_storing_and_dropped_after_the_restore():
    fake = FakeCoolify()
    original = fake.__call__
    seen = []

    def call(method, path, body=None):
        if method in ("PATCH", "POST") and body and body.get("timeout") == op.QUEUED_TIMEOUT:
            seen.append((body["command"].split()[-1], marker_file().exists()))
        return original(method, path, body)

    run_ok = op.run(call, env(**FLAGS), sleep=lambda s: None,
                    clock=iter(range(0, 10_000, 5)).__next__)
    assert run_ok["execution"]["status"] == "success"
    assert seen[0] == ("--apply", True)  # the storing PATCH happens with the marker on disk
    assert not marker_file().exists()  # restored in-process: nothing for the restore step


def test_after_a_still_running_refusal_the_restore_step_makes_no_call():  # R1
    fake = FakeCoolify(start_after=None)
    nowait(fake, **FLAGS)  # dispatch 1: queued, not started; restored in-process
    assert not marker_file().exists()
    stamp = fake.now()
    fake.executions.append({"id": 50, "status": "running", "created_at": stamp, "started_at": stamp})
    fake.queued_at_poll = None
    with pytest.raises(op.OpError, match="still running"):
        nowait(fake, **FLAGS)  # dispatch 2 refused: the step fails, so the restore step runs
    before, calls = dict(stored(fake)), len(fake.calls)
    assert op.main(["restore"], call=fake) == 0
    assert len(fake.calls) == calls and stored(fake) == before  # no Coolify call at all
    fake.executions[-1].update(status="success", finished_at=fake.now(), message=OUTPUT)
    fake.start_after = 1
    assert run(fake, **FLAGS)["execution"]["status"] == "success"  # dispatch 3 is allowed


def test_after_a_nightly_refusal_the_restore_step_makes_no_call():  # R1b
    fake = FakeCoolify(start_after=None)
    nowait(fake, **FLAGS)
    started_later(fake, 51)  # the mark is stale now
    fake.nightly[0]["status"] = "running"
    with pytest.raises(op.OpError, match="zenas-pool-nightly is running"):
        nowait(fake, **FLAGS)
    calls = len(fake.calls)
    assert op.restore_after_stop(fake)["restored"] is False
    assert len(fake.calls) == calls
    fake.nightly[0]["status"] = "success"
    fake.start_after = 1
    assert run(fake, **FLAGS)["execution"]["status"] == "success"


def test_a_refusal_keeps_the_flags_of_a_queued_run():  # R2
    fake = FakeCoolify(start_after=None)
    nowait(fake, **FLAGS)  # queued, not started: cac --limit 20 --dry-run, marked
    queued = stored(fake)["command"]
    with pytest.raises(op.OpError, match="queued after the latest execution"):
        nowait(fake)
    assert op.restore_after_stop(fake)["restored"] is False
    assert stored(fake)["command"] == queued == DRY


def test_a_failed_check_inputs_leaves_no_marker_for_the_restore_step():
    with pytest.raises(op.OpError):
        op.run(FakeCoolify(), env(LIMIT="0"))
    fake = FakeCoolify()
    assert op.restore_after_stop(fake)["restored"] is False and fake.calls == []


def test_a_stale_marker_from_an_earlier_step_is_dropped_by_the_next_run():
    left_marker(source="cac")
    fake = FakeCoolify(running=True)
    with pytest.raises(op.OpError, match="still running"):
        run(fake)
    assert not marker_file().exists()


def test_when_the_in_process_restore_fails_the_restore_step_puts_the_run_flags_back():
    fake = FakeCoolify()
    original = fake.__call__
    fail = {"on": False}

    def call(method, path, body=None):
        if path.endswith("/execute"):
            fail["on"] = True
            code, _ = original(method, path, body)
            raise TimeoutError  # the execute call is lost on the way back
        if method == "PATCH" and fail["on"]:
            return 502, None  # and Coolify is down for the in-process restore
        return original(method, path, body)

    with pytest.raises(op.OpError, match="restoring the dry-run command failed"):
        op.run(call, env(**FLAGS), sleep=lambda s: None, clock=lambda: 0)
    assert stored(fake)["command"].endswith("--apply")  # what the restore step is for
    assert marker_file().exists()
    result = op.restore_after_stop(fake)
    assert result["restored"] is True and stored(fake)["command"] == DRY  # the run's own flags, dry
    assert stored(fake)["timeout"] == op.QUEUED_TIMEOUT and not marker_file().exists()


def test_a_tampered_marker_is_validated_and_never_stores_apply():
    marker_file().write_text(json.dumps({"source": "cac; rm -rf /", "limit": "x", "apply": True}))
    fake = FakeCoolify()
    op.restore_after_stop(fake)
    default = op.command(op.DEFAULT_DRY_RUN)
    assert stored(fake)["command"] == default and default.endswith("--dry-run")


# Point 2: GitHub's cancel order is SIGINT, then SIGTERM 7.5 s later. With exec, both
# reach python; neither may cut the in-process restore.
def test_sigint_then_sigterm_during_the_restore_still_leaves_a_dry_run(capsys, monkeypatch):  # R4
    for name, value in env(**FLAGS).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    fake = FakeCoolify(start_after=None)
    original = fake.__call__
    sent = {"int": False, "term": False}

    def call(method, path, body=None):
        if path.endswith(f"{TASK}/executions") and fake.queued_at_poll is not None and not sent["int"]:
            sent["int"] = True
            os.kill(os.getpid(), signal.SIGINT)
        if method == "PATCH" and sent["int"] and not sent["term"]:
            sent["term"] = True
            os.kill(os.getpid(), signal.SIGTERM)  # arrives during the restore PATCH
        return original(method, path, body)

    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    assert op.main(["run"], call=call) == 1
    assert sent == {"int": True, "term": True}
    assert "failed (KeyboardInterrupt)" in capsys.readouterr().out
    assert stored(fake)["command"] == DRY and stored(fake)["timeout"] == op.QUEUED_TIMEOUT
    assert not marker_file().exists()
    assert {s: signal.getsignal(s) for s in handlers} == handlers  # put back after the op


def test_doc_says_the_workflow_execs_python():
    doc = " ".join((ROOT / "docs/ops/ZENAS-ZA-RENORMALISE.md").read_text().split())
    assert "`exec python3 …`" in doc and "ignored" in doc and "SIGINT" in doc and "zenas-renormalise-stored" in doc
