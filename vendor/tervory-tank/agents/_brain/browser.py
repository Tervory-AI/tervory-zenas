"""Base's careful public-web visitor, with persistent cookies and no bot bypass."""
from __future__ import annotations

import asyncio
from asyncio import CancelledError
import base64
import hmac
import uuid
from dataclasses import dataclass
import hashlib
import html as html_module
import io
import json
import logging
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from brain.evidence import stage
from brain.robots import HostBlocked, Run, USER_AGENT, host_stopped, require_allowed, stop_host
from brain.reader import read_page
from brain.security import public_url, scope, tenant_dir

app = FastAPI()
# The browser holds no Tank key (SEAL-1): it runs untrusted web pages. Its only
# caller is its owning gateway, which checks the room key before forwarding.


async def gateway_addresses() -> set[str]:
    answers = await asyncio.get_running_loop().getaddrinfo(
        urlsplit(os.environ.get("ACCESS_GATEWAY_URL", "http://base-browser-gateway:3000")).hostname,
        3000, type=socket.SOCK_STREAM)
    return {answer[4][0] for answer in answers}


@app.middleware("http")
async def only_the_gateway(request: Request, call_next):
    # Everything but /health must come from the gateway's address on the internal
    # browser network. A page inside this container calls from 127.0.0.1, which is
    # refused. uvicorn runs with --no-proxy-headers, so the peer cannot be forged.
    if request.url.path != "/health":
        peer = request.client.host if request.client else ""
        try:
            allowed = await gateway_addresses()
        except OSError:
            return JSONResponse({"detail": "The browser gateway cannot be found."}, status_code=503)
        if not peer or peer not in allowed:
            return JSONResponse({"detail": "The browser accepts work only from its gateway."}, status_code=401)
    return await call_next(request)
concurrency = asyncio.Semaphore(4)
site_locks: dict[str, asyncio.Lock] = {}
last_visit: dict[str, float] = {}
QUEUE_TIMEOUT = 20
# Three cleanup steps fit within Tiger's five-second transport margin. Each
# step is attempted even if the preceding resource fails to close.
CLEANUP_STEP_TIMEOUT_S = 1


async def cleanup_visit(context, browser, manager, storage, *, save: bool, bounded: bool):
    async def step(action):
        if not bounded:
            await action()
            return
        try:
            async with asyncio.timeout(CLEANUP_STEP_TIMEOUT_S):
                await action()
        except Exception as exc:
            # Never include exception messages, storage paths or credentials.
            logging.getLogger(__name__).warning("Browser cleanup failed (%s)", type(exc).__name__)

    try:
        if save and context is not None:
            await step(lambda: context.storage_state(path=str(storage)))
    finally:
        try:
            if browser is not None:
                await step(browser.close)
        finally:
            await step(lambda: manager.__aexit__(None, None, None))


@asynccontextmanager
async def render_slot(site):
    lock = site_locks.setdefault(site, asyncio.Lock())
    locked = admitted = False
    slot = {"retained": False}
    try:
        try:
            async with asyncio.timeout(QUEUE_TIMEOUT):
                await lock.acquire()
                locked = True
                await asyncio.sleep(max(0, 3.0 - (time.monotonic() - last_visit.get(site, 0))))
                await concurrency.acquire()
                admitted = True
        except TimeoutError:
            raise HTTPException(503, "Base browser is busy; try again shortly.") from None
        yield slot
    finally:
        if admitted and not slot["retained"]:
            concurrency.release()
        if locked:
            lock.release()
HUMAN_CHECKS = ("g-recaptcha", "hcaptcha", "cf-turnstile", "verify you are human", "confirm you are human",
                "checking your browser", "complete the security check", "prove you are human",
                "px-captcha", "cf-error-details", "sorry, you have been blocked", "your request has been blocked")
AGE_BUTTON = re.compile(r"^(?:yes[,! ]*)?(?:i am|i'm|i confirm i am) (?:over |at least )?(?:18|21)(?:\+| years old)?[.!]?$", re.I)


class Page(BaseModel):
    catalogue_match: bool = False
    url: str = Field(max_length=2048)
    screenshot: bool = False
    price_reader: bool = False
    settle_ms: int = Field(default=2000, ge=0, le=10000)
    max_ms: int = Field(default=90000, ge=1000, le=150000)
    # Navigation's max_ms excludes admission, evidence and extraction. Callers
    # with a shorter transport deadline must expire that whole operation here.
    request_timeout_ms: int | None = Field(default=None, ge=1000, le=150000)


def human_check(html: str) -> bool:
    return any(marker in html.lower() for marker in HUMAN_CHECKS)


async def confirm_age(page, html: str) -> bool:
    # One narrowly identified age button, never a form, login, consent to terms,
    # bot check or general-purpose click instruction from page text.
    if human_check(html) or not re.search(r"(?:age.{0,30}(?:18|21)|(?:over|at least)\s+(?:18|21)|age verification)", html, re.I):
        return False
    buttons = page.get_by_role("button", name=AGE_BUTTON)
    for index in range(min(await buttons.count(), 4)):
        button = buttons.nth(index)
        if await button.is_visible() and not await button.evaluate("el => !!el.closest('form')"):
            await button.click(timeout=10000)
            await page.wait_for_timeout(2000)
            return True
    return False


