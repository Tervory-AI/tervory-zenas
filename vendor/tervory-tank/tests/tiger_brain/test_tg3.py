"""TG3: optional Tiger runtime, existing accounting, and sealed offline fixtures."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import agent_skills
import httpx
import pytest
import yaml
from fastapi import FastAPI
from test_agent1_brain_route import Pool
from test_room_seal import FIXTURES, run_seal_check

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "agents/tiger/docker-compose.yml"
TENANT = UUID("11111111-1111-4111-8111-111111111111")
TASK = UUID("22222222-2222-4222-8222-222222222222")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_optional_services_reuse_pinned_runtime_and_sandbox_without_caller():
    config = yaml.safe_load(COMPOSE.read_text())
    services = config["services"]
    brain, sandbox = services["tiger-brain"], services["tiger-sandbox"]
    assert services["agent-tiger"]["environment"]["TIGER_BRAIN_ENABLED"] == "${TIGER_BRAIN_ENABLED:-false}"
    assert brain["profiles"] == sandbox["profiles"] == ["brain"]
    assert brain["build"] == {"context": "..", "dockerfile": "tiger/Dockerfile"}
    assert brain["command"][1] == "gates_brain_service:app"
    assert brain["environment"]["TIGER_BRAIN_ENABLED"] == "${TIGER_BRAIN_ENABLED:-false}"
    assert "COPY tiger/gates_brain_service.py /app/gates_brain_service.py" in (ROOT / "agents/tiger/Dockerfile").read_text()
    assert "HERMES_COMMIT=d923a5eb487a17c0a68801bba9ef0f56c81040a8" in (ROOT / "agents/rig/Dockerfile.brain").read_text()
    assert sandbox["build"]["dockerfile"] == "agents/base/Dockerfile.sandbox"
    assert brain["networks"] == ["gw", "sandbox"] and sandbox["networks"] == ["sandbox"]
    assert config["networks"]["sandbox"] == {"internal": True}
    assert brain["mem_limit"] == "2g" and sandbox["mem_limit"] == "512m"
    assert sandbox["read_only"] and sandbox["pids_limit"] == 64 and not sandbox.get("volumes")
    assert sandbox["cap_drop"] == ["ALL"] and sandbox["security_opt"] == ["no-new-privileges:true"]
    assert any(value.startswith("/work:") for value in sandbox["tmpfs"])
    assert set(sandbox["environment"]) == {"BRAIN_ROOM", "SANDBOX_KEY"}
    assert brain["environment"]["TANK_KEY"] == "${TANK_KEY_TIGER:-}"
    assert brain["environment"]["TIGER_BROWSER_URL"] == "http://tiger-browser-gateway:3000"
    assert brain["environment"]["TIGER_SANDBOX_URL"] == "http://tiger-sandbox:8002"
    assert brain["environment"]["HERMES_URL"] == "http://hermes:8000"
    for spec in (brain, sandbox):
        assert spec["environment"]["SANDBOX_KEY"] == "${SANDBOX_KEY_TIGER:-}"
        assert not set(spec["environment"]) & {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "LITELLM_MASTER_KEY"}
        assert not any(key in spec for key in ("ports", "privileged", "cap_add", "env_file", "network_mode"))
    source = (ROOT / "agents/tiger/app.py").read_text()
    assert "tiger-brain" not in source and "/v1/agents/tiger/" not in source


@pytest.mark.parametrize("profile", [False, True])
def test_actual_compose_profile_model_excludes_optional_services_by_default(profile):
    binary = os.environ.get("TG3_COMPOSE_BINARY") or shutil.which("docker-compose")
    command = [binary] if binary else [shutil.which("docker"), "compose"]
    assert command[0], "Docker Compose is required for the real profile-model check"
    command += ["-f", str(COMPOSE), "--env-file", "/dev/null"]
    if profile:
        command += ["--profile", "brain"]
    result = subprocess.run([*command, "config", "--services"], capture_output=True, text=True,
        env={"PATH": os.defpath, "AGENT_DB_PASSWORD_TIGER": "placeholder-database", "ACCESS_BROWSER_KEY_TIGER": "placeholder-browser"}, timeout=30)
    assert result.returncode == 0, result.stderr
    expected = {"agent-tiger", "tiger-browser", "tiger-browser-gateway"}
    if profile:
        expected |= {"tiger-brain", "tiger-sandbox"}
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.asyncio
async def test_standby_health_has_no_task_api_or_model_path():
    standby = load("placeholder_tiger_standby", ROOT / "agents/_brain/standby.py")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=standby.app), base_url="http://placeholder.invalid") as client:
        assert (await client.get("/health")).json() == {"ok": True, "room": "tiger", "hermes_version": "0.22.2"}
        assert (await client.post("/tasks", json={"tenant_id": str(TENANT)})).status_code == 404
    assert {route.path for route in standby.app.routes if "POST" in getattr(route, "methods", {})} == set()


@pytest.fixture
def corridor(monkeypatch):
    # Corridor protocol fixtures configure admission explicitly; default Ask,
    # durable cards and master precedence use real SQL in test_paid_steps.py.
    import paid_steps
    async def checkpoint(key, invoke):
        return await invoke()
    async def configured_admission(*args, **kwargs):
        await agent_skills.controls.refuse_paid(task=args[1], path="paid_steps")
        return None
    monkeypatch.setattr(paid_steps,"checkpoint",checkpoint)
    monkeypatch.setattr(paid_steps,"model_admission",configured_admission)
    keys = {room: "placeholder-" + room + "-key" for room in ("base", "rig", "tiger")}
    for room, key in keys.items():
        monkeypatch.setenv("TANK_KEY_" + room.upper(), key)
    pool = Pool()
    monkeypatch.setattr(agent_skills.controls, "db", lambda: pool)
    policy = AsyncMock(return_value={"model": "placeholder-serving", "baseline_model": "placeholder-baseline",
                                   "rung": 1, "priority": "standard", "max_tokens_out": 100})
    roster = AsyncMock(return_value=[{"model_name": name, "model_info": {"input_cost_per_token": .001, "output_cost_per_token": .002}}
                                    for name in ("placeholder-serving", "placeholder-baseline")])
    tank = SimpleNamespace(pool=lambda: pool, load_policy=policy, _roster_models=roster,
        box_region=lambda: "us-east", model_call_refusal=Mock(return_value=None),
        graduate=SimpleNamespace(router_cannot_serve=lambda model: False),
        LITELLM_URL="http://placeholder-gateway", LITELLM_MASTER_KEY="placeholder-gateway-key")
    app = FastAPI()
    app.include_router(agent_skills.build_router(tank))
    original = httpx.AsyncClient
    sent = []
    def gateway(request):
        sent.append(request)
        return httpx.Response(200, headers={"x-litellm-response-cost": "0.01", "x-litellm-model-group": "placeholder-serving"}, json={
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "placeholder response"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    monkeypatch.setattr(agent_skills.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(gateway)))
    return SimpleNamespace(app=app, tank=tank, pool=pool, sent=sent, keys=keys, client=original)


async def chat(corridor, key="placeholder-tiger-key", region="us-east"):
    async with corridor.client(transport=httpx.ASGITransport(app=corridor.app), base_url="http://placeholder-tank") as client:
        return await client.post(corridor.app.url_path_for("chat", room="tiger"), json={"messages": [{"role": "user", "content": "placeholder request"}]},
            headers={"X-Tank-Key": key, "X-Tenant-ID": str(TENANT), "X-Task-ID": str(TASK), "X-Home-Region": region})


@pytest.mark.asyncio
async def test_tiger_owning_key_keeps_policy_guard_and_durable_ledger(corridor):
    response = await chat(corridor)
    assert response.status_code == 200, response.text
    corridor.tank.load_policy.assert_awaited_once_with("agent.tiger.think", TENANT)
    corridor.tank.model_call_refusal.assert_called_once()
    assert corridor.tank.model_call_refusal.call_args.kwargs["caller"] == "tiger"
    assert len(corridor.sent) == 1
    rows = corridor.pool.rows
    assert len(rows) == 3 and "tank.ledger" in rows[0][0]
    assert rows[0][1][1] == TENANT and rows[0][1][11] == TASK
    assert rows[0][1][13:15] == ("tiger", "agent.tiger.think")
    assert "tank.transcripts" in rows[1][0] and "tank.agent_receipts" in rows[2][0]
    assert response.json()["receipt"]["room"] == "tiger"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "placeholder-base-key", "placeholder-rig-key", "placeholder-workspace-key", "placeholder-unknown-key"])
async def test_foreign_and_workspace_keys_cannot_use_tiger_brain_route(corridor, key):
    assert (await chat(corridor, key)).status_code == 401
    assert corridor.sent == [] and corridor.pool.rows == []


@pytest.mark.asyncio
async def test_pause_and_model_refusal_prevent_any_gateway_send(corridor):
    corridor.pool.paused = True
    assert (await chat(corridor)).status_code == 503
    assert corridor.sent == []
    corridor.pool.paused = False
    corridor.tank.model_call_refusal.return_value = "placeholder refusal"
    assert (await chat(corridor)).status_code == 403
    assert corridor.sent == []
    assert (await chat(corridor, region="za")).status_code == 403


@pytest.mark.asyncio
async def test_tiger_export_uses_own_room_and_candidate_submission_stays_base_only(corridor, monkeypatch):
    export = AsyncMock(return_value={"tenant_id": str(TENANT), "room": "tiger", "region": "us-east", "skills": []})
    monkeypatch.setattr(agent_skills, "export_skills", export)
    async with corridor.client(transport=httpx.ASGITransport(app=corridor.app), base_url="http://placeholder-tank") as client:
        response = await client.get("/v1/agents/tiger/skills", params={"tenant_id": str(TENANT)}, headers={"X-Tank-Key": corridor.keys["tiger"]})
        assert response.status_code == 200
        export.assert_awaited_once_with(corridor.tank, TENANT, "us-east", room="tiger")
        # Reuse a valid body from the existing candidate contract rather than bypass validation.
        candidate = {"tenant_id": str(TENANT), "home_region": "us-east", "skill_id": "base.learned.placeholder", "version": "1.0.0",
                     "description": "placeholder", "instructions": "placeholder", "run_py": "async def run(input, ctx): return input",
                     "cases": [{"input": {"value": "placeholder"}, "expected": {"value": "placeholder"}}]}
        submit = AsyncMock(side_effect=AssertionError("placeholder: Tiger must not submit a candidate"))
        monkeypatch.setattr(agent_skills, "submit", submit)
        refused = await client.post("/v1/agents/tiger/skills/candidates", json=candidate, headers={"X-Tank-Key": corridor.keys["tiger"]})
        assert refused.status_code == 403
        submit.assert_not_awaited()


def topology(room):
    names = ["placeholder-" + part for part in ("room", "brain", "sandbox", "browser", "gateway")]
    services = ["agent-" + room, room + "-brain", room + "-sandbox", room + "-browser", room + "-browser-gateway"]
    corridor, sandbox_net, browser_net = "tervory-gw-" + room, "placeholder_sandbox", "placeholder_browser"
    memberships = [[corridor], [corridor, sandbox_net], [sandbox_net], [browser_net], [corridor, browser_net]]
    rows = {}
    for name, service, networks in zip(names, services, memberships):
        rows[name] = {"Id": name, "State": {"Health": {"Status": "healthy"}},
            "Config": {"Labels": {"com.docker.compose.service": service}, "Env": []}, "Mounts": [],
            "HostConfig": {"Privileged": False, "CapAdd": [], "NetworkMode": "private", "PidMode": "", "PortBindings": {},
                           "ReadonlyRootfs": True, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"], "Tmpfs": {"/work": ""}},
            "NetworkSettings": {"Networks": {net: {"IPAddress": "10.0.0.2"} for net in networks}}}
    nets = {net: {"Internal": net != corridor, "Containers": {name: {} for name, row in rows.items() if net in row["NetworkSettings"]["Networks"]}}
            for net in (corridor, sandbox_net, browser_net)}
    return names, rows, nets


@pytest.mark.parametrize("room", ["base", "tiger"])
def test_isolation_checker_passes_own_topology_and_uses_supplied_sandbox(monkeypatch, capsys, room):
    gate = load("placeholder_isolation_gate", ROOT / "agents/base/check_brain_isolation.py")
    names, rows, networks = topology(room)
    calls = []
    def docker(*args):
        calls.append(args)
        if args[0] == "inspect":
            return json.dumps([rows[args[1]]])
        if args[:2] == ("network", "inspect"):
            return json.dumps([networks[args[2]]])
        assert args[0] == "exec"
        compile(args[-1], "placeholder probe", "exec")
        return ""
    monkeypatch.setattr(gate, "docker", docker)
    gate.main(names)
    assert "PASS " + room.title() + " topology" in capsys.readouterr().out
    probes = [call[-1] for call in calls if call[0] == "exec"]
    assert any("http://" + room + "-sandbox:8002/exec" in script for script in probes)
    assert any(repr("agent_" + room) in script for script in probes)
    if room == "tiger":
        assert not any("http://base-sandbox:" in script for script in probes)


@pytest.mark.parametrize("fault", ["tank_key", "foreign_sandbox", "provider_key"])
def test_isolation_checker_refuses_keys_or_foreign_executor(monkeypatch, fault):
    gate = load("placeholder_bad_isolation_gate", ROOT / "agents/base/check_brain_isolation.py")
    names, rows, _ = topology("tiger")
    if fault == "foreign_sandbox":
        rows[names[2]]["Config"]["Labels"]["com.docker.compose.service"] = "base-sandbox"
    else:
        rows[names[2]]["Config"]["Env"].append(("TANK_KEY" if fault == "tank_key" else "OPENAI_API_KEY") + "=placeholder-value")
    monkeypatch.setattr(gate, "docker", lambda *args: json.dumps([rows[args[1]]]))
    with pytest.raises(AssertionError):
        gate.main(names)
    with pytest.raises(ValueError):
        gate.sandbox_probe_script("placeholder-host\nplaceholder-command")


def tiger_placements():
    data = json.loads((FIXTURES / "sealed.json").read_text())
    room = next(row for row in data["containers"].values() if row["service"] == "agent-tiger")
    project = room.get("project", room["uuid"])
    data["networks"][project + "_sandbox"] = True
    for service, networks in (("tiger-brain", ["tervory-gw-tiger", project + "_sandbox"]), ("tiger-sandbox", [project + "_sandbox"])):
        data["containers"][service] = {**copy.deepcopy(room), "service": service, "project": project,
                                      "name": "placeholder-" + service, "networks": networks}
    return data


def test_seal_accepts_optional_pair(tmp_path):
    path = tmp_path / "placeholder-tiger-sealed.json"
    path.write_text(json.dumps(tiger_placements()))
    result = run_seal_check(tmp_path, str(path))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("service", ["tiger-brain", "tiger-sandbox"])
@pytest.mark.parametrize("internal", [False, True])
def test_extra_networks_cannot_weaken_tiger_brain_seal(tmp_path, service, internal):
    data = tiger_placements()
    data["networks"]["placeholder-extra"] = internal
    data["containers"][service]["networks"].append("placeholder-extra")
    path = tmp_path / "placeholder-tiger-leak.json"
    path.write_text(json.dumps(data))
    result = run_seal_check(tmp_path, str(path))
    assert result.returncode == 1 and "LEAK: placeholder-" + service in result.stdout


@pytest.mark.parametrize("service", ["tiger-brain", "tiger-sandbox"])
def test_partial_optional_pair_cannot_claim_sealed(tmp_path, service):
    data = tiger_placements()
    del data["containers"][service]
    path = tmp_path / "placeholder-tiger-partial.json"
    path.write_text(json.dumps(data))
    result = run_seal_check(tmp_path, str(path))
    assert result.returncode == 1 and "NOT FOUND: " + service in result.stdout


@pytest.mark.parametrize("fault", ["missing_attachment", "outward_sandbox"])
def test_optional_sandbox_attachment_must_exist_and_stay_internal(tmp_path, fault):
    data = tiger_placements()
    sandbox_net = data["containers"]["tiger-sandbox"]["networks"][0]
    if fault == "missing_attachment":
        data["containers"]["tiger-brain"]["networks"].remove(sandbox_net)
    else:
        data["networks"][sandbox_net] = False
    path = tmp_path / "placeholder-tiger-unsealed.json"
    path.write_text(json.dumps(data))
    result = run_seal_check(tmp_path, str(path))
    assert result.returncode == 1 and "NOT SEALED:" in result.stdout
