"""Zenas standby provisioning, civil gate and regional boundaries, entirely offline."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

from tiger_brain.test_tg3 import corridor as corridor, load, topology  # noqa: E402
from test_room_seal import FIXTURES, leaks, run_seal_check  # noqa: E402
from agents.zenas.core.matters import consider  # noqa: E402
from agents.zenas.core.scope_rules import REFUSED  # noqa: E402

TENANT = UUID("11111111-1111-4111-8111-111111111111")
FILES = {"us-east": "docker-compose.yml", "za": "docker-compose.za.yml"}


@pytest.mark.parametrize("region", FILES)
def test_compose_keeps_both_runtimes_off_hardened_and_box_local(region):
    path = ROOT / "agents/zenas" / FILES[region]
    text = path.read_text()
    config = yaml.safe_load(text)
    room = config["services"]["agent-zenas"]
    brain, sandbox = (config["services"][name] for name in ("zenas-brain", "zenas-sandbox"))
    assert text.startswith("# coolify: raw compose\n") and leaks(text) == []
    assert room["environment"]["ZENAS_BRAIN_ENABLED"] == "${ZENAS_BRAIN_ENABLED:-false}"
    assert brain["profiles"] == sandbox["profiles"] == ["brain"]
    assert brain["build"]["dockerfile"] == "agents/rig/Dockerfile.brain"
    assert brain["command"][1] == "brain.standby:app"
    assert sandbox["build"]["dockerfile"] == "agents/base/Dockerfile.sandbox"
    assert config["networks"]["sandbox"]["internal"] is True
    assert brain["networks"] == ["gw", "sandbox"] and sandbox["networks"] == ["sandbox"]
    assert config["networks"]["gw"]["name"] == "tervory-gw-zenas"
    assert (brain["mem_limit"], brain["cpus"], brain["pids_limit"]) == ("2g", 1, 128)
    assert (sandbox["mem_limit"], sandbox["cpus"], sandbox["pids_limit"]) == ("512m", .5, 64)
    for spec in (brain, sandbox):
        assert spec["read_only"] and spec["tmpfs"]
        assert spec["cap_drop"] == ["ALL"] and spec["security_opt"] == ["no-new-privileges:true"]
        assert not set(spec) & {"ports", "env_file", "privileged", "cap_add", "network_mode", "devices"}
        assert not set(spec["environment"]) & {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "LITELLM_MASTER_KEY"}
        assert not any("BUCKET" in key or "R2_" in key for key in spec["environment"])
        assert spec["environment"]["BRAIN_ROOM"] == "zenas"
        assert spec["environment"]["SANDBOX_KEY"] == "${SANDBOX_KEY_ZENAS:-}"
    assert set(sandbox["environment"]) == {"BRAIN_ROOM", "SANDBOX_KEY"}
    assert not sandbox.get("volumes") and any(value.startswith("/work:") for value in sandbox["tmpfs"])
    assert brain["environment"]["TANK_REGION"] == region
    assert brain["environment"]["TANK_URL"] == "http://tank:8000"
    assert brain["environment"]["ZENAS_URL"] == "http://agent-zenas:8000"
    assert brain["environment"]["ZENAS_SANDBOX_URL"] == "http://zenas-sandbox:8002"
    assert brain["environment"]["HERMES_URL"] == "http://hermes:8000"
    key = "${TANK_KEY:-}" if region == "za" else "${TANK_KEY_ZENAS:-}"
    assert brain["environment"]["TANK_KEY"] == key
    own = "za" if region == "za" else "us"
    assert config["volumes"]["zenas_brain_data"]["name"] == "tervory-zenas-" + own + "-brain-data"
    assert brain["volumes"] == ["zenas_brain_data:/data"]
    if region == "za":
        assert room["environment"]["TANK_REGION"] == "za"
        assert room["environment"]["R2_BUCKET"] == "${R2_BUCKET_ZENAS_ZA:-}"
        assert "tervory-zenas-us-brain-data" not in text


@pytest.mark.parametrize("region", FILES)
def test_existing_volume_entries_match_main_byte_for_byte(region):
    # Snapshots from main f0072e8e: no Git history or network needed in CI.
    original = {
        "us-east": "volumes:\n  agent_zenas_data:\n\n",
        "za": "volumes:\n  # Local to the SA server. Never replicated to the United States.\n  za_agent_data:\n\n",
    }
    text = (ROOT / "agents/zenas" / FILES[region]).read_bytes()
    section = text.split(b"\nvolumes:\n", 1)[1].split(b"networks:\n", 1)[0]
    section = b"volumes:\n" + section
    # Only the newly introduced brain volume may differ from main.
    without_brain = re.sub(rb"(?m)^  zenas_brain_data:\n(?:    .*\n)*", b"", section)
    assert without_brain == original[region].encode()
    data = "za_agent_data" if region == "za" else "agent_zenas_data"
    config = yaml.safe_load(text)
    for service in ("agent-zenas", "pool-loader"):
        assert config["services"][service]["volumes"] == [data + ":/data"]
    assert config["services"]["zenas-brain"]["volumes"] == ["zenas_brain_data:/data"]
    assert not config["services"]["zenas-sandbox"].get("volumes")


@pytest.mark.parametrize("region", FILES)
@pytest.mark.parametrize("profile", [False, True])
def test_real_compose_selects_only_existing_room_unless_profile_enabled(region, profile):
    binary = shutil.which("docker-compose")
    command = [binary] if binary else [shutil.which("docker"), "compose"]
    assert command[0], "Real Compose CLI is required"
    command += ["-f", str(ROOT / "agents/zenas" / FILES[region]), "--env-file", "/dev/null"]
    if profile:
        command += ["--profile", "brain"]
    result = subprocess.run([*command, "config", "--services"], capture_output=True, text=True,
        env={"PATH": os.defpath, "TANK_REGION": region, "AGENT_DB_PASSWORD_ZENAS": "placeholder-database"}, timeout=30)
    assert result.returncode == 0, result.stderr
    expected = {"agent-zenas"} | ({"zenas-brain", "zenas-sandbox"} if profile else set())
    assert set(result.stdout.splitlines()) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("region", FILES)
async def test_standby_cannot_dispatch_any_matter(monkeypatch, region):
    monkeypatch.setenv("BRAIN_ROOM", "zenas")
    monkeypatch.setenv("TANK_REGION", region)
    app = load("placeholder_zenas_standby", ROOT / "agents/_brain/standby.py").app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://placeholder.invalid") as client:
        assert (await client.get("/health")).json() == {"ok": True, "room": "zenas", "hermes_version": "0.22.2"}
        for prompt in ("commercial lease", "criminal defence bail application"):
            assert (await client.post("/tasks", json={"text": prompt})).status_code == 404
    assert not any("POST" in getattr(route, "methods", {}) for route in app.routes)


@pytest.fixture
def zenas(corridor, monkeypatch):
    key = "placeholder-zenas-key"
    monkeypatch.setenv("TANK_KEY_ZENAS", key)
    corridor.keys["zenas"] = key
    corridor.tank.box_region = Mock(return_value="us-east")
    corridor.tank._pick_version = AsyncMock(return_value="1.2.1")
    corridor.tank.get_package = Mock(return_value=SimpleNamespace(path=ROOT / "tank/skills/zenas.scope/1.2.1"))
    corridor.pool.fetch = AsyncMock(return_value=[])
    return corridor


async def request(zenas, *, region="us-east", home="us-east", key="placeholder-zenas-key", messages=None, **body):
    zenas.tank.box_region.return_value = region
    headers = {"X-Tank-Key": key, "X-Tenant-ID": str(TENANT)}
    if home is not None:
        headers["X-Home-Region"] = home
    payload = {"messages": messages or [{"role": "user", "content": "commercial lease"}], **body}
    async with zenas.client(transport=httpx.ASGITransport(app=zenas.app), base_url="http://placeholder.invalid") as client:
        return await client.post(zenas.app.url_path_for("chat", room="zenas"), json=payload, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("region", FILES)
async def test_owning_key_keeps_local_region_guards_and_receipts(zenas, region):
    response = await request(zenas, region=region, home=region)
    assert response.status_code == 200, response.text
    zenas.tank._pick_version.assert_awaited_once_with("zenas.scope", False)
    zenas.tank.load_policy.assert_awaited_once_with("agent.zenas.think", TENANT)
    assert len(zenas.sent) == 1 and len(zenas.pool.rows) == 3
    assert zenas.tank.model_call_refusal.call_args.kwargs["box"] == region
    assert zenas.tank.model_call_refusal.call_args.kwargs["caller"] == "zenas"
    assert zenas.pool.rows[0][1][13:15] == ("zenas", "agent.zenas.think")
    assert response.json()["receipt"]["region"] == region


@pytest.mark.asyncio
@pytest.mark.parametrize("region,home", [("za", "us-east"), ("us-east", "za"), ("za", None), ("us-east", None), ("unknown", "unknown")])
async def test_home_region_mismatch_cannot_reach_sql_or_gateway(zenas, region, home):
    assert (await request(zenas, region=region, home=home)).status_code == 403
    zenas.tank._pick_version.assert_not_awaited()
    assert not zenas.pool.rows and not zenas.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "placeholder-base-key", "placeholder-rig-key", "placeholder-tiger-key", "placeholder-workspace-key"])
async def test_foreign_keys_never_reach_zenas_scope_or_model(zenas, key):
    assert (await request(zenas, key=key)).status_code == 401
    zenas.tank._pick_version.assert_not_awaited()
    assert not zenas.pool.rows and not zenas.sent


@pytest.mark.asyncio
async def test_za_rejects_boston_room_key(zenas, monkeypatch):
    monkeypatch.setenv("TANK_KEY_ZENAS", "placeholder-zenas-za-key")
    assert (await request(zenas, region="za", home="za")).status_code == 401
    zenas.tank._pick_version.assert_not_awaited()
    assert not zenas.pool.rows and not zenas.sent
    assert (await request(zenas, region="za", home="za", key="placeholder-zenas-za-key")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("region", FILES)
@pytest.mark.parametrize("role", ["user", "assistant", "tool", "developer", "system"])
async def test_criminal_request_is_refused_before_policy_or_gateway(zenas, region, role):
    response = await request(zenas, region=region, home=region,
        messages=[{"role": "user", "content": "commercial lease"}, {"role": role, "content": "criminal defence bail application"}])
    assert response.status_code == 403 and response.json()["detail"] == REFUSED
    zenas.tank.load_policy.assert_not_awaited()
    assert not zenas.pool.rows and not zenas.sent


@pytest.mark.asyncio
async def test_criminal_tool_arguments_and_definitions_cannot_bypass_scope(zenas):
    calls = [{"id": "placeholder-call", "function": {"name": "skala_run", "arguments": json.dumps({"text": "bail application"})}}]
    response = await request(zenas, messages=[{"role": "user", "content": "commercial lease"}, {"role": "assistant", "content": None, "tool_calls": calls}])
    assert response.status_code == 403 and response.json()["detail"] == REFUSED
    tools = [{"type": "function", "function": {"name": "placeholder", "description": "criminal defence", "parameters": {"type": "object"}}}]
    response = await request(zenas, tools=tools)
    assert response.status_code == 403 and response.json()["detail"] == REFUSED
    assert not zenas.pool.rows and not zenas.sent


@pytest.mark.asyncio
async def test_existing_room_refusal_precedes_every_followup_tool():
    tank = SimpleNamespace(run_skill=AsyncMock(side_effect=AssertionError("criminal matter reached a tool")))
    result = await consider(tank, tenant_id=str(TENANT), text="criminal defence bail application", jurisdiction="za",
        follow=[{"skill": "zenas.draft", "input": {"text": "placeholder"}}])
    assert result["stopped"] is True and result["scope"]["reason"] == REFUSED
    assert result["outputs"] == []
    tank.run_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_out_of_scope_cannot_fall_back_to_model_and_pause_still_wins(zenas):
    assert (await request(zenas, messages=[{"role": "user", "content": "placeholder unrelated"}])).status_code == 403
    zenas.tank.load_policy.assert_not_awaited()
    zenas.pool.paused = True
    assert (await request(zenas)).status_code == 503
    assert len(zenas.pool.rows) == 1 and "tank.control_log" in zenas.pool.rows[0][0]
    zenas.pool.rows.clear()
    zenas.pool.paused = False
    zenas.tank.model_call_refusal.return_value = "placeholder model refusal"
    assert (await request(zenas)).status_code == 403
    assert not zenas.pool.rows and not zenas.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("region", FILES)
async def test_skills_export_is_owning_room_and_candidates_remain_base_only(zenas, region):
    zenas.tank.box_region.return_value = region
    async with zenas.client(transport=httpx.ASGITransport(app=zenas.app), base_url="http://placeholder.invalid") as client:
        headers = {"X-Tank-Key": zenas.keys["zenas"], "X-Home-Region": region}
        response = await client.get(zenas.app.url_path_for("approved", room="zenas"), params={"tenant_id": str(TENANT), "home_region": region}, headers=headers)
        assert response.status_code == 200 and response.json()["room"] == "zenas"
        assert zenas.pool.fetch.call_args.args[1:] == (TENANT, region, "zenas")
        body = {"tenant_id": str(TENANT), "home_region": region, "skill_id": "base.learned.placeholder", "version": "1.0.0",
                "description": "placeholder", "instructions": "placeholder", "run_py": "async def run(input, ctx): return input",
                "cases": [{"input": {}, "expected": {}}]}
        refused = await client.post(zenas.app.url_path_for("candidate", room="zenas"), json=body, headers=headers)
        assert refused.status_code == 403


def zenas_topology(region):
    names, rows, networks = topology("zenas")
    names = names[:3]
    rows = {name: rows[name] for name in names}
    networks = {key: value for key, value in networks.items() if not key.endswith("_browser")}
    for value in networks.values():
        value["Containers"] = {key: item for key, item in value["Containers"].items() if key in rows}
    for name in names[:2]:
        rows[name]["Config"]["Env"] = ["TANK_REGION=" + region]
    own = "za" if region == "za" else "us"
    rows[names[1]]["Mounts"] = [{"Type": "volume", "Destination": "/data", "Name": "tervory-zenas-" + own + "-brain-data"}]
    return names, rows, networks


@pytest.mark.parametrize("region", FILES)
def test_isolation_checker_accepts_zenas_on_each_box(monkeypatch, region):
    gate = load("placeholder_zenas_isolation", ROOT / "agents/base/check_brain_isolation.py")
    names, rows, networks = zenas_topology(region)
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
    assert any("http://zenas-sandbox:8002/exec" in call[-1] for call in calls if call[0] == "exec")


@pytest.mark.parametrize("fault", ["region", "volume", "network"])
def test_za_isolation_refuses_boston_resources(monkeypatch, fault):
    gate = load("placeholder_cross_box_isolation", ROOT / "agents/base/check_brain_isolation.py")
    names, rows, networks = zenas_topology("za")
    if fault == "region":
        rows[names[1]]["Config"]["Env"] = ["TANK_REGION=us-east"]
    elif fault == "volume":
        rows[names[1]]["Mounts"][0]["Name"] = "tervory-zenas-us-brain-data"
    else:
        rows[names[1]]["NetworkSettings"]["Networks"]["placeholder-boston"] = {}
    def docker(*args):
        assert args[0] != "exec", "cross-box resources reached execution"
        return json.dumps([rows[args[1]] if args[0] == "inspect" else networks[args[2]]])
    monkeypatch.setattr(gate, "docker", docker)
    with pytest.raises(AssertionError):
        gate.main(names)


@pytest.mark.parametrize("region", ["us", "za"])
def test_seal_fixture_covers_both_boxes(tmp_path, region):
    result = run_seal_check(tmp_path, "zenas_" + region + "_sealed.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "agent-zenas" in result.stdout and "sealed" in result.stdout


@pytest.mark.parametrize("region", ["us", "za"])
@pytest.mark.parametrize("service", ["zenas-brain", "zenas-sandbox"])
@pytest.mark.parametrize("fault", ["extra_internal", "extra_outward", "missing", "outward_sandbox"])
def test_seal_refuses_wrong_network_or_partial_pair(tmp_path, region, service, fault):
    data = json.loads((FIXTURES / ("zenas_" + region + "_sealed.json")).read_text())
    target = data["containers"][service]
    if fault == "missing":
        del data["containers"][service]
    elif fault == "outward_sandbox":
        data["networks"][target["project"] + "_sandbox"] = False
    else:
        target["networks"].append("placeholder-foreign")
        data["networks"]["placeholder-foreign"] = fault == "extra_internal"
    path = tmp_path / "placeholder-zenas-leak.json"
    path.write_text(json.dumps(data))
    result = run_seal_check(tmp_path, str(path))
    assert result.returncode == 1, result.stdout + result.stderr