def blocked_result(url: str) -> dict:
    return {"ok": False, "url": url, "challenge": True, "blocked_by_check": True,
            "reason": "blocked", "needs_person": "needs a person",
            "page": read_page(url, challenge=True)}


async def read_pdf(context, url: str, timeout: int, run: Run | None = None) -> dict:
    from pypdf import PdfReader
    started = time.perf_counter()
    for _ in range(8):
        public_url(url)
        await require_allowed(context, url, min(timeout, 10000), run)
        response = await context.request.get(url, timeout=timeout, max_redirects=0,
                                             headers={"User-Agent": USER_AGENT})
        if response.status in {401, 403, 429}:
            stop_host(run, url)
            return {**blocked_result(url), "content_type": "application/pdf", "status": response.status}
        if response.status in {301, 302, 303, 307, 308}:
            url = urljoin(url, response.headers.get("location", ""))
            continue
        if int(response.headers.get("content-length", "0")) > 10_000_000:
            raise ValueError("PDF too large")
        data = await response.body()
        if len(data) > 10_000_000 or not data.startswith(b"%PDF-"):
            raise ValueError("Unsupported document")
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:100])[:300000]
        return {"ok": True, "url": url, "status": response.status, "content_type": "application/pdf",
                "title": str((reader.metadata or {}).get("/Title", "")), "pdf_extraction_ms": (time.perf_counter() - started) * 1000,
                "text": text, "html": "<pre>" + html_module.escape(text) + "</pre>", "challenge": False,
                "blocked_by_check": False, "links": [
                    {"text": "PDF link", "url": str(annotation.get_object().get("/A", {}).get("/URI", ""))}
                    for page in reader.pages[:100] for annotation in page.get("/Annots", [])
                    if annotation.get_object().get("/A", {}).get("/URI")], "pdf_data": data}
    raise ValueError("Too many document redirects")


