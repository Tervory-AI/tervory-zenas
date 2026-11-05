"""Tiger — the browser.

POST /render {url, settle_ms?, max_ms?, session?, agent?, tenant?, screenshot?} → what a person
would see after the page finished loading.

Why it exists: a plain HTTP fetch gives up on sites that build their page with JavaScript, load
slowly, or show a "checking your browser…" page for a few seconds first. Base used to mark those
sites dead. Tiger loads them in a real Chromium and waits (default up to 15 s) for the page to settle.

What it will not do, by design: it does not click "I am human" boxes, solve CAPTCHAs, spoof its
identity or use stealth tricks. If a site still shows an interactive check after waiting, Tiger
reports `challenge: true` and returns nothing from that page. The site owner's decision stands.

A session is one browser context for one agent and one tenant. Cookies are stored at
`/data/sessions/<agent>/<tenant>/<session>.json`. Contexts never mix. An idle context closes
after 10 minutes. `screenshot: true` keeps images and returns a PNG.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from playwright.async_api import Browser, async_playwright
from pydantic import BaseModel, Field

from ocr import ocr_png
from pool import SessionKeyError, idle_expired, render_block, route_continues, session_file

MAX_PAGES = int(os.environ.get("TIGER_MAX_PAGES", "4"))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
UA_SUFFIX = " TervoryTiger/0.1 (+https://tervory.com)"   # we say who we are
CHALLENGE_MARKERS = (
    "cf-turnstile", "challenges.cloudflare.com", "cf-challenge", "challenge-platform", "g-recaptcha",
    "hcaptcha.com", "captcha-delivery", "px-captcha",
)
CHALLENGE_TITLES = ("just a moment", "attention required", "verify you are human", "access denied", "are you a robot")

app = FastAPI(title="Tiger", version="0.2.0")
_pw = None
_browser: Browser | None = None
_ua: str = ""
_sem = asyncio.Semaphore(MAX_PAGES)
_guard = asyncio.Lock()
_live: dict[tuple[str, str, str], "_Live"] = {}
_sweeper: asyncio.Task | None = None


class _Live:
    def __init__(self, context: Any) -> None:
        self.context = context
        self.lock = asyncio.Lock()
        self.last = time.monotonic()


class RenderReq(BaseModel):
    url: str
    settle_ms: int = Field(default=10_000, ge=0, le=20_000)   # how long to wait for a self-clearing check
    max_ms: int = Field(default=25_000, ge=3_000, le=45_000)  # hard cap for the whole render
    session: str | None = None
    agent: str | None = None
    tenant: str | None = None
    screenshot: bool = False


class OcrReq(BaseModel):
    png_base64: str = Field(min_length=1, max_length=8_000_000)


@app.on_event("startup")
async def _start() -> None:
    global _pw, _browser, _ua, _sweeper
    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(args=["--disable-dev-shm-usage", "--no-sandbox"])
    ctx = await _browser.new_context()
    page = await ctx.new_page()
    _ua = (await page.evaluate("navigator.userAgent")).replace("HeadlessChrome", "Chrome") + UA_SUFFIX
    await ctx.close()
    _sweeper = asyncio.create_task(_close_idle())


@app.on_event("shutdown")
async def _stop() -> None:
    global _sweeper
    if _sweeper is not None:
        _sweeper.cancel()
        try:
            await _sweeper
        except asyncio.CancelledError:
            pass
        _sweeper = None
    async with _guard:
        lives = list(_live.items())
        _live.clear()
    for key, live in lives:
        await _save_and_close(key, live)
    if _browser:
        await _browser.close()
    if _pw:
        await _pw.stop()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": _browser is not None and _browser.is_connected(), "max_pages": MAX_PAGES}


async def _looks_like_challenge(page) -> bool:
    try:
        title = (await page.title()).lower()
        if any(t in title for t in CHALLENGE_TITLES):
            return True
        html = (await page.content())[:200_000].lower()
        return any(m in html for m in CHALLENGE_MARKERS)
    except Exception:  # noqa: BLE001 — page navigated mid-read; treat as still settling
        return True


async def _route(target, screenshot: bool) -> None:
    async def handle(route) -> None:
        # Every hop: the navigation, a redirect, and each script or image.
        request = route.request
        if route_continues(request.url, request.resource_type, screenshot):
            await route.continue_()
        else:
            await route.abort()

    await target.route("**/*", handle)


async def _fill(page, req: RenderReq, out: dict[str, Any]) -> None:
    resp = await page.goto(req.url, wait_until="domcontentloaded", timeout=req.max_ms)
    out["status"] = resp.status if resp else None
    # Let scripts run and the page settle.
    try:
        await page.wait_for_load_state("networkidle", timeout=min(8_000, req.max_ms))
    except Exception:  # noqa: BLE001 — busy pages never go idle; content is still usable
        pass
    # A self-clearing "checking your browser" page usually navigates on its own within seconds.
    deadline = time.perf_counter() + req.settle_ms / 1000
    while await _looks_like_challenge(page) and time.perf_counter() < deadline:
        await asyncio.sleep(1.0)
    if await _looks_like_challenge(page):
        out["challenge"] = True   # interactive check still there: we stop, by design
        return
    out["final_url"] = page.url
    out["title"] = (await page.title())[:300]
    out["text"] = (await page.evaluate("document.body ? document.body.innerText : ''"))[:30_000]
    out["html"] = (await page.content())[:600_000]
    out["ok"] = bool(out["text"].strip())
    if req.screenshot:
        png = await page.screenshot(type="png")
        out["png_base64"] = base64.b64encode(png).decode("ascii")


def _context_args() -> dict[str, Any]:
    return {
        "user_agent": _ua,
        "java_script_enabled": True,
        "locale": "en-US",
        "viewport": {"width": 1280, "height": 900},
    }


async def _render_ephemeral(req: RenderReq, started: float) -> dict[str, Any]:
    assert _browser is not None
    ctx = await _browser.new_context(**_context_args())
    await _route(ctx, req.screenshot)
    page = await ctx.new_page()
    out: dict[str, Any] = {"url": req.url, "ok": False, "challenge": False}
    try:
        await _fill(page, req, out)
        return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
        return out
    finally:
        out["ms"] = int((time.perf_counter() - started) * 1000)
        await ctx.close()


async def _session_live(agent: str, tenant: str, session: str) -> tuple[_Live, Any]:
    assert _browser is not None
    key = (agent, tenant, session)
    path = session_file(DATA_DIR, agent, tenant, session)
    async with _guard:
        live = _live.get(key)
        if live is None:
            kwargs = _context_args()
            if path.is_file():
                kwargs["storage_state"] = str(path)
            live = _Live(await _browser.new_context(**kwargs))
            _live[key] = live
        live.last = time.monotonic()
        return live, path


async def _save_and_close(key: tuple[str, str, str], live: _Live) -> None:
    try:
        path = session_file(DATA_DIR, *key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await live.context.storage_state(path=str(path))
    except Exception:  # noqa: BLE001 — a closed browser has nothing left to save
        pass
    try:
        await live.context.close()
    except Exception:  # noqa: BLE001
        pass


async def _render_session(req: RenderReq, started: float) -> dict[str, Any]:
    out: dict[str, Any] = {"url": req.url, "ok": False, "challenge": False}
    try:
        live, path = await _session_live(req.agent or "", req.tenant or "", req.session or "")
    except SessionKeyError:
        out["error"] = "session needs an agent and a tenant"
        out["ms"] = int((time.perf_counter() - started) * 1000)
        return out
    async with live.lock:
        live.last = time.monotonic()
        page = await live.context.new_page()
        try:
            await _route(page, req.screenshot)
            await _fill(page, req, out)
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)[:300]
        finally:
            await page.close()
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                await live.context.storage_state(path=str(path))
            except Exception:  # noqa: BLE001
                pass
            live.last = time.monotonic()
            out["ms"] = int((time.perf_counter() - started) * 1000)
    return out


async def _close_idle() -> None:
    while True:
        await asyncio.sleep(30)
        async with _guard:
            keys = list(_live.keys())
        for key in keys:
            async with _guard:
                live = _live.get(key)
            if live is None:
                continue
            async with live.lock:
                if not idle_expired(live.last, time.monotonic()):
                    continue
                await _save_and_close(key, live)
                async with _guard:
                    if _live.get(key) is live:
                        _live.pop(key, None)


@app.post("/render")
async def render(req: RenderReq) -> dict[str, Any]:
    blocked = render_block(req.url)
    if blocked is not None:
        return blocked
    assert _browser is not None
    started = time.perf_counter()
    async with _sem:
        if req.session:
            return await _render_session(req, started)
        return await _render_ephemeral(req, started)


@app.post("/ocr")
async def ocr(req: OcrReq) -> dict[str, str]:
    try:
        raw = base64.b64decode(req.png_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="png is not base64") from exc
    if not raw.startswith(b"\x89PNG"):
        raise HTTPException(status_code=400, detail="not a png")
    return {"text": await ocr_png(raw)}
