"""Real Tank, Market Lab and Tiger processes on a disposable local Postgres."""

from __future__ import annotations

import asyncio
import http.client
import os
import secrets
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "agents/market_lab"), str(ROOT / "agents/_chassis")]
from fixture_proof.server import database_url, free_port, wait_ready
from warning_proof.seed import seed


@contextmanager
def services(*, log=None, api_port=None, seed_data=True):
    database_url()
    if seed_data:
        asyncio.run(seed())
    ports = {"agent-market-lab:8000": free_port(), "agent-tiger:8000": free_port()}
    api_port = api_port or free_port()

    class Corridor(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def forward(self):
            target = urlsplit(self.path)
            if target.scheme != "http" or target.netloc not in ports:
                self.send_error(502, "Only declared local room corridors are available")
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            connection = http.client.HTTPConnection(
                "127.0.0.1", ports[target.netloc], timeout=130
            )
            try:
                connection.request(
                    self.command,
                    target.path + ("?" + target.query if target.query else ""),
                    body,
                    {
                        "content-type": "application/json",
                        "x-tank-key": self.headers.get("x-tank-key", ""),
                    },
                )
                response = connection.getresponse()
                data = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            finally:
                connection.close()

        do_POST = forward
        do_GET = forward

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Corridor)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [
                str(ROOT / "tank"),
                str(ROOT / "agents/market_lab"),
                str(ROOT / "agents/_chassis"),
                # Tiger's alert_email imports agents.rig.rigbox.reserve (#365); its image
                # copies that file to /app/agents/rig/rigbox/, so the repo root plays /app.
                str(ROOT),
            ]
        ),
        "TANK_API_KEY": secrets.token_hex(32),
        "TANK_KEY_MARKET_LAB": secrets.token_hex(32),
        "LITELLM_MASTER_KEY": secrets.token_hex(32),
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "TANK_URL": f"http://127.0.0.1:{api_port}",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "TANK_REGION": "us-east",
    }
    for key in list(env):
        if key.startswith(("TRIGGER_SIGNIN_", "TRIGGER_SIGNIN_")) or key in {
            "FRED_API_KEY",
            "CENSUS_API_KEY",
            "LEGISCAN_API_KEY",
            "EIA_API_KEY",
            "NOAA_TOKEN",
            "FEEDS_FIXTURES",
        }:
            env.pop(key)
    children = []
    try:
        for name, port in ports.items():
            room = "market_lab" if name.startswith("agent-market") else "tiger"
            cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ]
            if room == "market_lab":
                cmd += ["--lifespan", "off"]
            child = subprocess.Popen(
                cmd,
                cwd=ROOT / "agents" / room,
                env={
                    **env,
                    "AGENT_NAME": room,
                    "TANK_KEY": env["TANK_KEY_MARKET_LAB"]
                    if room == "market_lab"
                    else secrets.token_hex(32),
                },
                stdout=log,
                stderr=log,
            )
            children.append(child)
            wait_ready(f"http://127.0.0.1:{port}/openapi.json", child)
        tank_env = {
            **env,
            "HTTP_PROXY": f"http://127.0.0.1:{proxy.server_port}",
            "http_proxy": f"http://127.0.0.1:{proxy.server_port}",
        }
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--no-access-log",
            ],
            cwd=ROOT / "tank",
            env=tank_env,
            stdout=log,
            stderr=log,
        )
        children.append(child)
        wait_ready(env["TANK_URL"] + "/health", child)
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
