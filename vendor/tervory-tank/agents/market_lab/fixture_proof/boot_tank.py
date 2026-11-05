"""Start the unmodified Tank app on local Postgres and verify its package import."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
from uuid import UUID

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "tank/skills/market.series_fetch/1.2.0"


def local_database() -> str:
    target = os.environ.get("DATABASE_URL", "")
    parsed = urlsplit(target)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.path.endswith("_test"):
        raise RuntimeError("Use a loopback Postgres database ending in _test")
    return target


async def verify_imported_cases() -> None:
    cases = [json.loads(line) for line in (PACKAGE / "evals/cases.jsonl").read_text().splitlines()]
    conn = await asyncpg.connect(local_database())
    try:
        registered = await conn.fetchrow(
            "SELECT cases, present FROM tank.skills WHERE id=$1 AND version=$2",
            "market.series_fetch", "1.2.0")
        assert registered and registered["present"] and registered["cases"] == len(cases)
        for case in cases:
            imported = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM tank.gold_examples "
                "WHERE tenant_id=$1 AND region='us-east' AND skill_id=$2 AND source='package' "
                "AND input=$3::jsonb AND expected=$4::jsonb)",
                UUID("00000000-0000-0000-0000-000000000001"), "market.series_fetch",
                json.dumps(case["input"]), json.dumps(case["expected"]))
            assert imported, case["name"]
        assert any(case["name"] == "decimal CSV observation" for case in cases)
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    target = local_database()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "DATABASE_URL": target, "TANK_REGION": "us-east",
           "TANK_API_KEY": secrets.token_hex(32), "LITELLM_MASTER_KEY": secrets.token_hex(32),
           "SKILLS_DIR": str(ROOT / "tank/skills"), "PYTHONPATH": str(ROOT / "tank"),
           "REDIS_URL": "redis://127.0.0.1:1/0"}
    # Generated local keys never leave process memory or appear in the log.
    command = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
               "--port", str(port), "--no-access-log"]
    with args.log.open("w") as log:
        log.write("Real Tank startup; cwd=tank; python3 -m uvicorn main:app (loopback, access log off)\n")
        log.flush()
        child = subprocess.Popen(command, cwd=ROOT / "tank", env=env, stdout=log, stderr=log)
        try:
            # Direct loopback readiness only; external egress keeps the managed proxy.
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError("Tank exited before startup completed; inspect the startup log")
                try:
                    with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        health = json.load(response)
                    if health.get("ok") is True:
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Tank did not become healthy; inspect the startup log")
            asyncio.run(verify_imported_cases())
            log.write("GET /health: 200, ok=true\n")
            log.write("Gold import: all 7/7 market.series_fetch 1.2.0 cases present, including decimal CSV observation.\n")
            log.flush()
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    print(args.log.read_text(), end="")


if __name__ == "__main__":
    main()