async def rendered_source(page) -> str:
    """Remove computed CSS invisibility while keeping structured data available."""
    return await page.evaluate(r"""() => {
        const copy = document.documentElement.cloneNode(true);
        const rawTextTags = new Set(['NOSCRIPT', 'TEMPLATE', 'IFRAME', 'NOEMBED', 'NOFRAMES', 'XMP']);
        const originals = [document.documentElement, ...document.documentElement.querySelectorAll('*')];
        const clones = [copy, ...copy.querySelectorAll('*')];
        const pageWidth = Math.max(document.documentElement.clientWidth, document.body.clientWidth, innerWidth);
        const pageHeight = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight, innerHeight);
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 1;
        const paint = canvas.getContext('2d', {willReadFrequently: true});
        const color = text => {
            paint.clearRect(0, 0, 1, 1);
            paint.fillStyle = text;
            paint.fillRect(0, 0, 1, 1);
            const rgba = [...paint.getImageData(0, 0, 1, 1).data];
            return [rgba[0], rgba[1], rgba[2], rgba[3] / 255];
        };
        const effectiveOpacity = el => {
            let opacity = 1;
            for (let node = el; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
            return opacity;
        };
        const background = el => {
            let result = [0, 0, 0, 0];
            for (let node = el; node; node = node.parentElement) {
                if (getComputedStyle(node).backgroundImage !== 'none') return null;
                const c = color(getComputedStyle(node).backgroundColor);
                if (!c) continue;
                const alpha = result[3] + c[3] * (1 - result[3]);
                if (alpha) result = [0, 1, 2].map(i => (result[i]*result[3] + c[i]*c[3]*(1-result[3]))/alpha).concat(alpha);
                if (alpha >= 1) break;
            }
            return [0, 1, 2].map(i => result[i]*result[3] + 255*(1-result[3]));
        };

        // WCAG relative luminance, with translucent ink composited over its
        // effective background before measuring contrast.
        const luminance = rgb => {
            const linear = rgb.map(value => {
                const channel = value / 255;
                return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
            });
            return linear[0]*0.2126 + linear[1]*0.7152 + linear[2]*0.0722;
        };
        const contrast = (ink, bg) => {
            const rendered = ink.slice(0,3).map((value, j) => value*ink[3] + bg[j]*(1-ink[3]));
            const a = luminance(rendered), b = luminance(bg);
            return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        };
        const visibleText = node => {
            const range = document.createRange();
            range.selectNodeContents(node);
            return [...range.getClientRects()].some(rect => {
                let left = rect.left, top = rect.top, right = rect.right, bottom = rect.bottom;
                for (let parent = node.parentElement; parent; parent = parent.parentElement) {
                    const css = getComputedStyle(parent), box = parent.getBoundingClientRect();
                    // Scrollable text is reachable by a reader. Project its
                    // logical extent into the scrollport before outer clipping.
                    if (['auto', 'scroll'].includes(css.overflowX) && parent.scrollWidth > parent.clientWidth) {
                        const start = box.left + parent.clientLeft - parent.scrollLeft;
                        if (right <= left || right <= start || left >= start + parent.scrollWidth) return false;
                        left = box.left + parent.clientLeft;
                        right = left + parent.clientWidth;
                    } else if (['hidden', 'clip'].includes(css.overflowX)) {
                        left = Math.max(left, box.left + parent.clientLeft);
                        right = Math.min(right, box.left + parent.clientLeft + parent.clientWidth);
                    }
                    if (['auto', 'scroll'].includes(css.overflowY) && parent.scrollHeight > parent.clientHeight) {
                        const start = box.top + parent.clientTop - parent.scrollTop;
                        if (bottom <= top || bottom <= start || top >= start + parent.scrollHeight) return false;
                        top = box.top + parent.clientTop;
                        bottom = top + parent.clientHeight;
                    } else if (['hidden', 'clip'].includes(css.overflowY)) {
                        top = Math.max(top, box.top + parent.clientTop);
                        bottom = Math.min(bottom, box.top + parent.clientTop + parent.clientHeight);
                    }
                }
                return Math.min(right, pageWidth-scrollX) > Math.max(left, -scrollX) &&
                       Math.min(bottom, pageHeight-scrollY) > Math.max(top, -scrollY);
            });
        };
        originals.forEach((el, i) => {
            // Remove raw-text elements before serialization so embedded closing
            // tags cannot escape a hidden subtree in the downstream parser.
            if (rawTextTags.has(el.tagName)) { clones[i].remove(); return; }
            if (el.closest('head') || el.tagName === 'SCRIPT') return;
            const css = getComputedStyle(el);
            // Contents wrappers have no box; their visible children still do.
            const contents = css.display === 'contents';
            const r = el.getBoundingClientRect();
            const x = r.left + scrollX, y = r.top + scrollY;
            let scrollableAncestor = false;
            for (let parent = el.parentElement; parent; parent = parent.parentElement) {
                const style = getComputedStyle(parent);
                if ((['auto', 'scroll'].includes(style.overflowY) && parent.scrollHeight > parent.clientHeight) ||
                    (['auto', 'scroll'].includes(style.overflowX) && parent.scrollWidth > parent.clientWidth)) scrollableAncestor = true;
            }
            const clipped = (css.clip !== 'auto' || css.clipPath !== 'none');
            const tinyBox = r.width <= 1 && r.height <= 1;
            const hiddenBox = !contents && (r.width <= 0 || r.height <= 0 || (!scrollableAncestor && (x+r.width <= 0 || y+r.height <= 0 || x >= pageWidth || y >= pageHeight)) || tinyBox);
            const ink = color(css.webkitTextFillColor || css.color), bg = background(el);
            const invisibleInk = ink && (ink[3] === 0 || (bg && contrast(ink, bg) < 1.2));
            // Mark the element's own text without hiding contrasting children.
            // Geometric/CSS invisibility applies to the whole subtree.
            if ((!el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) && !contents) || hiddenBox || clipped || effectiveOpacity(el) < 0.1 || el.getAttribute('aria-hidden') === 'true') {
                clones[i].remove();
                return;
            }
            const originalChildren = [...el.childNodes], copiedChildren = [...clones[i].childNodes];
            originalChildren.forEach((node, j) => {
                if (node.nodeType === Node.TEXT_NODE && node.textContent.trim() &&
                    (parseFloat(css.fontSize) < 4 || invisibleInk || !visibleText(node))) copiedChildren[j].remove();
            });
            if (parseFloat(css.fontSize) < 4 || invisibleInk) {
                clones[i].removeAttribute('itemprop');
                if (clones[i].tagName === 'A') clones[i].removeAttribute('href');
            }
            if (clones[i].tagName === 'A' && clones[i].hasAttribute('href')) clones[i].setAttribute('href', el.href);
        });
        return copy.outerHTML;
    }""")



@app.get("/health")
async def health():
    return {"ok": True, "room": os.environ.get("ACCESS_ROOM", "base")}


@app.post("/render")
async def render(body: Page, x_tank_key: str = Header(default=""), x_tenant_id: str = Header(default=""),
                 x_home_region: str = Header(default=""), x_task_id: str = Header(default="")):
    # x_tank_key is ignored and kept only so existing callers keep their argument
    # order. The gateway no longer sends it; only_the_gateway admits the caller.
    tenant = scope(x_tenant_id, x_home_region)
    if x_task_id:
        try: x_task_id = str(uuid.UUID(x_task_id))
        except ValueError: raise HTTPException(422, "Invalid task identifier.") from None
    try:
        site = site_origin(body.url)
    except (ValueError, OSError):
        raise HTTPException(403, "Private or internal web address refused.") from None
    async def scoped_visit():
        if access_configured():
            async with access_locks.setdefault((tenant, site), asyncio.Lock()):
                task = asyncio.current_task()
                active_visits[(tenant, site)] = task
                try:
                    return await visit(body, tenant, x_task_id, site)
                finally:
                    if active_visits.get((tenant, site)) is task: active_visits.pop((tenant, site), None)
        return await visit(body, tenant, x_task_id, site)

    if body.request_timeout_ms is None:
        return await scoped_visit()
    try:
        # Include the tenant/site lock wait: an expired queued visit must never
        # start later ahead of the caller's retry. Cancellation propagates into
        # visit's existing browser/context cleanup before releasing admission.
        async with asyncio.timeout(body.request_timeout_ms / 1000):
            return await scoped_visit()
    except TimeoutError:
        raise HTTPException(503, "The browser could not read this page; no result is claimed.") from None


