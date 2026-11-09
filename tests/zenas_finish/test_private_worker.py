"""The packaged private worker stays off and refuses invalid work before Tank."""
import importlib

import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("configuration", ["unset", "missing_database", "missing_key"])
async def test_worker_starts_without_enabling_work(monkeypatch, configuration):
    from agents.zenas.core.brain_service import create_app
    for name in ("ZENAS_BRAIN_ENABLED", "DATABASE_URL", "TANK_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TANK_REGION", "us-east")
    if configuration != "unset":
        monkeypatch.setenv("ZENAS_BRAIN_ENABLED", "true")
        monkeypatch.setenv("TANK_KEY" if configuration == "missing_database" else "DATABASE_URL", "placeholder")
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http:
            response = await http.get("/health")
    assert response.status_code == 200
    missing = {"unset": [], "missing_database": ["DATABASE_URL"], "missing_key": ["TANK_KEY"]}[configuration]
    assert response.json() == {"ok": True, "room": "zenas", "enabled": configuration != "unset",
                               "ready": False, "missing_settings": missing}
    assert app.state.tank is None and app.state.pool is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change,expected", [({}, 409), ({"task_key": "moot.judge"}, 422),
    ({"task_key": "tiger.review"}, 422), ({"input": {"messages": []}}, 422),
    ({"run_id": "not-a-run"}, 422), ({"provider": "unapproved"}, 422)])
async def test_disabled_or_invalid_worker_never_dispatches(monkeypatch, change, expected):
    from agents.zenas.core.brain_service import create_app
    monkeypatch.delenv("ZENAS_BRAIN_ENABLED", raising=False)
    app = create_app()
    body = {"task_key": "zenas.rule_card", "input": {"messages": [{"role": "user", "content": "contract"}]},
            "recall": [], "run_id": "11111111-1111-4111-8111-111111111111", **change}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http:
            response = await http.post("/tasks?tenant_id=11111111-1111-4111-8111-111111111111&user_id=placeholder", json=body)
    assert response.status_code == expected
    assert app.state.tank is None


@pytest.mark.asyncio
async def test_moot_keeps_its_existing_tank_route_when_zenas_worker_is_enabled(monkeypatch):
    from agents.zenas.zenas_brain import brain_then_route
    monkeypatch.setenv("ZENAS_BRAIN_ENABLED", "true")
    class Tank:
        async def delegate_task(self, *args):
            pytest.fail("Moot must not enter the Zenas private worker")

        async def route(self, tenant_id, task_type, messages):
            assert task_type == "moot.judge"
            return {"content": "placeholder existing Moot result"}
    response = await brain_then_route(None, Tank(), tenant_id="11111111-1111-4111-8111-111111111111",
        task_type="moot.judge", messages=[{"role": "user", "content": "placeholder"}], actor=object())
    assert response["content"] == "placeholder existing Moot result"


def test_opt_in_configuration_selects_the_packaged_worker():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[2]
    # #796 is now the actual configuration; test it without replaying its
    # historical proposal or modifying the source/deployment checkout.
    proposed = root / 'agents/zenas'
    for name, region in (("docker-compose.yml", "us-east"), ("docker-compose.za.yml", "za")):
        service = yaml.safe_load((proposed / name).read_text())["services"]["zenas-brain"]
        module, attribute = service["command"][1].split(":")
        assert module == "agents.zenas.core.brain_service" and attribute == "app"
        assert getattr(importlib.import_module(module), attribute) is not None
        assert service["profiles"] == ["brain"]
        assert service["environment"]["ZENAS_BRAIN_ENABLED"] == "${ZENAS_BRAIN_ENABLED:-false}"
        assert service["environment"]["TANK_REGION"] == region
        assert service["read_only"] and service["cap_drop"] == ["ALL"]
        assert service["build"] == {"context": "..", "dockerfile": "zenas/Dockerfile"}
    docker = (root / "agents/zenas/Dockerfile").read_text()
    assert "COPY zenas/core /app/agents/zenas/core" in docker
