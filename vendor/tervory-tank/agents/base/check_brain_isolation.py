"""Deployment gate for per-room brain and private-tool topologies.

Run on the authorized cloud Docker host, never on a laptop:
python agents/base/check_brain_isolation.py ROOM BRAIN SANDBOX [BROWSER GATEWAY]
"""
import json
import subprocess
import sys
import shlex
import re


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True)


def sandbox_probe_script(sandbox_host="base-sandbox"):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", sandbox_host):
        raise ValueError("Invalid sandbox host")
    scratch = "blocked=False\ntry:\n import socket\n socket.socket()\nexcept (ImportError,OSError): blocked=True\nassert blocked\nfrom pathlib import Path\nPath('proof').write_text('offline')\nprint('OFFLINE')"
    sandbox_check = "import json,os,urllib.request,uuid\ncommand=" + repr("python -c " + shlex.quote(scratch)) + "\n" + """body = json.dumps({'tenant_id':str(uuid.uuid4()),'task_id':str(uuid.uuid4()),'command':command}).encode()
request = urllib.request.Request(SANDBOX_URL,data=body,headers={'Content-Type':'application/json','X-Sandbox-Key':os.environ['SANDBOX_KEY']})
result = json.load(urllib.request.urlopen(request,timeout=35))
assert result['exit_code']==0 and 'OFFLINE' in result['output'], result
"""
    return sandbox_check.replace("SANDBOX_URL", repr("http://" + sandbox_host + ":8002/exec"))