async def visit(body, tenant, task, origin, *, run=None):
    host, _ = public_url(body.url)
    directory = tenant_dir(Path(os.environ.get("BROWSER_DATA_DIR", "/browser-data")), tenant)
    # A check starts empty even when its task ID is reused or omitted. No
    # process-global stop state can leak into another tenant/room/check.
    run = Run() if run is None else run
    result = {"ok": False, "challenge": False}
    try:
        result = await _visit(body, tenant, task, origin, run, directory)
        return result
    finally:
        with (directory / "history.jsonl").open("a") as history:
            history.write(json.dumps({"time": int(time.time()), "tenant_id": tenant,
                "task_id": str(uuid.UUID(task)) if task else None,
                "action": "browser", "host": host, "human_check": bool(result.get("challenge")),
                "needs_person": "needs a person" if result.get("challenge") else None,
                "age_confirmation": bool(result.get("age_confirmation")), "ok": result.get("ok", False),
                "reason": result.get("reason"),
                # Sealed connection audits must not expose private URL paths.
                "url": origin if access_configured() else result.get("url", body.url)}) + "\n")


async def _visit(body, tenant, task, origin, run, directory):
    host, _ = public_url(body.url)
    if host_stopped(run, body.url):
        return blocked_result(body.url)
    storage = storage_for(tenant, origin)
    secure = access_configured()
    policy = await access_rpc(tenant, origin, "policy") if secure else {"state": "public"}
    private = policy["state"] != "public"
    if body.catalogue_match and (private or urlsplit(body.url).path.lower().endswith(".pdf")):
        return {"ok": False, "challenge": False, "reason": "unsupported", "html": ""}
    if private: storage = storage_for(tenant, origin, authenticated=True)
    social = any(host == name or host.endswith("." + name) for name in ("facebook.com", "instagram.com", "fb.com", "fb.me"))
    if social: storage.unlink(missing_ok=True)
    if private and policy["state"] != "allowed":
        storage.unlink(missing_ok=True)
        for handle, item in list(held.items()):
            if item.tenant == tenant and item.site == origin: await dispose(handle, save=False)
        reason = policy["state"]
        queue = None
        if reason in {"terms_no", "terms_unknown", "rotation_due"}:
            queue = (await access_rpc(tenant, origin, "queue", reason=reason, task_id=task or None))["queue_id"]
        return {"ok": False, "challenge": True, "needs_person": "needs a person", "queue_id": queue,
                "html": "", "login_status": "login failed"}
    for handle, item in ([] if body.catalogue_match else list(held.items())):
        if item.tenant == tenant and item.site == origin:
            if not item.ready:
                return {"ok": False, "challenge": True, "needs_person": "needs a person", "html": ""}
            if item.url != body.url:
                # Keep the handed-back session, but never use another page's
                # content as the answer to this URL.
                await dispose(handle)
                continue
            # Consume the page the person handed back, in the same live context.
            html = "" if private else await item.page.content()
            result = {"ok": True, "challenge": False, "html": html[:600000], "url": origin,
                      "human_handled": True, "links": []}
            if private:
                result.update(await portal_result(item.page, policy))
            if not private:
                result = await public_result(item.page, result, html, directory, task, price_reader=body.price_reader)
            await dispose(handle)
            return result
    from playwright.async_api import async_playwright
    site = host.removeprefix("www.")
    async with render_slot(site) as slot:
        # A queued read must recheck after the preceding read releases the host.
        if host_stopped(run, body.url):
            return blocked_result(body.url)
        manager = async_playwright()
        browser = None
        context = None
        retained = False
        cancelled = False
        result = {"ok": False, "challenge": False}
        navigation = {"login": False, "human": False}
        try:
            playwright = await manager.__aenter__()
            proxy = os.environ.get("BROWSER_PROXY_URL", "http://base-browser-gateway:3128")
            browser = await playwright.chromium.launch(headless=True, executable_path="/usr/bin/chromium",
                proxy={"server": proxy},
                args=["--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--proxy-bypass-list=<-loopback>"])
            context = await browser.new_context(storage_state=str(storage) if storage.exists() and not body.catalogue_match else None,
                proxy={"server": proxy}, viewport={"width": 1280, "height": 900},
                service_workers="block", accept_downloads=False, user_agent=USER_AGENT)
            denied: set[str] = set()
            if not body.catalogue_match:
                await require_allowed(context, body.url, min(body.max_ms, 10000), run)
            if body.catalogue_match:
                # Page routing does not intercept redirect hops. This transport
                # refuses them, leaving each destination to a new reservation.
                response = await context.request.get(body.url, timeout=min(body.max_ms, 10000), max_redirects=0)
                status = response.status
                if status in {301, 302, 303, 307, 308}:
                    return {"ok": False, "challenge": False, "reason": "redirect",
                            "status": status, "location": response.headers.get("location"), "url": body.url}
                source = (await response.body())[:600000].decode("utf-8", errors="replace")
                challenge = human_check(source) or status in {401, 403, 429}
                if challenge:
                    stop_host(run, body.url)
                # An isolated structured-data read starts no page scripts,
                # subresources, forms or navigation.
                return {"ok": status == 200 and not challenge, "challenge": challenge,
                        "status": status, "url": body.url, "html": "" if challenge else source}
            discovery_document_sent = False
            async def check(route):
                nonlocal discovery_document_sent
                # Discovery budgets one exact public GET. Do not let redirects,
                # frames, scripts, forms or subresources escape that reservation.
                if body.catalogue_match:
                    if (discovery_document_sent or route.request.url != body.url
                            or route.request.resource_type != "document" or route.request.method != "GET"):
                        await route.abort()
                        return
                    discovery_document_sent = True
                try:
                    public_url(route.request.url)
                    target = site_origin(route.request.url)
                    target_host = urlsplit(route.request.url).hostname or ""
                    target_social = any(target_host == name or target_host.endswith("." + name) for name in ("facebook.com", "instagram.com", "fb.com", "fb.me"))
                    human = human_navigation(navigation)
                    if target_social and (human or route.request.method not in {"GET", "HEAD", "OPTIONS"}):
                        raise ValueError("Social pages are anonymous and read-only")
                    if private and target != origin:
                        raise ValueError("Authenticated requests stay on the owning site")
                    # The automated visitor cannot submit a form. Only this
                    # sealed login operation or an authenticated human may do so.
                    if (private or route.request.is_navigation_request()) and route.request.method not in {"GET", "HEAD", "OPTIONS"} and not (navigation["login"] or human):
                        raise ValueError("Forms are not allowed")
                    if navigation["login"] and urlsplit(route.request.url).query:
                        raise ValueError("Login query refused")
                except (ValueError, OSError):
                    denied.add("private or unsupported request")
                    await route.abort()
                    return
                if not human:
                    try:
                        await require_allowed(context, route.request.url, 10000, run)
                    except HostBlocked:
                        denied.add('blocked')
                        await route.abort()
                        return
                await route.continue_()
            await context.route("**/*", check)
            if urlsplit(body.url).path.lower().endswith(".pdf"):
                result = await read_pdf(context, body.url, body.max_ms, run)
            else:
                page = await context.new_page()
                pdfs = []
                def notice_response(response):
                    if (response.status in {403, 429}
                            and response.request.resource_type == "document"
                            and response.frame == page.main_frame
                            and not human_navigation(navigation)):
                        stop_host(run, response.url)
                    if ("application/pdf" in response.headers.get("content-type", "")
                            and response.request.resource_type == "document"):
                        pdfs.append(response.url)
                page.on("response", notice_response)
                try:
                    response = await page.goto(body.url, wait_until="domcontentloaded", timeout=body.max_ms)
                except Exception:
                    if pdfs:
                        result = await read_pdf(context, pdfs[-1], body.max_ms, run)
                    else:
                        raise
                else:
                    html = await page.content()
                    challenge = human_check(html) or bool(response and response.status in {401, 403, 429}) or host_stopped(run, page.url)
                    login_needed = private and (not storage.exists() or await page.locator('input[type="password"]').count() > 0 or bool(response and response.status == 401))
                    if login_needed and not human_check(html) and not bool(response and response.status in {403, 429}):
                        success = await login(page, context, tenant, origin, navigation, task)
                        await access_rpc(tenant, origin, "event", event="logged_in" if success else "login_failed", task_id=task or None)
                        if success:
                            if (await access_rpc(tenant, origin, "policy"))["state"] != "allowed":
                                raise HTTPException(403, "Connection is no longer available.")
                            result = {"ok": True, "challenge": False, "html": "", "login_status": "logged in", "links": []}
                            return result
                        challenge = True
                        html = await page.content()
                    clicked = False
                    if not challenge:
                        clicked = await confirm_age(page, html) if not body.catalogue_match else False
                        await page.evaluate("window.scrollBy(0, Math.min(document.body.scrollHeight, 900))")
                        await page.wait_for_timeout(body.settle_ms)
                        html = await page.content()
                        challenge = human_check(html) or bool(response and response.status in {401, 403, 429}) or host_stopped(run, page.url)
                    public_url(page.url)
                    if challenge:
                        stop_host(run, body.url)
                        stop_host(run, page.url)
                    queue = None
                    if challenge and secure and social:
                        queue = (await access_rpc(tenant, origin, "queue", reason="human_login", task_id=task or None))["queue_id"]
                    if challenge and secure and not social and not body.catalogue_match:
                        queue = await retain(tenant, origin, page, context, browser, manager, storage, body.url, task,
                                             "captcha" if human_check(html) else "human_login", navigation, slot)
                        retained = True
                    result = {"ok": not challenge and bool(response and response.status < 400), "url": origin if private else page.url,
                        "status": response.status if response else None, "challenge": challenge,
                        "needs_person": "needs a person" if challenge else None, "queue_id": queue,
                        "html": "" if challenge or private else html[:600000],
                        "blocked_by_check": challenge, "denied_requests": len(denied), "age_confirmation": clicked}
            if private and result.get("ok") and not urlsplit(body.url).path.lower().endswith(".pdf"):
                result.update(await portal_result(page, policy))
            if private and urlsplit(body.url).path.lower().endswith(".pdf"):
                result = {"ok": False, "html": "", "login_status": "logged in", "error": "Private documents need a configured data reader."}
            if private and (await access_rpc(tenant, origin, "policy"))["state"] != "allowed":
                storage.unlink(missing_ok=True)
                raise HTTPException(403, "Connection is no longer available.")
            if not private:
                result = await public_result(page if result.get('content_type') != 'application/pdf' else None,
                                             result, html if result.get('content_type') != 'application/pdf' else '', directory, task,
                                             price_reader=body.price_reader)
            if result.get("challenge"):
                stop_host(run, body.url)
                result["reason"] = "blocked"
            if body.screenshot and not private and not result.get("challenge") and result.get("ok"):
                result["png_base64"] = base64.b64encode(await page.screenshot(type="png")).decode()
            return result
        except HTTPException:
            raise
        except CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            if isinstance(exc, HostBlocked) or 'blocked' in locals().get('denied', set()) or host_stopped(run, body.url):
                stop_host(run, body.url)
                result = blocked_result(body.url)
                return result
            raise HTTPException(503, "The browser could not read this page; no result is claimed.") from None
        finally:
            try:
                if not retained:
                    # Expiry never waits to save cookies. Bind the existing
                    # storage destination only for a normally completed visit.
                    await cleanup_visit(context, browser, manager, storage,
                        save=not cancelled and not social and not body.catalogue_match, bounded=body.request_timeout_ms is not None)
            finally:
                last_visit[site] = time.monotonic()


