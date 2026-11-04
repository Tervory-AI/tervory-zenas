"""Integration with the existing Laws.Africa sync, Tank proxy and nightly hook."""

from datetime import date, datetime, timezone
from pathlib import Path

import asyncpg
import pytest

from agents.zenas.core.regulation_sources import laws_africa_snapshot
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex
from agents.zenas.pool.store import PostgresStore
from zenas_za_laws_africa.test_sync import Replay, SOURCE


@pytest.mark.asyncio
async def test_laws_africa_minimum_act_version_link_and_outage(
    tmp_path, monkeypatch, clock_and_token
):
    pgserver = pytest.importorskip("pgserver")
    monkeypatch.setenv("TANK_REGION", "za")
    server = pgserver.get_server(tmp_path / "laws-db")
    conn = await asyncpg.connect(server.get_uri())
    try:
        pool = PostgresStore(conn, home_region="za")
        await pool.apply_schema()
        replay = Replay()
        args = dict(
            region="za",
            only=[SOURCE],
            live=True,
            http_get=replay.get,
            embedder=NoOpEmbedder(),
            index=NoOpVectorIndex(),
            data_dir=tmp_path,
        )
        await load_wave(pool, on=date(2026, 10, 28), **args)
        watch = dict(topic="Companies Act")
        before = await laws_africa_snapshot(conn, watch)
        assert len(before) == 1 and before[0]["date"] == "2024-01-01"
        assert (
            "Laws.Africa" in before[0]["attribution"]
            and "2024-01-01" in before[0]["link"]
        )
        work = replay.pages[0]["results"][0]
        work["points_in_time"].append(
            {
                "date": "2026-10-29",
                "expressions": [
                    {
                        "language": "eng",
                        "url": "https://api.laws.africa/v3"
                        + work["frbr_uri"]
                        + "/eng@2026-10-29",
                    }
                ],
            }
        )
        await load_wave(pool, on=date(2026, 10, 30), **args)
        after = await laws_africa_snapshot(conn, watch)
        assert len(after) == 1 and after[0]["date"] == "2026-10-29"
        assert (
            after[0]["version"] != before[0]["version"]
            and "2026-10-29" in after[0]["link"]
        )
        await pool.record_run(
            "laws-africa",
            status="error",
            detail="",
            documents=0,
            when=datetime.now(timezone.utc),
        )
        with pytest.raises(ValueError, match="^source_unavailable$"):
            await laws_africa_snapshot(conn, watch)
    finally:
        await conn.close()
        server.cleanup()


@pytest.mark.asyncio
async def test_real_tank_proxy_allows_watch_and_supplies_room_auth(monkeypatch):
    from test_tank_zenas_load_timeout import _request
    import main
    import json

    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("TANK_KEY_ZENAS", "TANK_KEY_ZENAS")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    calls = []

    class Room:
        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            import httpx

            return httpx.Response(201, json={"ok": True})

    monkeypatch.setattr(main, "_http", Room())
    path = "regulations/watches"
    request = _request(
        "POST", "/v1/zenas/" + path, json.dumps({"home_region": "us-east"}).encode()
    )
    result = await main.zenas_proxy(path, request)
    assert result.status_code == 201
    assert calls[0][1] == "http://agent-zenas:8000/regulations/watches"
    assert calls[0][2]["headers"]["X-Tank-Key"] == "TANK_KEY_ZENAS"


def test_existing_nightly_command_calls_alert_job_after_sources():
    import ast

    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "agents/zenas/pool/nightly.py").read_text())
    # #621: _main wraps _night_main in the ZA run window, and _night_main runs the
    # sources (run_nightly) before _scheduled_jobs, whose first job is the alerts.
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}
    calls = [
        n
        for n in ast.walk(funcs["_night_main"])
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    pool_lines = [n.lineno for n in calls if n.func.id == "run_nightly"]
    job_lines = [n.lineno for n in calls if n.func.id == "_scheduled_jobs"]
    assert pool_lines and len(job_lines) == 1 and job_lines[0] > max(pool_lines)
    jobs = next(
        n
        for n in ast.walk(funcs["_scheduled_jobs"])
        if isinstance(n, ast.Tuple) and n.elts and isinstance(n.elts[0], ast.Tuple)
    )
    names = [item.elts[1].id for item in jobs.elts]
    assert names[0] == "regulations_scheduled" and names.count("regulations_scheduled") == 1
    for filename in (
        "agents/zenas/docker-compose.yml",
        "agents/zenas/docker-compose.za.yml",
    ):
        import yaml

        env = yaml.safe_load((root / filename).read_text())["services"]["agent-zenas"][
            "environment"
        ]
        assert all(
            "ZENAS_SMTP_" + name in env
            for name in ("HOST", "PORT", "USER", "PASSWORD", "FROM", "ALERT_ADDRESSES")
        )
    assert (
        "LAWS_AFRICA_API_TOKEN"
        not in yaml.safe_load((root / "agents/zenas/docker-compose.yml").read_text())[
            "services"
        ]["agent-zenas"]["environment"]
    )