def main(names):
    if len(names) not in {3, 4, 5}:
        raise SystemExit("Supply ROOM BRAIN SANDBOX and optional BROWSER [GATEWAY] container names")
    rows = [json.loads(docker("inspect", name))[0] for name in names]
    room, brain, sandbox = rows[:3]
    for row in rows:
        assert row["State"].get("Health", {}).get("Status") == "healthy", "service is not healthy"
        config = row["HostConfig"]
        assert not config.get("Privileged"), "privileged room service"
        assert not config.get("CapAdd"), "room service adds capabilities"
        assert config.get("NetworkMode") != "host" and not str(config.get("NetworkMode", "")).startswith("container:"), "shared host/container network"
        assert config.get("PidMode", "") in {"", "private"}, "shared PID namespace"
        assert not row["HostConfig"]["PortBindings"], "room service publishes a host port"
        assert all(m["Type"] == "volume" or (m["Type"] == "tmpfs" and m["Destination"] in {"/tmp", "/work"}) for m in row["Mounts"]), "unexpected host mount"
        keys = {v.split("=", 1)[0] for v in row["Config"]["Env"]}
        assert not keys & {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "LITELLM_MASTER_KEY"}, "model key in room"
    service = room["Config"].get("Labels", {}).get("com.docker.compose.service")
    room_name = {"agent-base": "base", "agent-tiger": "tiger", "agent-zenas": "zenas", "agent-market-lab": "market_lab", "agent-radix": "radix", "agent-tisan": "tisan", "agent-trigger": "trigger", "agent-ghost": "ghost"}.get(service)
    assert room_name is not None, "unsupported room"
    stem = room_name.replace("_", "-")
    provisioned = room_name in {"market_lab", "radix", "tisan", "trigger", "ghost"}
    assert len(rows) == (3 if room_name == "zenas" else 4 if room_name == "ghost" else 5), "room tool topology mismatch"
    assert brain["Config"].get("Labels", {}).get("com.docker.compose.service") == stem + "-brain", "brain belongs to another room"
    if room_name == "zenas":
        region = next((v.partition("=")[2] for v in room["Config"]["Env"] if v.startswith("TANK_REGION=")), "")
        brain_region = next((v.partition("=")[2] for v in brain["Config"]["Env"] if v.startswith("TANK_REGION=")), "")
        assert region in {"us-east", "za"} and brain_region == region, "cross-region brain"
        assert brain["HostConfig"].get("ReadonlyRootfs"), "brain root must be read-only"
        volume_name = "tervory-zenas-" + ("za" if region == "za" else "us") + "-brain-data"
        assert [m.get("Name") for m in brain["Mounts"] if m["Type"] == "volume"] == [volume_name], "brain volume belongs to another region"
    if provisioned:
        assert brain["HostConfig"].get("ReadonlyRootfs"), "brain root must be read-only"
        assert "ALL" in brain["HostConfig"].get("CapDrop", []), "brain must drop all capabilities"
        assert any("no-new-privileges" in option for option in brain["HostConfig"].get("SecurityOpt", [])), "brain can gain privileges"
        assert [m.get("Name") for m in brain["Mounts"] if m["Type"] == "volume"] == ["tervory-" + stem + "-brain-data"], "brain mounts room/foreign data"
        keys = {v.split("=", 1)[0] for v in brain["Config"]["Env"]}
        assert keys <= {"BRAIN_ROOM", "TANK_URL", "TANK_KEY", "TANK_REGION", "HERMES_URL", "SANDBOX_KEY", "DATA_DIR", "PATH", "LANG", "PYTHON_VERSION", "PYTHON_SHA256", "GPG_KEY", "PYTHONPATH", "PYTHONUNBUFFERED", "HERMES_BUNDLED_SKILLS", "HERMES_OPTIONAL_SKILLS", room_name.upper() + "_URL", room_name.upper() + "_BROWSER_URL", room_name.upper() + "_SANDBOX_URL"}, "unexpected brain setting"
    corridor_name = "tervory-gw-" + room_name
    sandbox_host = sandbox["Config"].get("Labels", {}).get("com.docker.compose.service")
    assert sandbox_host == stem + "-sandbox", "sandbox belongs to another room"
    sandbox_keys = {value.split("=", 1)[0] for value in sandbox["Config"]["Env"]}
    assert "TANK_KEY" not in sandbox_keys, "Tank key in sandbox"
    assert sandbox["HostConfig"].get("ReadonlyRootfs"), "sandbox root must be read-only"
    assert "ALL" in sandbox["HostConfig"].get("CapDrop", []), "sandbox must drop all capabilities"
    assert any("no-new-privileges" in option for option in sandbox["HostConfig"].get("SecurityOpt", [])), "sandbox can gain privileges"
    assert not any(m["Type"] == "volume" for m in sandbox["Mounts"]), "sandbox must not mount room/session data"
    assert "/work" in sandbox["HostConfig"].get("Tmpfs", {}), "sandbox lacks isolated tmpfs work"
    memberships = [set(row["NetworkSettings"]["Networks"]) for row in rows]
    project = room["Config"].get("Labels", {}).get("com.docker.compose.project", "")
    room_extra = {"radix": {project + "_proxy-link"}, "tisan": {project + "_figma_internal"}, "trigger": {project + "_checkers"}}.get(room_name, set())
    assert memberships[0] == {corridor_name} | room_extra, "room corridor mismatch"
    sandbox_net, = memberships[2]
    assert memberships[1] == {corridor_name, sandbox_net}
    private = [(sandbox_net, {brain["Id"], sandbox["Id"]})]
    if len(rows) == 5:
        browser, gateway = rows[3:]
        assert browser["Config"].get("Labels", {}).get("com.docker.compose.service") == stem + "-browser", "browser belongs to another room"
        assert gateway["Config"].get("Labels", {}).get("com.docker.compose.service") == stem + "-browser-gateway", "gateway belongs to another room"
        if provisioned:
            assert [m.get("Name") for m in browser["Mounts"] if m["Type"] == "volume"] == ["tervory-" + stem + "-browser-data"], "browser shares room/foreign data"
            assert "TANK_KEY" not in {v.split("=", 1)[0] for v in browser["Config"]["Env"]}, "Tank key in browser"
        browser_net, = memberships[3]
        assert sandbox_net != browser_net
        assert memberships[4] == {corridor_name, browser_net}
        private.append((browser_net, {browser["Id"], gateway["Id"]}))
    if len(rows) == 4:
        browser = rows[3]
        assert browser["Config"].get("Labels", {}).get("com.docker.compose.service") == "ghost-browser", "Watch browser belongs to another room"
        assert memberships[3] == {corridor_name}, "Watch browser corridor mismatch"
        brain_volumes = {m.get("Name") for m in brain["Mounts"] if m["Type"] == "volume"}
        assert not brain_volumes & {m.get("Name") for m in browser["Mounts"] if m["Type"] == "volume"}, "Watch browser shares brain data"
    for network, members in private:
        info = json.loads(docker("network", "inspect", network))[0]
        assert info["Internal"], "private network has external egress"
        assert set(info["Containers"]) == members, "another service joined Base's private tools"
    own = {row["Id"] for row in rows}
    corridor = json.loads(docker("network", "inspect", corridor_name))[0]
    for identity in corridor["Containers"]:
        if identity in own:
            continue
        peer = json.loads(docker("inspect", identity))[0]
        service = peer["Config"].get("Labels", {}).get("com.docker.compose.service")
        assert service in {"tank", "postgres", "hermes", "searxng"} | ({"passive-egress"} if room_name == "radix" else set()), "unexpected corridor neighbour"
    room_script = '''import asyncio, os, asyncpg
async def run():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    assert await conn.fetchval('SELECT current_user') == 'agent_base'
    assert not await conn.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
    for sql in ['SELECT 1 FROM tank.ledger LIMIT 1', 'SELECT 1 FROM base.records LIMIT 1']:
        try:
            await conn.execute(sql)
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError('Base can read another schema')
    await conn.close()
asyncio.run(run())
'''
    room_script = room_script.replace("'agent_base'", repr("agent_" + room_name)).replace("Base can read", room_name.title() + " can read")
    if room_name != "ghost":
        docker("exec", names[0], "python", "-c", room_script)
    proof = "import socket; s=socket.socket(); s.settimeout(3); assert s.connect_ex(('1.1.1.1',443)) != 0"
    sandbox_check = sandbox_probe_script(sandbox_host)
    docker("exec", names[1], "python", "-c", sandbox_check)
    if len(rows) == 5:
        gateway_address = gateway["NetworkSettings"]["Networks"][corridor_name]["IPAddress"]
        docker("exec", names[1], "python", "-c", "import socket; s=socket.socket(); s.settimeout(3); assert s.connect_ex(('" + gateway_address + "',3128)) != 0")
    docker("exec", names[2], "python", "-c", proof)
    if len(rows) == 5:
        docker("exec", names[3], "python", "-c", proof)
    detail = "topology, private tools, volumes, keys and denied sandbox egress" if room_name == "ghost" else "topology, private tools, volumes, keys, database role and denied direct egress"
    print("PASS " + room_name.title() + " " + detail)


if __name__ == "__main__":
    main(sys.argv[1:])
