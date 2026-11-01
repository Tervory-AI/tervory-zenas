"""Actual text UI against domain-backed offline transport; no live auth/models.

Postgres and trusted identity are tested separately. This harness proves client
handover/draft/private-projection behavior and captures existing-shell viewports.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agents.zenas.core.moot import hearing  # noqa: E402
from agents.zenas.core.moot.hearing_ai import validate_turn  # noqa: E402

WORKSPACE = ROOT / "workspace"
TENANT = str(uuid4())
OWNER = hearing.Actor(TENANT, "operator", "us-east", True)
PEOPLE = [OWNER, hearing.Actor(TENANT, "student-b", "us-east"), hearing.Actor(TENANT, "student-j", "us-east")]
PROBLEM = {"worked_id": str(uuid4()), "region": "us-east", "kind": "civil_appeal", "criminal": False,
    "title": "Offline civil appeal", "facts": "Notice reached the recipient before the deadline.", "issues": ["Was notice timely?"],
    "bundles": {s: {"issues": ["Was notice timely?"]} for s in hearing.SIDES},
    "bench_notes": {"research": "Private bench research"}, "real_holding": "The original court dismissed the appeal.",
    "authorities": [{"id": "case-1", "name": "Offline authority", "quote": "Notice must arrive before the deadline.",
        "source_url": "https://example.invalid/case", "verified": True, "weight": "unknown"}]}


class Boundary:
    def __init__(self):
        self.state = hearing.create(str(uuid4()), OWNER, deepcopy(PROBLEM), {
            "applicant": {"kind": "human", "user_id": OWNER.user_id}, "respondent": {"kind": "human", "user_id": PEOPLE[1].user_id},
            "judge": {"kind": "human", "user_id": PEOPLE[2].user_id}})
        self.state = hearing.change(self.state, OWNER, 0, "lock", {})
        self.state = hearing.change(self.state, OWNER, 1, "start", {})
        self.requests = {}
        self.delay_judge = False
        self.ai_calls = 0

    def present(self, actor, seat=None):
        projection = hearing.view(self.state, actor, seat)
        projection["pending_ai"] = None
        projection["last_ai_resolution"] = None
        return {"hearing": projection, "operator": actor == OWNER}

    async def route(self, route, actor):
        request = route.request
        path = urlsplit(request.url).path.removeprefix("/api/zenas/moot/").split("/")
        seat = parse_qs(urlsplit(request.url).query).get("seat", [None])[0]
        try:
            if request.method == "GET":
                if path == ["hearings"]:
                    result = {"hearings": [{"id": self.state["id"], "title": PROBLEM["title"], "phase": self.state["phase"]}]}
                elif path == ["sources"]:
                    if actor != OWNER: return await route.fulfill(status=404, json={"error": "not_found"})
                    result = {"sources": [{"id": PROBLEM["worked_id"], "title": PROBLEM["title"]}]}
                elif path == ["participants"]:
                    if actor != OWNER: return await route.fulfill(status=404, json={"error": "not_found"})
                    result = {"participants": [{"user_id": p.user_id, "name": p.user_id} for p in PEOPLE]}
                else:
                    result = self.present(actor, seat)
                    if seat == "judge" and self.delay_judge:
                        await asyncio.sleep(0.4)  # Deliberately deliver the old privileged projection late.
                return await route.fulfill(json=result)
            body = request.post_data_json
            action = path[-1]
            if action == "ai":
                self.ai_calls += 1
                return await route.fulfill(status=503, json={"error": "room_unavailable"})
            request_id = body["request_id"]
            if request_id not in self.requests:
                if action == "turn":
                    self.state = hearing.turn(self.state, actor, body["revision"], validate_turn(self.state, body["payload"]))
                else:
                    self.state = hearing.change(self.state, actor, body["revision"], action, body["payload"])
                self.requests[request_id] = True
            return await route.fulfill(json=self.present(actor, seat))
        except hearing.HearingError as exc:
            return await route.fulfill(status=409 if exc.code == "stale_revision" else 403, json={"error": exc.code})


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); return sock.getsockname()[1]


async def fill_submission(page, text, *, judge=False):
    await page.get_by_role("textbox", name=re.compile(r"^Submission")).fill(text)
    await page.get_by_role("textbox", name=re.compile(r"^Why this fact matters")).fill("Timely notice is the material fact.")
    await page.get_by_role("textbox", name=re.compile(r"^Opposing argument")).fill("The recipient disputes receipt.")
    await page.get_by_role("textbox", name=re.compile(r"^Response to the challenge")).fill("Compare the recorded delivery date, not similar words.")
    await page.get_by_role("textbox", name=re.compile(r"^Uncertainty")).fill("The notice record may be disputed.")
    await page.get_by_role("textbox", name=re.compile(r"^Conclusion")).fill("The timing rule applies subject to the disputed record.")
    if judge:
        await page.get_by_role("textbox", name=re.compile(r"^Order")).fill("Independent reasoned order")
        await page.get_by_role("textbox", name=re.compile(r"^Reasons")).fill("The material timing fact was addressed by both counsel.")


async def capture(base, output):
    files = []; errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        try:
            for width in (1440, 375):
                boundary = Boundary()
                contexts = []
                pages = []
                for actor in PEOPLE:
                    context = await browser.new_context(viewport={"width": width, "height": 1000})
                    contexts.append(context)
                    await context.route("**/api/zenas/moot/**", lambda route, request=None, actor=actor: boundary.route(route, actor))
                    page = await context.new_page(); pages.append(page)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    await page.goto(base)
                    if errors: print("Browser errors:", errors)
                    await page.get_by_role("combobox", name=re.compile(r"^The hearing")).select_option(boundary.state["id"])
                owner, respondent, judge = pages
                await owner.get_by_role("textbox", name=re.compile(r"^Submission")).fill("Keep this human draft through handover")
                await owner.get_by_role("combobox", name=re.compile(r"^Applicant")).select_option("ai")
                await owner.get_by_role("combobox", name=re.compile(r"^Applicant")).select_option(OWNER.user_id)
                try:
                    await owner.get_by_role("textbox", name=re.compile(r"^Submission")).wait_for(timeout=5000)
                except Exception:
                    print("Handover state", json.dumps({k: boundary.state[k] for k in ("revision", "phase", "floor", "seats")}))
                    print("Owner body", await owner.locator("body").inner_text())
                    print("Browser errors", errors)
                    await owner.screenshot(path=str(output / "handover-failure.png"), full_page=True)
                    raise
                assert await owner.get_by_role("textbox", name=re.compile(r"^Submission")).input_value() == "Keep this human draft through handover"
                # The operator may inspect the judge role, but stale judge responses
                # must not restore its bench view after the operator selects counsel.
                boundary.delay_judge = True
                await owner.get_by_role("combobox", name=re.compile(r"^Your role")).select_option("judge")
                await owner.get_by_role("combobox", name=re.compile(r"^Your role")).select_option("applicant")
                await owner.wait_for_timeout(600)
                assert "Private bench research" not in await owner.locator("body").text_content()
                boundary.delay_judge = False
                await fill_submission(owner, "Applicant's actual typed submission")
                await owner.screenshot(path=str(output / f"operator-active-{width}.png"), full_page=True)
                files.append(f"operator-active-{width}.png")
                await owner.get_by_role("button", name="Submit", exact=True).click()
                await judge.get_by_role("textbox", name=re.compile(r"^Submission")).wait_for()
                assert "Private bench research" in await judge.locator("body").text_content()
                await fill_submission(judge, "What factual distinction changes the rule?")
                await judge.get_by_role("combobox", name=re.compile(r"^Counsel")).select_option("respondent")
                await judge.get_by_role("button", name="Submit", exact=True).click()
                await respondent.get_by_role("textbox", name=re.compile(r"^Submission")).wait_for()
                assert "Private bench research" not in await respondent.locator("body").text_content()
                await fill_submission(respondent, "Respondent's distinct typed answer")
                await respondent.screenshot(path=str(output / f"respondent-active-{width}.png"), full_page=True)
                files.append(f"respondent-active-{width}.png")
                await respondent.get_by_role("button", name="Submit", exact=True).click()
                await judge.get_by_role("textbox", name=re.compile(r"^Submission")).wait_for()
                await judge.get_by_role("combobox", name=re.compile(r"^Judge")).select_option("ruling")
                await fill_submission(judge, "An independent judgment on the relevant facts", judge=True)
                await judge.screenshot(path=str(output / f"judge-active-{width}.png"), full_page=True)
                files.append(f"judge-active-{width}.png")
                await judge.get_by_role("button", name="Submit", exact=True).click()
                await owner.get_by_role("button", name="Publish", exact=True).wait_for()
                await owner.get_by_role("button", name="Publish", exact=True).click()
                await owner.get_by_role("link", name="Public watch page", exact=True).wait_for()
                assert boundary.state["published"] and len(boundary.state["transcript"]) == 4
                assert boundary.state["seats"]["applicant"]["epoch"] == 2
                for name, page in zip(("operator", "respondent", "judge"), pages):
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    filename = f"{name}-{width}.png"
                    await page.screenshot(path=str(output / filename), full_page=True)
                    files.append(filename)
                for context in contexts: await context.close()
        finally:
            await browser.close()
    assert not errors, errors
    (output / "evidence.json").write_text(json.dumps({"label": "local actual UI with domain-backed offline transport; no live auth/models; Postgres tested separately",
        "checks": ["human draft survives AI/human handover", "stale judge projection cannot return to counsel", "two distinct human counsel and human judge",
                   "human judge question/floor/ruling", "founder publish", "375/1440 no horizontal overflow"], "screenshots": files, "page_errors": errors}, indent=2) + "\n")
    print("Passed both viewport journeys; twelve screenshots; no model or production calls")


def main():
    output = Path(sys.argv[1]).resolve(); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="moot-ui-") as temp:
        folder = Path(temp); (folder / "app").mkdir()
        (folder / "node_modules").symlink_to((WORKSPACE / "node_modules").resolve())
        (folder / "public").symlink_to(WORKSPACE / "public")
        (folder / "package.json").write_text('{"private":true}')
        (folder / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"target": "ES2022", "module": "esnext", "moduleResolution": "bundler", "jsx": "preserve", "esModuleInterop": True, "skipLibCheck": True}}))
        (folder / "next.config.mjs").write_text('export default {experimental:{externalDir:true},devIndicators:false};')
        fonts = json.dumps(str(WORKSPACE / "app/fonts"))
        (folder / "app/layout.tsx").write_text(f'import {{jost,inter,mono}} from {fonts};export default function Layout({{children}}:{{children:React.ReactNode}}){{return <html lang="en" style={{{{height:"auto",overflow:"visible"}}}} className={{`${{jost.variable}} ${{inter.variable}} ${{mono.variable}}`}}><body style={{{{margin:0,height:"auto",overflow:"visible"}}}}>{{children}}</body></html>;}}')
        component = json.dumps(str(WORKSPACE / "app/zenas/moot-court")); css = json.dumps(str(WORKSPACE / "app/zenas/zenas.css"))
        (folder / "app/page.tsx").write_text(f'import {{MootCourt}} from {component};import {css};export default function Page(){{return <main className="zn" style={{{{padding:24,display:"block",height:"auto",minHeight:"100vh",maxHeight:"none",boxSizing:"border-box",overflow:"visible"}}}}><MootCourt/></main>;}}')
        base = f"http://127.0.0.1:{port()}"
        with (output / "next.log").open("w") as log:
            web = subprocess.Popen(["node", str(WORKSPACE / "node_modules/next/dist/bin/next"), "dev", "--hostname", "127.0.0.1", "--port", base.rsplit(":", 1)[1]], cwd=folder,
                env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}, stdout=log, stderr=log)
            try:
                import time
                import urllib.request
                for _ in range(100):
                    try: urllib.request.urlopen(base, timeout=2); break
                    except Exception:
                        if web.poll() is not None: raise RuntimeError("Next exited; see next.log")
                        time.sleep(.3)
                asyncio.run(capture(base, output))
            finally:
                web.terminate(); web.wait(timeout=15)


if __name__ == "__main__":
    main()