async def public_result(page, result, html, directory, task, *, price_reader: bool = False):
    result['evidence'] = await stage(directory.parent.parent, directory, task,
        html=html if result.get('content_type') != 'application/pdf' else '',
        pdf=result.pop('pdf_data', None), page=page if result.get('content_type') != 'application/pdf' else None)
    # The rendered DOM removes CSS-hidden content before parsing; raw
    # HTML is staged as evidence only, never included in the report.
    extraction_started = time.perf_counter()
    source = result.get("html", "")
    if result.get("content_type") != "application/pdf":
        source = await rendered_source(page)
    result["page"] = read_page(result.get("url", ""), source,
        text=result.get("text"), title=result.get("title", ""), links=result.get("links"), status=result.get("status"),
        challenge=bool(result.get("challenge")))
    result["page"]["facts"]["age_gate"] |= bool(result.get("age_confirmation"))
    result["page"]["extraction_ms"] = round((time.perf_counter() - extraction_started) * 1000 + result.pop("pdf_extraction_ms", 0), 3)
    result.pop("html", None)
    # Only Tiger's deterministic price reader requests markup. Normal browser
    # tools keep the bounded page report; the model adapter also uses that report.
    if (price_reader and access_room() == "tiger" and result.get("ok")
            and not result.get("challenge") and result.get("content_type") != "application/pdf"):
        result["html"] = source[:600000]
    result.pop("text", None)
    result.pop("links", None)
    return result


