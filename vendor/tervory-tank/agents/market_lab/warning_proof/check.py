"""Run the warning job on the real Tank and retain a small, credential-free receipt."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "agents/market_lab"), str(ROOT / "agents/_chassis")]
import asyncpg
import httpx
from fixture_proof.server import database_url
from warning_proof.seed import OWNER, ONE, EMPTY, RESOLVED
from warning_proof.services import services


def verify(env: dict) -> dict:
    with httpx.Client(
        base_url=env["TANK_URL"],
        headers={"x-tank-key": env["TANK_API_KEY"]},
        trust_env=False,
        timeout=150,
    ) as client:

        def call(op, tenant=OWNER, **data):
            return client.post(
                "/v1/market-lab/warnings/" + op, json={"tenant_id": tenant, **data}
            )

        assert client.get("/health").json()["ok"] is True
        response = call("list")
        assert response.status_code == 200, response.text
        active = response.json()
        assert len(active["warnings"]) == 1, active
        warning = active["warnings"][0]
        assert warning["status"] == "Window still open"
        assert all(
            fact["href"].startswith("/market-lab/sources/") for fact in warning["facts"]
        )
        for fact in warning["facts"]:
            ident = fact["href"].split("/")[3].split("#")[0]
            source = client.post(
                "/v1/market-lab/document",
                json={"tenant_id": OWNER, "document_id": ident},
            )
            assert source.status_code == 200, source.text
            calculation = source.json()["calculation"]
            assert calculation["episode_count"] == calculation["hits"] == 3, calculation
            for episode in calculation["episodes"]:
                for name in ("upstream", "previous", "baseline", "outcome"):
                    point = episode[name]
                    target = point["document_id"]
                    stored = client.post(
                        "/v1/market-lab/document",
                        json={
                            "tenant_id": OWNER,
                            "document_id": target,
                            "row": point["row"],
                        },
                    )
                    assert stored.status_code == 200
                    assert stored.json()["page_size"] == 50
                    rows = stored.json()["points"]
                    if point.get("display_value"):
                        assert (
                            next(p for p in rows if p["index"] == point["row"])[
                                "display_value"
                            ]
                            == point["display_value"]
                        )
                    assert any(
                        p["index"] == point["row"]
                        and str(p["value"]) == point["value"]
                        and p["date"] == point["date"]
                        for p in rows
                    )
            assert (
                client.post(
                    "/v1/market-lab/document",
                    json={"tenant_id": EMPTY, "document_id": ident},
                ).status_code
                == 404
            )
        assert call("list").json()["warnings"][0]["id"] == warning["id"]
        one = call("list", ONE).json()
        assert one["warnings"] == [], one
        assert any(
            p["text"] == "Not enough history yet: 1 of 3 past episodes"
            for p in one["history"]
        ), one
        assert call("list", EMPTY).json()["warnings"] == []
        answer = client.post(
            "/v1/market-lab/ask",
            json={"tenant_id": OWNER, "question": "What is the saved availability?"},
        )
        assert answer.status_code == 200 and answer.json()["facts"]
        assert any("Available" in fact["text"] for fact in answer.json()["facts"])
        assert all(
            "2 availability" not in fact["text"] for fact in answer.json()["facts"]
        )
        event_ids = []
        for useful in (True, False):
            event = str(uuid4())
            event_ids.append(event)
            data = {
                "warning_id": warning["id"],
                "event_id": event,
                "useful": useful,
                "actor": "local-owner",
            }
            saved = call("feedback", **data)
            assert saved.status_code == 200 and saved.json() == {
                "saved": True,
                "useful": useful,
                "sync_pending": False,
            }, saved.text
            assert call("feedback", **data).json() == saved.json()
            assert call("feedback", EMPTY, **data).status_code == 404
            assert call("feedback", **{**data, "useful": not useful}).status_code == 409
        daily = subprocess.run(
            [
                sys.executable,
                str(ROOT / "agents/market_lab/warning_proof/daily_job.py"),
            ],
            env={
                **env,
                "TANK_KEY": env["TANK_KEY_MARKET_LAB"],
                "AGENT_NAME": "market_lab",
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert daily.returncode == 0, daily.stdout + daily.stderr
        job_receipt = json.loads(daily.stdout.strip().splitlines()[-1])
        assert job_receipt["daily_job"] == "done"
        resolved_response = call("list", RESOLVED)
        assert resolved_response.status_code == 200, resolved_response.text
        resolved = resolved_response.json()["warnings"]
        assert len(resolved) == 3, resolved
        assert {w["status"] for w in resolved} == {
            "Came true",
            "Did not come true",
            "Not measurable: there were not enough saved observations during the window.",
        }, resolved
        assert all(w["resolution_href"] for w in resolved)
        assert call("list", RESOLVED).json()["warnings"] == resolved
        assert (
            client.post(
                "/v1/market-lab/internal/training",
                json={"tenant_id": OWNER, "event_id": event_ids[0]},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/v1/market-lab/warnings/list",
                json={"tenant_id": OWNER},
                headers={"x-tank-key": env["TANK_KEY_MARKET_LAB"]},
            ).status_code
            == 403
        )
        cross = client.post(
            "/v1/market-lab/internal/training",
            json={"tenant_id": EMPTY, "event_id": event_ids[0]},
            headers={"x-tank-key": env["TANK_KEY_MARKET_LAB"]},
        )
        assert cross.status_code == 404, cross.text

    async def records():
        conn = await asyncpg.connect(database_url())
        try:
            pairs = await conn.fetch(
                "SELECT tenant_id,source_ref,input,output,active,public FROM tank.training_pairs WHERE source='market_lab_warning' AND tenant_id=$1 AND source_ref=ANY($2::text[])",
                UUID(OWNER),
                event_ids,
            )
            assert len(pairs) == 2
            assert all(
                json.loads(row["input"])["calculation"]["episode_count"] == 3
                for row in pairs
            )
            assert all(not r["active"] and not r["public"] for r in pairs)
            assert {json.loads(r["output"])["useful"] for r in pairs} == {True, False}
            outcomes = await conn.fetch(
                "SELECT output,active,public FROM tank.training_pairs WHERE source='market_lab_warning' AND tenant_id=$1",
                UUID(RESOLVED),
            )
            assert len(outcomes) == 3 and {
                json.loads(r["output"])["outcome"] for r in outcomes
            } == {"hit", "miss", "not_measurable"}
            assert all(not r["active"] and not r["public"] for r in outcomes)
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM tank.training_pairs WHERE source='market_lab_warning' AND tenant_id=$1",
                    UUID(EMPTY),
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM tank.ledger WHERE tenant_id=ANY($1::uuid[])",
                    [UUID(x) for x in (OWNER, ONE, EMPTY, RESOLVED)],
                )
                == 0
            )
            return {
                "feedback_pairs": len(pairs),
                "resolution_pairs": len(outcomes),
                "other_tenant_pairs": 0,
                "model_calls": 0,
            }
        finally:
            await conn.close()

    return {
        "result": "passed",
        "daily_resolution_job": job_receipt["daily_job"],
        "active_warnings": 1,
        "history_episodes": 3,
        "insufficient_history": "1 of 3 past episodes",
        "resolutions": ["came true", "did not come true", "not measurable"],
        **asyncio.run(records()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "services.log").open("w") as log:
        with services(log=log) as env:
            result = verify(env)
    (args.output / "job.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
