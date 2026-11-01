"""Actual Moot routes in a disposable Next shell with an offline Tank boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
MATCH = "c634c3df-a3e3-4d25-86ea-0286d63ba4c9"
mode = "empty"


class Tank(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        assert self.path.startswith("/v1/zenas/moot/published?")
        body = {"matches": []} if mode == "empty" else {}
        self.send_response(503 if mode == "unavailable" else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    global mode
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    tank = ThreadingHTTPServer(("127.0.0.1", 0), Tank)
    threading.Thread(target=tank.serve_forever, daemon=True).start()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="moot-states-") as temp:
            folder = Path(temp)
            (folder / "node_modules").symlink_to((WORKSPACE / "node_modules").resolve())
            shutil.copytree(WORKSPACE / "app/moot", folder / "app/moot")
            (folder / "lib").mkdir()
            shutil.copyfile(WORKSPACE / "lib/tank.ts", folder / "lib/tank.ts")
            (folder / "package.json").write_text('{"private":true}')
            (folder / "tsconfig.json").write_text(json.dumps({"compilerOptions": {
                "target": "ES2022", "module": "esnext", "moduleResolution": "bundler",
                "jsx": "preserve", "strict": True, "esModuleInterop": True,
                "skipLibCheck": True, "baseUrl": ".", "paths": {"@/*": ["./*"]}}}))
            (folder / "next.config.mjs").write_text('export default {devIndicators:false};')
            (folder / "app/layout.tsx").write_text('export default function Layout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>;}')
            env = {**os.environ, "TANK_URL": f"http://127.0.0.1:{tank.server_port}", "TANK_API_KEY": "", "NEXT_TELEMETRY_DISABLED": "1"}
            with (output / "next.log").open("w") as log:
                web = subprocess.Popen(["node", str(WORKSPACE / "node_modules/next/dist/bin/next"), "dev", "--hostname", "127.0.0.1", "--port", str(port)], cwd=folder, env=env, stdout=log, stderr=log)
                try:
                    import urllib.request
                    for _ in range(120):
                        if web.poll() is not None:
                            raise RuntimeError("Next exited; see next.log")
                        try:
                            urllib.request.urlopen(base + "/moot/status", timeout=2)
                            break
                        except Exception:
                            time.sleep(0.5)
                    with sync_playwright() as p:
                        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
                        try:
                            for width in (1440, 375):
                                page = browser.new_page(viewport={"width": width, "height": 1000})
                                for state in ("empty", "unavailable", "malformed"):
                                    mode = state
                                    expected = 503 if state == "unavailable" else 502 if state == "malformed" else 200
                                    readiness = page.request.get(base + "/moot/status")
                                    assert readiness.status == expected
                                    assert readiness.json() == {"status": state}
                                    for kind, path in (("index", "/moot"), ("detail", f"/moot/{MATCH}")):
                                        response = page.goto(base + path)
                                        expected_page = 404 if state == "empty" and kind == "detail" else 200
                                        assert response.status == expected_page
                                        if state == "empty" and kind == "index":
                                            assert page.get_by_text("No published matches yet.", exact=True).count() == 1
                                        elif state != "empty":
                                            assert page.locator(f'[role="alert"][data-moot-state="{state}"]').inner_text() == "Room unavailable"
                                            assert page.get_by_text("No published matches yet.", exact=True).count() == 0
                                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                                        name = f"{state}-{kind}-{width}.png"
                                        page.screenshot(path=str(output / name), full_page=True)
                                        results.append({"file": name, "http_status": response.status, "readiness_status": readiness.status})
                                page.close()
                        finally:
                            browser.close()
                finally:
                    web.terminate()
                    web.wait(timeout=15)
    finally:
        tank.shutdown()
        tank.server_close()
    (output / "evidence.json").write_text(json.dumps({"label": "local offline transport; actual Moot routes; no live journey claim", "captures": results}, indent=2) + "\n")
    print(f"Passed {len(results)} desktop/mobile route captures")


if __name__ == "__main__":
    main()