# ACCESS-1. The gateway transports ciphertext; it has no copy of this key.
# Credentials never pass through /render, a model, the history file or a URL.
def access_room():
    room = os.environ.get("ACCESS_ROOM", "base")
    if room not in {"base", "tiger", "rig", "trigger", "tisan", "radix", "market_lab"}:
        raise HTTPException(503, "Browser room is not configured.")
    return room


def access_configured():
    return bool(os.environ.get("ACCESS_BROWSER_KEY_" + access_room().upper()))


def access_key():
    try:
        raw = base64.urlsafe_b64decode(os.environ.get("ACCESS_BROWSER_KEY_" + access_room().upper(), ""))
        if len(raw) != 32: raise ValueError()
        return raw
    except ValueError:
        raise HTTPException(503, "Secure connections are not configured.") from None


def pack(direction, value):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    value = {**value, "at": int(time.time()), "nonce": str(uuid.uuid4())}
    data = AESGCM(access_key()).encrypt(nonce, json.dumps(value).encode(), f"access:browser:{access_room()}:{direction}".encode())
    return {"sealed": base64.urlsafe_b64encode(nonce + data).decode()}


def unpack(direction, value):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        data = base64.urlsafe_b64decode(value["sealed"])
        result = json.loads(AESGCM(access_key()).decrypt(data[:12], data[12:], f"access:browser:{access_room()}:{direction}".encode()))
        uuid.UUID(result["nonce"])
        if abs(time.time() - result["at"]) > 60: raise ValueError()
        return result
    except Exception:
        raise HTTPException(403, "Secure browser request refused.") from None


