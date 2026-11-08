"""Local test data and real Tank/room processes; no replacement Tank objects.

The database must be loopback and end in _test. The room serves its real API
without its scheduler, so refresh is proved through its persisted queue entry.
The HTTP forwarder only resolves the room's Docker hostname to its local port.
It cannot forward to any publisher or model. Tank's own startup creates _http.
"""
from __future__ import annotations

import asyncio
import http.client
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
TENANT = "00000000-0000-0000-0000-000000000905"
FIXTURES = ROOT / "tests/fixtures/feeds"


def database_url() -> str:
    target = os.environ.get("DATABASE_URL", "")
    parsed = urlsplit(target)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.path.endswith("_test"):
        raise RuntimeError("The fixture proof requires a loopback database ending in _test")
    return target


async def seed(*, history: bool = False) -> None:
    from uuid import UUID
    from chassis.db import ensure_schema
    from core.jobs import perform_refresh
    from core.ledger import PostgresLedger
    from core.evidence import save_check
    from datetime import datetime, timezone
    conn = await asyncpg.connect(database_url())
    try:
        await ensure_schema(conn, "market_lab")
        ledger = PostgresLedger(conn)
        await ledger.apply_schema()
        with tempfile.TemporaryDirectory(prefix="market-history-") if history else nullcontext(str(FIXTURES)) as fixtures:
            if history:
                shutil.copytree(FIXTURES, fixtures, dirs_exist_ok=True)
                # Saved publisher-shaped input, parsed and stored by the real job.
                (Path(fixtures) / "ports/los_angeles.csv").write_text(
                    "month,total_teu\n" + "".join(f"{year}-01,{14408 + year - 1960}\n" for year in range(1960, 2027)))
            await perform_refresh(tenant_id=TENANT, engine="ffl", ledger=ledger, fixtures=fixtures)
        # This disposable local database represents production-shaped inputs.
        # Explicitly classify only this tenant's seeded records for the normal
        # serving path. No application read filter or route is overridden.
        # Never use this script against any deployed or shared database.
        for table in ("feed_checks", "feed_documents"):
            await conn.execute(f"UPDATE agent_market_lab.{table} SET fixture = false WHERE tenant_id = $1 AND fixture = true", UUID(TENANT))
        if history:
            from fixture_proof.mixed_source import seed_mixed_source
            await seed_mixed_source(ledger, TENANT)
        # A saved external page-check result, to prove linked-page status reaches
        # the UI. Other unvisited linked pages must still say Not checked yet.
        await save_check(ledger, TENANT, {"source_id": "ups_hazmat", "name": "UPS hazardous materials",
            "points": [], "events": [{"kind": "changed", "date": "2024-03-02", "title": "changed", "geo": "US", "body": "saved-test-digest", "url": "https://www.ups.com/"}],
            "downloads": [], "note": "", "fixture": False}, at=datetime.now(timezone.utc))
    finally:
        await conn.close()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url: str, child: subprocess.Popen, *, timeout: float = 45, headers: dict | None = None) -> None:
    # Loopback service readiness only; never bypass the cloud proxy for egress.
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError("A local service exited during startup; inspect its local log")
        try:
            with opener.open(Request(url, headers=headers or {}), timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Local service did not become ready")


@contextmanager
def services(api_port: int = 8046, log=None, *, history: bool = False):
    # Bind before seeding or starting children; never release a discovered port
    # for another parallel test to claim before uvicorn has inherited its fd.
    with socket.socket() as api_socket, socket.socket() as room_socket:
        api_socket.bind(("127.0.0.1", api_port))
        room_socket.bind(("127.0.0.1", 0))
        api_socket.listen(128)
        room_socket.listen(128)
        api_port = api_socket.getsockname()[1]
        room_port = room_socket.getsockname()[1]
        database_url()
        sys.path[:0] = [str(ROOT / "agents/market_lab"), str(ROOT / "agents/_chassis")]
        asyncio.run(seed(history=history))
    
        class Corridor(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Never log request headers or credentials.
    
            def do_POST(self):
                destination = urlsplit(self.path)
                if destination.scheme != "http" or destination.netloc != "agent-market-lab:8000" or not destination.path.startswith("/market/"):
                    self.send_error(502, "Only the local Market Lab corridor is available")
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                connection = http.client.HTTPConnection("127.0.0.1", room_port, timeout=30)
                try:
                    connection.request("POST", destination.path, body, {"content-type": "application/json", "x-tank-key": self.headers.get("x-tank-key", "")})
                    response = connection.getresponse()
                    data = response.read()
                    self.send_response(response.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                finally:
                    connection.close()
    
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Corridor)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "tank"), str(ROOT / "agents/market_lab"), str(ROOT / "agents/_chassis")]),
               "TANK_API_KEY": secrets.token_hex(32), "TANK_KEY_MARKET_LAB": secrets.token_hex(32),
               "LITELLM_MASTER_KEY": secrets.token_hex(32), "REDIS_URL": "redis://127.0.0.1:1/0",
               "TANK_URL": f"http://127.0.0.1:{api_port}", "NEXT_TELEMETRY_DISABLED": "1"}
        for name in list(env):
            if name.startswith("TRIGGER_SIGNIN_") or name in {"FRED_API_KEY", "CENSUS_API_KEY", "LEGISCAN_API_KEY", "EIA_API_KEY", "NOAA_TOKEN", "FEEDS_FIXTURES"}:
                env.pop(name)
        room_env = {**env, "TANK_KEY": env["TANK_KEY_MARKET_LAB"]}
        # Local corridor forwarding, not an external network bypass. All other
        # destinations are refused by Corridor; HTTPS retains the managed proxy.
        tank_env = {**env, "HTTP_PROXY": f"http://127.0.0.1:{proxy.server_port}", "http_proxy": f"http://127.0.0.1:{proxy.server_port}", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        children = []
        try:
            room = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--fd", str(room_socket.fileno()), "--lifespan", "off", "--no-access-log"], cwd=ROOT / "agents/market_lab", env=room_env, stdout=log, stderr=log, pass_fds=(room_socket.fileno(),))
            children.append(room)
            wait_ready(f"http://127.0.0.1:{room_port}/market/settings", room, headers={'X-Tank-Key': room_env['TANK_KEY']})
            tank = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--fd", str(api_socket.fileno()), "--no-access-log"], cwd=ROOT / "tank", env=tank_env, stdout=log, stderr=log, pass_fds=(api_socket.fileno(),))
            children.append(tank)
            wait_ready(f"http://127.0.0.1:{api_port}/health", tank)
            yield env
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=2)
