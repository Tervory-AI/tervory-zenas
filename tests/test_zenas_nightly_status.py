"""Fake Coolify metadata responses exercise filtering and resource binding."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nightly_status", ROOT / ".github/scripts/zenas_nightly_status.py")
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)


class Metadata(dict):
    def get(self, key, *args):
        assert key not in {"message", "error_details", "command", "value", "real_value"}
        return super().get(key, *args)


def execution(state="success", start="2026-10-30T02:00:00Z", end="2026-10-30T02:05:00Z"):
    return Metadata(status=state, started_at=start, finished_at=end,
                    message="DO_NOT_PRINT", error_details="DO_NOT_PRINT")


def fake_api(overrides=None):
    data = {}
    calls = []
    for _, app, name, task in status.TARGETS:
        base = f"/applications/{app}"
        data[base] = Metadata(uuid=app, name=name, command="DO_NOT_PRINT")
        data[base + "/scheduled-tasks"] = [Metadata(uuid=task, name=status.TASK_NAME, command="DO_NOT_PRINT")]
        data[base + f"/scheduled-tasks/{task}/executions"] = [execution()]
    data.update(overrides or {})

    def get(path):
        assert path in status.ALLOWED_PATHS
        calls.append(path)
        value = data[path]
        if isinstance(value, Exception):
            raise value
        return value
    return get, data, calls


def test_both_regions_exact_projection_and_no_output_reads(capsys):
    get, _, calls = fake_api()
    assert status.main(get) == 0
    output = capsys.readouterr().out
    assert "DO_NOT_PRINT" not in output
    rows = json.loads(output)
    assert [row["region"] for row in rows] == ["us", "za"]
    assert set(calls) == status.ALLOWED_PATHS
    for row, (region, app, _, task) in zip(rows, status.TARGETS):
        assert row == {"region": region, "resource_uuid": app, "task_uuid": task,
                       "name": status.TASK_NAME, "metadata_status": "verified",
                       "last_execution": {"status": "success", "started_at": "2026-10-30T02:00:00+00:00",
                                          "finished_at": "2026-10-30T02:05:00+00:00", "exit_code": None}}


@pytest.mark.parametrize("bad_resource", [None, {}, {"uuid": status.TARGETS[1][1], "name": "agent-zenas"},
                                         {"uuid": status.TARGETS[0][1], "name": "agent-zenas-za"}])
def test_resource_mismatch_stops_before_task_reads(bad_resource):
    target = status.TARGETS[0]
    get, _, calls = fake_api({f"/applications/{target[1]}": bad_resource})
    with pytest.raises(status.MetadataError):
        status.region_status(get, target)
    assert calls == [f"/applications/{target[1]}"]


@pytest.mark.parametrize("tasks", [None, [], [None], [{"uuid": status.TARGETS[1][3], "name": status.TASK_NAME}],
                                    [{"uuid": status.TARGETS[0][3], "name": "wrong"}],
                                    [{"uuid": status.TARGETS[0][3], "name": status.TASK_NAME}] * 2])
def test_missing_wrong_duplicate_task_never_requests_execution(tasks):
    target = status.TARGETS[0]
    get, _, calls = fake_api({f"/applications/{target[1]}/scheduled-tasks": tasks})
    with pytest.raises(status.MetadataError):
        status.region_status(get, target)
    assert len(calls) == 2


def test_latest_is_by_actual_instant_not_api_order_and_running_is_not_success():
    target = status.TARGETS[0]
    path = f"/applications/{target[1]}/scheduled-tasks/{target[3]}/executions"
    get, _, _ = fake_api({path: [execution("running", "2026-10-30T03:00:00Z", None),
                               execution("success", "2026-10-30T03:30:00+01:00", "2026-10-30T03:35:00+01:00")]})
    row = status.region_status(get, target)
    assert row["last_execution"]["status"] == "running"
    assert row["last_execution"]["finished_at"] is None
    assert row["last_execution"]["exit_code"] is None


@pytest.mark.parametrize("runs", [None, [None], [{}], [execution("UNSAFE%0A")],
                                   [execution(start="UNSAFE%0A")], [execution(start="2026-10-30T02:00:00")],
                                   [execution(end="2026-10-29T02:00:00Z")]])
def test_bad_execution_metadata_fails_closed(runs, capsys):
    target = status.TARGETS[0]
    path = f"/applications/{target[1]}/scheduled-tasks/{target[3]}/executions"
    get, _, _ = fake_api({path: runs})
    assert status.main(get) == 1
    text = capsys.readouterr().out
    assert "UNSAFE" not in text
    rows = json.loads(text)
    assert rows[0]["metadata_status"] == "unverified"
    assert rows[1]["metadata_status"] == "verified"


def test_empty_history_is_not_success_and_transport_errors_never_echo(capsys):
    us, za = status.TARGETS
    get, _, _ = fake_api({f"/applications/{us[1]}/scheduled-tasks/{us[3]}/executions": [],
                         f"/applications/{za[1]}": RuntimeError("DO_NOT_PRINT")})
    assert status.main(get) == 1
    text = capsys.readouterr().out
    assert "DO_NOT_PRINT" not in text
    rows = json.loads(text)
    assert rows[0]["metadata_status"] == "no_executions"
    assert rows[0]["last_execution"] is None
    assert rows[1]["metadata_status"] == "unverified"


def test_transport_is_fixed_host_allowlisted_get_without_redirects(monkeypatch):
    monkeypatch.setenv("COOLIFY_URL", status.HOST)
    monkeypatch.setenv("COOLIFY_TOKEN", "fake-test-only")
    seen = []

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            assert timeout == 30
            return Response(b"[]")

    def build(handler):
        assert handler is status.NoRedirect
        assert handler().redirect_request(None, None, None, None, None, None) is None
        return Opener()

    monkeypatch.setattr(status.urllib.request, "build_opener", build)
    for path in status.ALLOWED_PATHS:
        assert status.get_metadata(path) == []
    assert all(request.get_method() == "GET" and request.data is None for request in seen)
    assert {r.full_url for r in seen} == {status.HOST + "/api/v1" + p for p in status.ALLOWED_PATHS}
    with pytest.raises(status.MetadataError):
        status.get_metadata("/applications/other/envs")
    monkeypatch.setenv("COOLIFY_URL", "https://untrusted.invalid")
    with pytest.raises(status.MetadataError):
        status.get_metadata(next(iter(status.ALLOWED_PATHS)))
    assert len(seen) == len(status.ALLOWED_PATHS)


def test_workflow_runs_only_dedicated_read_step():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/coolify-ops.yml").read_text())
    steps = workflow["jobs"]["op"]["steps"]
    read = next(step for step in steps if step.get("name") == "Read existing US and ZA nightly execution metadata")
    assert read["if"] == "inputs.op == 'nightly-status'"
    assert read["run"] == "python3 .github/scripts/zenas_nightly_status.py"
    shell = next(step for step in steps if step.get("name") == "Run")
    assert "inputs.op != 'nightly-status'" in shell["if"]
    check = next(step for step in steps if step.get("name") == "Check nightly metadata scope")
    assert check["run"] == 'test -z "$APP$ROOM$NAMES"'
    assert check["if"] == "inputs.op == 'nightly-status'"
    assert steps.index(check) < steps.index(read)