def site_origin(url):
    host, port = public_url(url)
    scheme = urlsplit(url).scheme
    return scheme + "://" + ("[" + host + "]" if ":" in host else host)


async def access_rpc(tenant, site, action, **values):
    payload = pack("request", {"tenant_id": tenant, "site": site, "action": action, **values})
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.post(os.environ.get("ACCESS_GATEWAY_URL", "http://base-browser-gateway:3000") + "/access/browser", json=payload)
        if response.status_code != 200: raise ValueError()
        result = unpack("response", response.json())
        if result.get("request_nonce") != unpack("request", payload)["nonce"]: raise ValueError()
        return result
    except Exception:
        raise HTTPException(503, "Secure connections are unavailable.") from None


def storage_for(tenant, site, *, authenticated=False):
    host, _ = public_url(site)
    directory = tenant_dir(Path(os.environ.get("BROWSER_DATA_DIR", "/browser-data")), tenant) / "sites"
    directory.mkdir(exist_ok=True)
    return directory / (hashlib.sha256(site.encode()).hexdigest() + (".auth.json" if authenticated else ".json"))


# Bound to a tenant/site and one actual Playwright page, never a debug socket.
@dataclass
class LivePage:
    tenant: str
    site: str
    page: object
    context: object
    browser: object
    manager: object
    storage: Path
    url: str
    task: str
    navigation: dict
    reserved: bool = False
    ready: bool = False
    timer: object = None
    human_timer: object = None


held: dict[str, LivePage] = {}
active_visits: dict[tuple[str, str], asyncio.Task] = {}
access_locks: dict[tuple[str, str], asyncio.Lock] = {}
control_nonces: dict[str, float] = {}


async def dispose(handle, *, save=True):
    item = held.pop(handle, None)
    if not item: return
    if item.timer and item.timer is not asyncio.current_task(): item.timer.cancel()
    stop_human(item)
    try:
        if save: await item.context.storage_state(path=str(item.storage))
    finally:
        try:
            await item.browser.close()
        finally:
            await item.manager.__aexit__(None, None, None)
            if item.reserved: concurrency.release()


async def expire_live(handle):
    await asyncio.sleep(900)
    item = held.get(handle)
    if item:
        async with access_locks.setdefault((item.tenant, item.site), asyncio.Lock()):
            await dispose(handle)
        try:
            await access_rpc(item.tenant, item.site, "event", event="live_expired", task_id=item.task or None)
        except HTTPException:
            pass  # Expiry still closes the browser; no credential was used.


async def retain(tenant, site, page, context, browser, manager, storage, url, task, reason, navigation, slot):
    if len(held) >= 4:
        raise HTTPException(503, "All live views are in use; try again shortly.")
    handle = str(uuid.uuid4())
    result = await access_rpc(tenant, site, "queue", reason=reason, handle=handle, task_id=task or None)
    item = LivePage(tenant, site, page, context, browser, manager, storage, url, task, navigation, reserved=True)
    slot["retained"] = True
    held[handle] = item
    item.timer = asyncio.create_task(expire_live(handle))
    return result["queue_id"]


