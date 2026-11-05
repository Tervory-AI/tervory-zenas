import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from agents.zenas.core import configuration, operations

ROOT = Path(__file__).resolve().parents[2]


def new_room():
    spec = importlib.util.spec_from_file_location("placeholder_gate4_room", ROOT / "agents/zenas/app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", configuration.ROOM_SETTINGS)
async def test_room_starts_without_each_required_setting_and_blocks_work(monkeypatch, missing):
    for name in configuration.ROOM_SETTINGS:
        monkeypatch.setenv(name, "placeholder")
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("ZENAS_PII_KEY", "00" * 32)
    monkeypatch.delenv(missing)
    monkeypatch.delenv("CHASSIS_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("CHASSIS_RLS_ADMIN_ROLE", raising=False)
    room = new_room()
    startup = AsyncMock(side_effect=AssertionError("incomplete settings started workers"))
    monkeypatch.setattr(room, "_inner_lifespan", startup)
    async with room.app.router.lifespan_context(room.app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=room.app), base_url="http://placeholder"
    ) as client:
        for method, path in [("GET", "/health"), ("POST", "/matters"), ("POST", "/jobs"), ("GET", "/pool/status")]:
            response = await client.request(method, path, json={})
            assert response.status_code == 503
            assert response.json()["needs_settings"] == [missing]
            assert "placeholder" not in response.text
    startup.assert_not_called()


@pytest.mark.parametrize("name,value", [("TANK_REGION", "unknown"), ("ZENAS_PII_KEY", "placeholder")])
def test_invalid_settings_are_named_without_showing_values(monkeypatch, name, value):
    for key in configuration.ROOM_SETTINGS:
        monkeypatch.setenv(key, "placeholder")
    monkeypatch.setenv("TANK_REGION", "za")
    monkeypatch.setenv("ZENAS_PII_KEY", "00" * 32)
    monkeypatch.setenv(name, value)
    result = configuration.room_readiness()
    assert not result["ok"] and name in result["invalid_settings"]
    assert value not in json.dumps(result)


@pytest.mark.asyncio
async def test_all_saturday_commands_run_from_the_command_without_live_settings(monkeypatch):
    from agents.zenas.core.dry_run import COMMANDS
    monkeypatch.setenv("TANK_REGION", "us-east")
    for name in ("DATABASE_URL", "TANK_URL", "TANK_KEY", "TANK_API_KEY", "TRAINING_JOB_KEY", "HF_TOKEN", "HF_ORG"):
        monkeypatch.delenv(name, raising=False)
    result = await operations.execute(operations.parser().parse_args(["saturday", "--dry-run"]))
    assert result["ok"] and result["simulation"] and not result["live_ready"]
    assert result["launches"] == 0 and result["storage"] == "disposable"
    assert [row["command"] for row in result["results"]] == list(COMMANDS)
    assert all(row["ok"] for row in result["results"])
    training = next(row for row in result["results"] if row["command"] == "train")
    assert training["phase"] == "ready_to_launch" and training["launch_requested"] is False
    assert training["artifacts"]["qualification"]["min_cases"] == 200
    assert training["artifacts"]["config"] == "tank/training_jobs/train.py"
    assert "DATABASE_URL" in result["needs_settings"]
    assert all(request["path"] != "/v1/training/run" for request in result["requests"])
    assert "DATABASE_URL" not in __import__("os").environ


@pytest.mark.asyncio
async def test_za_saturday_command_runs_regional_jobs_and_stops_before_gpu_launch(monkeypatch):
    monkeypatch.setenv("TANK_REGION", "za")
    result = await operations.execute(operations.parser().parse_args(["saturday", "--dry-run"]))
    assert result["ok"] and result["region"] == "za" and result["launches"] == 0
    court = next(row for row in result["results"] if row["command"] == "judgment-training")
    nightly = next(row for row in result["results"] if row["command"] == "nightly")
    assert court["result"]["split"] + nightly["result"]["judgment_training"]["split"] >= 1
    assert any(request["path"].endswith("/court-text") for request in result["requests"])


@pytest.mark.asyncio
async def test_dry_run_cannot_use_configured_database_or_training_endpoint(monkeypatch):
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("DATABASE_URL", "postgresql://placeholder@private.invalid/placeholder")
    monkeypatch.setenv("TANK_URL", "http://private.invalid")
    result = await operations.execute(operations.parser().parse_args(["train", "--submit", "--dry-run"]))
    assert result["ok"] and result["launches"] == 0
    assert result["results"][0]["phase"] == "ready_to_launch"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["sample", "load", "nightly", "regulations", "convert", "selftest", "judgment-training", "reconcile", "gold", "heartbeat"])
async def test_each_operational_entry_point_accepts_dry_run(monkeypatch, command):
    monkeypatch.setenv("TANK_REGION", "us-east")
    result = await operations.execute(operations.parser().parse_args([command, "--dry-run"]))
    assert result["ok"] and result["dry_run"] and result["results"][0]["command"] == command


@pytest.mark.asyncio
async def test_billing_dry_run_cannot_write_a_real_ledger_or_start_training(monkeypatch):
    monkeypatch.setenv("TANK_REGION", "za")
    result = await operations.execute(operations.parser().parse_args(["train", "--job-id", "placeholder-job",
        "--billed-usd", "0", "--dry-run"]))
    assert result["ok"] and result["launches"] == 0 and result["results"][0]["phase"] == "receipt"
    assert [request["path"] for request in result["requests"]] == ["/v1/training/receipt"]


@pytest.mark.asyncio
async def test_missing_mail_settings_are_named_without_sending_anything(monkeypatch):
    monkeypatch.setenv("TANK_REGION", "us-east")
    names = ["ZENAS_SMTP_HOST", "ZENAS_SMTP_FROM", "ZENAS_SMTP_ALERT_ADDRESSES"]
    for name in names:
        monkeypatch.delenv(name, raising=False)
    result = await operations.execute(operations.parser().parse_args(["regulations", "--dry-run"]))
    report = result["results"][0]["result"]
    assert report["sent"] == 0 and report["needs_settings"] == sorted(names)


@pytest.mark.parametrize("module,name", [("bulk", "load"), ("nightly", "nightly")])
def test_legacy_pool_commands_honor_dry_run_instead_of_ignoring_the_flag(monkeypatch, module, name):
    import sys

    from agents.zenas.pool import bulk, nightly
    target = {"bulk": bulk, "nightly": nightly}[module]
    command = Mock(return_value=0)
    monkeypatch.setattr(operations, "main", command)
    monkeypatch.setattr(sys, "argv", ["placeholder-entrypoint", "--dry-run"])
    with pytest.raises(SystemExit) as stopped:
        target.main()
    assert stopped.value.code == 0
    command.assert_called_once_with([name, "--dry-run"])
