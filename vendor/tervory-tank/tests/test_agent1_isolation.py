"""Room memory, skills and network boundaries without model or browser downloads."""
import asyncio
import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("brain", ROOT / "agents/_brain/__init__.py", submodule_search_locations=[str(ROOT / "agents/_brain")])
brain = importlib.util.module_from_spec(spec)
sys.modules["brain"] = brain
spec.loader.exec_module(brain)

from brain import browser, runner, security, service, sandbox  # noqa: E402


def _base_brain():
    """agents/base/base_brain.py, loaded once. Tests used to rely on an earlier test in
    this file having loaded it, so they failed with KeyError when run alone or reordered."""
    module = sys.modules.get("base_brain")
    if module is None:
        spec = importlib.util.spec_from_file_location("base_brain", ROOT / "agents/base/base_brain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def test_browser_and_sandbox_have_no_direct_egress_or_cross_room_network():
    compose = yaml.safe_load((ROOT / "agents/base/docker-compose.yml").read_text())
    assert compose["networks"]["browser"]["internal"] is True
    assert compose["networks"]["sandbox"]["internal"] is True
    assert compose["services"]["base-browser"]["networks"] == ["browser"]
    assert compose["services"]["base-sandbox"]["networks"] == ["sandbox"]
    assert compose["services"]["base-browser"]["volumes"] == ["base_browser_data:/browser-data"]
    assert "TIGER_URL" not in compose["services"]["agent-base"]["environment"]
    keyed = {name for name, item in compose["services"].items() if "SANDBOX_KEY" in item.get("environment", {})}
    assert keyed == {"base-brain", "base-sandbox"}
    sandbox = compose["services"]["base-sandbox"]
    assert sandbox["read_only"] is True and sandbox["cap_drop"] == ["ALL"]
    assert not sandbox.get("volumes") and any(str(m).startswith("/work:") for m in sandbox["tmpfs"])
    main = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "tervory-gw-base" not in main["services"]["tiger"]["networks"]
    assert "tervory-gw-tiger" in main["services"]["tiger"]["networks"]
    for item in compose["services"].values():
        assert not item.get("privileged") and not item.get("cap_add")
        assert item.get("network_mode") not in {"host"} and not str(item.get("network_mode", "")).startswith(("container:", "service:"))
        assert item.get("pid", "") in {"", "private"}
        assert not any("docker.sock" in v for v in item.get("volumes", []))
        assert not item.get("ports")
        assert not any(k in item.get("environment", {}) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://127.0.0.1", "http://169.254.169.254", "https://www.atf.gov:444", "https://u:p@www.atf.gov", "https://localhost"])
def test_public_web_blocks_unsafe_urls(url):
    with pytest.raises(ValueError):
        security.public_url(url)


def test_any_public_site_has_no_host_allowlist():
    assert security.public_url("https://independent-dealer.example") == ("independent-dealer.example", 443)
    assert security.public_url("http://another-business.example") == ("another-business.example", 80)


@pytest.mark.asyncio
async def test_dns_private_address_is_refused_even_with_public_hostname(monkeypatch):
    async def addresses(*args, **kwargs):
        return [(2, 1, 6, "", ("169.254.169.254", 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", addresses)
    with pytest.raises(ValueError, match="Private"):
        await security.public_destination("https://public-name.example")


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "fc00::1", "224.0.0.1"])
def test_proxy_refuses_private_metadata_and_multicast(address):
    assert not security.public_address(address)


def test_human_check_is_recorded():
    assert browser.human_check('<div class="g-recaptcha">verify you are human</div>')
    assert not browser.human_check("A public business page")


def test_retired_and_candidate_skills_cannot_enter_hermes(tmp_path):
    tenant = str(uuid.uuid4())
    bundle = {"tenant_id": tenant, "room": "base", "region": "us-east", "skills": [{
        "id": "base.normalize", "version": "1.0.0", "name": "base-normalize", "state": "candidate", "agent": "base", "files": {"SKILL.md": "candidate"}}]}
    with pytest.raises(RuntimeError, match="Unapproved"):
        runner.install_skills(tmp_path, bundle, tenant)
    bundle["skills"][0]["state"] = "approved"
    runner.install_skills(tmp_path, bundle, tenant)
    assert len(list((tmp_path / "skills").glob("*/SKILL.md"))) == 1
    bundle["skills"] = []
    runner.install_skills(tmp_path, bundle, tenant)
    assert list((tmp_path / "skills").glob("*/SKILL.md")) == []


@pytest.mark.asyncio
async def test_recall_precedes_work_and_remember_closes_task(monkeypatch, tmp_path):
    tenant, task = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TANK_REGION", "us-east")
    events = []

    async def memory(client, path, payload):
        assert payload["tenant_id"] == str(tenant)
        events.append(path)
        if path == "/recall":
            return {"memories": [{"id": str(i), "tenant_id": str(tenant), "text": "old evidence " * 500, "meta": "x" * 5000} for i in range(8)]}
        assert payload["source"] == "base:task:" + str(task)
        return {"id": "saved"}

    class Child:
        returncode = 0
        async def wait(self):
            return 0

    async def child(*args, **kwargs):
        assert events == ["/recall"]
        assert str(tenant) in kwargs["env"]["HERMES_HOME"]
        assert "OPENAI_API_KEY" not in kwargs["env"]
        directory = Path(args[-1])
        recalled=json.loads((directory/"request.json").read_text())["memories"]
        assert len(recalled)==8 and all(len(m["text"])<=1000 and set(m)=={"id","text"} for m in recalled)
        (directory / "receipt.json").write_text(json.dumps({"tenant_id": str(tenant), "task_id": str(task),
            "steps": [{"action": "pipeline_run", "status": "done"}], "model_receipts": []}))
        events.append("work")
        return Child()

    monkeypatch.setattr(service, "memory", memory)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", child)
    receipt = await service.run_task(service.Run(tenant_id=tenant, task_id=task, home_region="us-east", batch="public pilot", count=1))
    assert events == ["/recall", "work", "/memories"]
    assert [s["action"] for s in receipt["steps"]] == ["recall", "pipeline_run", "remember"]


@pytest.mark.asyncio
async def test_foreign_memory_never_reaches_work(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    async def memory(*args):
        return {"memories": [{"tenant_id": str(uuid.uuid4()), "id": "foreign", "text": "private"}]}
    monkeypatch.setattr(service, "memory", memory)
    with pytest.raises(RuntimeError, match="tenant boundary"):
        await service.run_task(service.Run(tenant_id=uuid.uuid4(), task_id=uuid.uuid4(), home_region="us-east", batch="public pilot", count=1))


@pytest.mark.asyncio
async def test_panel_receipt_query_is_tenant_scoped():
    module = _base_brain()
    tenant, task = uuid.uuid4(), uuid.uuid4()
    receipt = {"tenant_id": str(tenant), "task_id": str(task), "steps": [{"action": "remember"}]}

    class Connection:
        async def fetch(self, sql, *args):
            assert "WHERE tenant_id=$1" in sql and args == (tenant,)
            return [{"id": task, "status": "done", "error": None, "batch": "public pilot", "receipt": json.dumps(receipt)}]

    batches = await module.batch_receipts(Connection(), tenant)
    assert batches["public pilot"][0]["receipt"] == receipt


@pytest.mark.asyncio
async def test_pipeline_defers_paid_calls_until_recovery_finishes(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    import httpx
    from fastapi import FastAPI
    module = _base_brain()
    tenant, task = uuid.uuid4(), uuid.uuid4()
    key = uuid.uuid4().hex
    monkeypatch.setenv("TANK_KEY", key)
    calls = []

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield self
        async def execute(self, sql, *args):
            assert args[:2] == (tenant, task)
        async def fetch(self, sql, *args):
            assert args == (tenant, task)
            return []
        async def fetchrow(self, sql, *args):
            return {"status": "done", "result": "fixture"}

    async def call(skill_id, payload):
        calls.append(payload)
        return {}

    async def run(pool, task_id, cfg, callback):
        await callback("base.normalize", {"name": "public"})

    monkeypatch.setitem(sys.modules, "room", SimpleNamespace(_run_batch=run))
    module.active[(tenant, task)] = module.ActiveBatch(Pool(), {}, call)
    app = FastAPI()
    module.register(app)
    payload = {"tenant_id": str(tenant), "task_id": str(task)}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://base") as client:
            headers = {"X-Tank-Key": key}
            early = await client.post("/brain/pipeline", headers=headers, json={**payload, "finish": True})
            assert early.status_code == 409 and calls == []
            assert (await client.post("/brain/pipeline", headers=headers, json=payload)).status_code == 202
            await module.active[(tenant, task)].job
            assert (await client.get("/brain/pipeline", headers=headers, params=payload)).json()["pipeline_status"] == "done"
            assert calls == [{"name": "public", "_base_brain_free": True}]
            assert (await client.post("/brain/pipeline", headers=headers, json={**payload, "finish": True})).status_code == 202
            await module.active[(tenant, task)].job
            assert (await client.get("/brain/pipeline", headers=headers, params={**payload, "finish": True})).json()["pipeline_status"] == "done"
            assert calls[-1] == {"name": "public"}
            count = len(calls)
            assert (await client.post("/brain/pipeline", headers=headers, json={**payload, "finish": True})).status_code == 200
            assert len(calls) == count  # a lost start reply cannot rerun paid work
    finally:
        module.active.pop((tenant, task))


@pytest.mark.asyncio
async def test_rerun_replaces_thinking_subtotal_without_double_counting(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    module = _base_brain()
    tenant, task = uuid.uuid4(), uuid.uuid4()
    receipt = {"tenant_id": str(tenant), "task_id": str(task), "status": "done",
               "model_receipts": [{"ledger_id": str(uuid.uuid4()), "cost_usd": 0.15}]}
    monkeypatch.setenv("BASE_BRAIN_URL", "http://base-brain")
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: receipt)
    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield self
        async def execute(self, sql, *args):
            assert args[:2] == (tenant, task)
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    cfg = {"tenant_id": str(tenant), "batch": "public", "count": 1, "model_spend": 0.4, "brain_spend": 0.1}
    await module.drive_batch(Pool(), SimpleNamespace(_headers=lambda: {}), task, cfg, None)
    assert cfg["model_spend"] == pytest.approx(0.45)
    assert cfg["brain_spend"] == 0.15


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["down", "refused", "paused"])
async def test_brain_failure_before_pipeline_uses_existing_pipeline(monkeypatch, failure):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    import httpx
    module = _base_brain()
    tenant, task = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setenv("BASE_BRAIN_URL", "http://base-brain")
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            if failure == "down": raise httpx.ConnectError("fixture")
            return SimpleNamespace(status_code=200, json=lambda: {"tenant_id":str(tenant),"task_id":str(task),
                "status":"failed","steps":[],"model_receipts":[]})
        async def delete(self, *args, **kwargs):
            calls.append("cancel")
    class Pool:
        @asynccontextmanager
        async def acquire(self): yield self
        async def execute(self, *args): pass
    async def run(pool, task_id, cfg, callback):
        calls.append("pipeline")
        assert cfg["brain_receipt"]["status"] == "fallback"
        assert module.active[(tenant,task)].operation == "fallback"
    monkeypatch.setattr(module.httpx,"AsyncClient",Client)
    monkeypatch.setitem(sys.modules,"room",SimpleNamespace(_run_batch=run))
    cfg={"tenant_id":str(tenant),"batch":"fixture","count":1}
    await module.drive_batch(Pool(),SimpleNamespace(_headers=lambda:{}),task,cfg,None)
    assert calls == ["cancel","pipeline"]


@pytest.mark.asyncio
async def test_retry_refuses_private_and_metadata_dns_before_record_write(monkeypatch):
    module = _base_brain()
    async def addresses(*args, **kwargs):
        return [(2,1,6,"",("10.0.0.1",443))]
    monkeypatch.setattr(asyncio.get_running_loop(),"getaddrinfo",addresses)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as refused:
        await module.check_website("https://public-looking.example")
    assert refused.value.status_code == 403



@pytest.mark.asyncio
async def test_submission_restore_quarantines_failure_without_breaking_startup(monkeypatch, caplog):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    import agent_skills
    writes=[]
    class Pool:
        @asynccontextmanager
        async def acquire(self): yield self
        async def fetch(self,*args):
            return [{'skill_id':'base.learned.fixture','version':'1.0.0','package':{},'sha256':'f'*64}]
        async def execute(self,sql,*args): writes.append((sql,args))
    def broken(*args): raise RuntimeError('private failure detail must not be logged')
    monkeypatch.setattr(agent_skills,'materialize',broken)
    await agent_skills.restore(SimpleNamespace(pool=lambda:Pool(),box_region=lambda:'us-east',_packages={}))
    assert any('SET present=false' in sql for sql,args in writes)
    assert not any("SET state=" in sql for sql,args in writes)
    assert 'restore failed' in caplog.text and 'private failure detail' not in caplog.text


def test_sandbox_accepts_python_scratch_only():
    from brain.sandbox_exec import python_source
    assert python_source("python -c 'print(1)'")=='print(1)'
    assert python_source("python - <<'CODE'\nprint(2)\nCODE")=='print(2)'
    with pytest.raises(ValueError): python_source('curl https://example.com')


@pytest.mark.parametrize("field,value", [("Privileged",True),("CapAdd",["SYS_ADMIN"]),
    ("NetworkMode","host"),("NetworkMode","container:other"),("PidMode","host"),("PidMode","container:other")])
def test_deployment_gate_rejects_privileges_and_shared_namespaces(monkeypatch,field,value):
    import copy
    spec=importlib.util.spec_from_file_location('base_gate',ROOT/'agents/base/check_brain_isolation.py')
    gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
    row={'State':{'Health':{'Status':'healthy'}},'HostConfig':{'Privileged':False,'CapAdd':[],
        'NetworkMode':'private','PidMode':'','PortBindings':{},'ReadonlyRootfs':True,'CapDrop':['ALL'],
        'SecurityOpt':['no-new-privileges'],'Tmpfs':{'/work':''}},'Mounts':[],'Config':{'Env':[]}}
    rows={name:copy.deepcopy(row) for name in ['room','brain','sandbox','browser','gateway']}
    rows['room']['HostConfig'][field]=value
    monkeypatch.setattr(gate,'docker',lambda *args:json.dumps([rows[args[1]]]))
    with pytest.raises(AssertionError): gate.main(list(rows))
    compile(gate.sandbox_probe_script(),'sandbox-probe','exec')


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['done', 'failed'])
async def test_brain_failure_after_free_pass_runs_paid_continuation(monkeypatch, status):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    module = _base_brain()
    tenant, task = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setenv('BASE_BRAIN_URL', 'http://base-brain')
    calls=[]
    class Pool:
        @asynccontextmanager
        async def acquire(self): yield self
        async def execute(self, sql, *args):
            if 'status=$4' in sql: calls.append(('persist',args[3]))
    async def free_pass():
        await asyncio.sleep(.01)
        calls.append('free settled')
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            batch=module.active[(tenant,task)]
            batch.ran=True
            batch.job=asyncio.create_task(free_pass())
            return SimpleNamespace(status_code=503)
        async def delete(self, *args, **kwargs): calls.append('cancel')
    async def phase(batch, body):
        assert body.finish and batch.finished and calls==['cancel','free settled']
        calls.append('paid pass')
        batch.result={'pipeline_status':status,'summary':'continued'}
    monkeypatch.setattr(module.httpx,'AsyncClient',Client)
    monkeypatch.setattr(module,'run_phase',phase)
    cfg={'tenant_id':str(tenant),'batch':'fixture','count':20}
    action=module.drive_batch(Pool(),SimpleNamespace(_headers=lambda:{}),task,cfg,None)
    if status=='failed':
        with pytest.raises(RuntimeError,match='continuation'): await action
    else: await action
    assert calls==['cancel','free settled','paid pass',('persist',status)]
    assert cfg['brain_receipt']['status']==status
    assert cfg['brain_receipt']['steps'][-1]['action']=='pipeline_continue_fallback'
    assert (tenant,task) not in module.active


@pytest.mark.asyncio
async def test_twenty_unresolved_records_exclude_large_profiles_and_passes():
    from contextlib import asynccontextmanager
    module=_base_brain()
    tenant, task=uuid.uuid4(),uuid.uuid4()
    class Pool:
        @asynccontextmanager
        async def acquire(self): yield self
        async def fetch(self, sql, *args):
            assert args==(tenant,task) and 'LIMIT 20' in sql
            return [{'id':uuid.uuid4(),'raw':{'name':'Fixture business','large':'x'*50000},
                'profile':{'website':{'value':'https://example.com'},'large':'x'*50000},
                'passes':{'large':'x'*50000},'status':'done'} for _ in range(20)]
        async def fetchrow(self,*args):return {'status':'done','result':'20 processed'}
    result=await module.results(module.ActiveBatch(Pool(),{},None),module.Task(tenant_id=tenant,task_id=task))
    assert len(result['unresolved'])==20
    assert all(set(row)=={'id','name','website','status'} for row in result['unresolved'])
    assert result['unresolved'][0]['website']=='https://example.com'
    assert len(runner.tool_result(result))<8000
    assert len(runner.tool_result({'html':'x'*100000}))<=8000


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['auth','path','encoding','size'])
async def test_sandbox_auth_and_rejected_uploads_leave_no_run(monkeypatch,tmp_path,kind):
    import base64
    from fastapi import HTTPException
    key=uuid.uuid4().hex
    monkeypatch.setenv('SANDBOX_KEY',key)
    monkeypatch.setattr(sandbox,'ROOT',tmp_path)
    monkeypatch.setattr(sandbox,'gate',asyncio.Semaphore(1))
    files={'safe':base64.b64encode(b'initial').decode()}
    expected=401
    if kind=='path': files['../escape']='aA==';expected=422
    if kind=='encoding': files['bad']='%%%';expected=422
    if kind=='size': files['huge']=base64.b64encode(b'x'*2000001).decode();expected=413
    body=sandbox.Execute(tenant_id=uuid.uuid4(),task_id=uuid.uuid4(),command='python -c "pass"',files=files)
    with pytest.raises(HTTPException) as error:
        await sandbox.execute(body,uuid.uuid4().hex if kind=='auth' else key)
    assert error.value.status_code==expected
    assert not (tmp_path/str(body.tenant_id)/str(body.task_id)).exists()
    assert sandbox.gate._value==1