def authenticator(secret):
    # RFC 6238; enrollment was explicitly confirmed by the account owner.
    import struct
    key_bytes = base64.b32decode(secret.upper(), casefold=True)
    digest = hmac.new(key_bytes, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((int.from_bytes(digest[offset:offset+4], "big") & 0x7fffffff) % 1000000).zfill(6)


async def login(page, context, tenant, site, navigation, task=""):
    grant = await access_rpc(tenant, site, "release", task_id=task or None)
    if grant["state"] != "allowed": return False
    values = grant.pop("credential")
    config = grant["config"]
    try:
        await page.goto(site + config["login_path"], wait_until="domcontentloaded", timeout=30000)
        if site_origin(page.url) != site or human_check(await page.content()): return False
        # No GET form or cross-origin submit, even when the page asks for one.
        button = page.locator(config["submit_selector"])
        safe_form = await button.evaluate("""el => {const f=el.form || el.closest('form');
            return !!f && f.method.toLowerCase()==='post' && new URL(f.action,location.href).origin===location.origin;}""")
        if not safe_form: return False
        navigation["login"] = True
        await page.locator(config["username_selector"]).fill(values["username"])
        await page.locator(config["password_selector"]).fill(values["password"])
        await button.click(timeout=10000)
        await page.wait_for_timeout(1200)
        if human_check(await page.content()): return False
        code = page.locator(config["totp_selector"])
        if await code.count() and await code.first.is_visible():
            if not values.get("totp"): return False
            await code.first.fill(authenticator(values["totp"]))
            await page.locator(config["submit_selector"]).click(timeout=10000)
            await page.wait_for_timeout(1200)
        return site_origin(page.url) == site and not human_check(await page.content()) and await page.locator(config["success_selector"]).count() > 0
    except Exception:
        return False
    finally:
        navigation["login"] = False
        # Remove credential fields before any human screenshot or content output.
        try:
            for name in ("username_selector", "password_selector", "totp_selector"):
                await page.locator(config[name]).evaluate_all("els=>els.forEach(e=>e.value='')")
        except Exception:
            pass
        values.clear()


async def portal_result(page, policy):
    # Models receive only login status. Tiger may read configured numeric fields
    # with plain code; account details and page HTML never reach a model or gold.
    result = {"html": "", "login_status": "logged in"}
    if access_room() != "tiger": return result
    from decimal import Decimal, InvalidOperation
    config = policy.get("config") or {}
    item = {"currency": "USD", "price": None, "quantity": None}
    try:
        for name in ("price", "quantity"):
            selector = config.get(name + "_selector")
            if selector:
                text = (await page.locator(selector).inner_text()).strip().replace("$", "").replace(",", "")
                amount = Decimal(text)
                if not amount.is_finite() or amount < 0: raise ValueError()
                item[name] = str(amount)
        if item["price"] is not None: result["portal_item"] = item
    except (ValueError, InvalidOperation):
        pass
    return result


def human_navigation(navigation):
    if time.monotonic() >= navigation.get("human_until", 0):
        navigation["human"] = False
    return navigation.get("human", False)


def stop_human(item):
    item.navigation.update(human=False, human_until=0)
    if item.human_timer:
        item.human_timer.cancel()
        item.human_timer = None


def human_input(item):
    stop_human(item)
    item.navigation.update(human=True, human_until=time.monotonic() + 120)
    item.human_timer = asyncio.get_running_loop().call_later(120, stop_human, item)


@app.post("/access/control")
async def access_control(request: Request):
    raw = await request.body()
    if len(raw) > 20000: raise HTTPException(413, "Request too large.")
    try:
        body = unpack("control", json.loads(raw))
        tenant = str(uuid.UUID(body["tenant_id"]))
        site = site_origin(body["site"])
    except (ValueError, KeyError):
        raise HTTPException(403, "Secure browser request refused.") from None
    now = time.time()
    for nonce, at in list(control_nonces.items()):
        if at < now-120: control_nonces.pop(nonce, None)
    if body["nonce"] in control_nonces: raise HTTPException(409, "Request already used.")
    control_nonces[body["nonce"]] = now
    if body["action"] == "revoke":
        visiting = active_visits.get((tenant, site))
        if visiting and visiting is not asyncio.current_task():
            visiting.cancel()
            await asyncio.gather(visiting, return_exceptions=True)
    async with access_locks.setdefault((tenant, site), asyncio.Lock()):
        result = {}
        if body["action"] == "revoke":
            for handle, item in list(held.items()):
                if item.tenant == tenant and item.site == site: await dispose(handle, save=False)
            storage_for(tenant, site).unlink(missing_ok=True)
            storage_for(tenant, site, authenticated=True).unlink(missing_ok=True)
            result = {"cleared": True}
        elif body["action"] in {"status", "dismiss"}:
            item = held.get(body["handle"])
            available = bool(item and item.tenant == tenant and item.site == site)
            if body["action"] == "dismiss" and available: await dispose(body["handle"], save=False)
            result = {"available": available} if body["action"] == "status" else {"dismissed": True}
        elif body["action"] == "live":
            item = held.get(body["handle"])
            if not item or item.tenant != tenant or item.site != site:
                result = {"error": "expired"}
            else:
                value = body["input"]
                action = value["action"]
                # Only actual human input extends permission to submit forms.
                # Screenshots/open/polling cannot keep that permission alive.
                human_navigation(item.navigation)
                if action in {"click", "key", "text", "scroll"}: human_input(item)
                if action == "click": await item.page.mouse.click(value["x"], value["y"])
                elif action == "key": await item.page.keyboard.press(value["key"])
                elif action == "text": await item.page.keyboard.insert_text(value["text"])
                elif action == "scroll": await item.page.mouse.wheel(0, value["delta"])
                elif action == "hand_back":
                    stop_human(item)
                    policy = await access_rpc(tenant, site, "policy")
                    item.ready = not human_check(await item.page.content())
                    if policy["state"] not in {"public", "allowed"}: item.ready = False
                    if policy["state"] == "allowed":
                        marker = (policy.get("config") or {}).get("success_selector", "")
                        item.ready = item.ready and bool(marker) and await item.page.locator(marker).count() > 0
                    if item.ready: await item.context.storage_state(path=str(item.storage))
                    result = {"ready": item.ready}
                elif action not in {"open", "frame"}: raise HTTPException(422, "Unsupported action.")
                if action != "hand_back":
                    result = {"png": base64.b64encode(await item.page.screenshot(type="png")).decode()}
        else:
            raise HTTPException(422, "Unsupported action.")
    return pack("result", {**result, "request_nonce": body["nonce"]})
