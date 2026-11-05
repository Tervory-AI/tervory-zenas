"""Base's public-page boundary. No robots rules, identity spoofing or block bypass.

Pacing is shared across Base requests in this Tank process. Blocked sites stay
closed for the tenant; the skill response carries the URL into the existing row.
Only an explicit client connection may subsequently read a blocked page.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import re
from typing import Any
from weakref import WeakKeyDictionary

import httpx
from base_evidence import observations, observe
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Match the existing Base browser identity, including its version and contact.
USER_AGENT = "Tervory/1.0 (+https://tervory.com)"
HOST_INTERVAL = 3.0
BROWSER_RENDER = "http://base-browser-gateway:3000/render"
_records: ContextVar[list[dict[str, str]] | None] = ContextVar("base_public_blocks", default=None)


def recorded_url(url: str | httpx.URL) -> str:
    """Keep a useful public address without credentials, query tokens or fragments."""
    parsed = httpx.URL(url)
    return str(parsed.copy_with(username=None, password=None, query=None, fragment=None))


def host_key(url: str | httpx.URL) -> str:
    # www, scheme and port changes must not turn into another attempt at a block.
    return httpx.URL(url).host.lower().rstrip(".").removeprefix("www.")


def blocked_reason(status: int, headers: httpx.Headers, text: str) -> str | None:
    if status in {401, 407}:
        return "login_required"
    if status == 402:
        return "paywall"
    if status in {403, 429, 451}:
        return f"http_{status}"
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return "active_block"
    html = text[:600_000].lower()
    if any(marker in html for marker in (
        "g-recaptcha", "hcaptcha", "cf-turnstile", "verify you are human", "confirm you are human",
        "checking your browser", "complete the security check", "prove you are human",
        "access denied", "request blocked", "automated requests are blocked",
        "cf-chl-", "_cf_chl_opt", "unusual traffic", "temporarily blocked", "too many requests",
    )):
        return "active_block"
    if re.search(r'<input\b[^>]*\btype\s*=\s*[\"\']?password\b', html) or re.search(
        r"(?:sign in|log in|login) (?:is required|to (?:continue|read|view|access))", html,
    ):
        return "login_required"
    if re.search(r'"isaccessibleforfree"\s*:\s*(?:false|"false")', html) or re.search(
        r"(?:subscribe|purchase a subscription|pay) to (?:continue|read|view|access)", html,
    ):
        return "paywall"
    return None


@dataclass
class HostState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_read: float = 0.0
    blocked: dict[str, dict[str, str]] = field(default_factory=dict)


# Separate event loops in offline tests do not share asyncio locks. Production
# uses Tank's single Uvicorn worker; all tenants share each host's polite pace.
_hosts: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, HostState]] = WeakKeyDictionary()


def host_state(url: str | httpx.URL) -> HostState:
    states = _hosts.setdefault(asyncio.get_running_loop(), {})
    return states.setdefault(host_key(url), HostState())


class PublicPageHTTP:
    def __init__(self, client: httpx.AsyncClient, tenant: Any, services: tuple[str, ...] = ()) -> None:
        self.client = client
        self.tenant = str(tenant)
        self.services = tuple(value.rstrip("/") for value in services if value)
        self.stopped: dict[str, str] | None = None

    def _record(self, block: dict[str, str]) -> None:
        self.stopped = block
        rows = _records.get()
        if rows is not None and block not in rows:
            rows.append(block)

    def _block(self, url: str | httpx.URL, reason: str, aliases: list[httpx.URL]) -> dict[str, str]:
        block = {"status": "blocked", "url": recorded_url(url), "reason": reason, "next": "client_connection"}
        for alias in [httpx.URL(url), *aliases]:
            host_state(alias).blocked[self.tenant] = block
        self._record(block)
        return block

    def _refusal(self, url: httpx.URL, block: dict[str, str], *, browser: bool = False) -> httpx.Response:
        self._record(block)
        return httpx.Response(200 if browser else 403, request=httpx.Request("GET", url),
                              json={"ok": False, "challenge": True, **block})

    async def get(self, url: str | httpx.URL, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def head(self, url: str | httpx.URL, **kwargs: Any) -> httpx.Response:
        return await self.request("HEAD", url, **kwargs)

    async def request(self, method: str, url: str | httpx.URL, **kwargs: Any) -> httpx.Response:
        # Approved find/scrape skills catch errors and continue down their old
        # fallback ladder. Once this call sees a block, none of those attempts
        # may reach a search provider, another URL or the browser.
        if self.stopped is not None:
            return self._refusal(httpx.URL(url), self.stopped, browser=str(url) == BROWSER_RENDER)
        headers = httpx.Headers(kwargs.pop("headers", None))
        headers["User-Agent"] = USER_AGENT
        kwargs["headers"] = headers
        if str(url) == BROWSER_RENDER:
            return await self._browser(method, kwargs)
        # Existing authenticated search APIs are services, not public pages.
        if any(str(url) == base or str(url).startswith(base + "/") for base in self.services):
            return await self.client.request(method, url, **kwargs)
        follow = kwargs.pop("follow_redirects", False)
        kwargs.pop("auth", None)
        current = self.client.build_request(method, url, **kwargs)
        aliases: list[httpx.URL] = []
        for _ in range(9):
            target = current.url
            if target.scheme not in {"http", "https"} or not target.host:
                raise ValueError("Unsupported public page address.")
            state = host_state(target)
            async with state.lock:
                if block := state.blocked.get(self.tenant):
                    return self._refusal(target, block)
                # Public reads never inherit an authenticated session or a
                # caller's cookie jar. Client connections are a separate path.
                for name in ("cookie", "authorization", "proxy-authorization", "x-tank-key"):
                    current.headers.pop(name, None)
                current.headers["User-Agent"] = USER_AGENT
                if target.username or target.password or re.search(r"/(?:login|signin|sign-in)(?:/|$)", target.path, re.I):
                    return self._refusal(target, self._block(target, "login_required", aliases))
                await asyncio.sleep(max(0, state.next_read - asyncio.get_running_loop().time()))
                try:
                    response = await self.client.send(current, follow_redirects=False, auth=None)
                finally:
                    state.next_read = asyncio.get_running_loop().time() + HOST_INTERVAL
                reason = blocked_reason(response.status_code, response.headers, response.text)
                if reason:
                    return self._refusal(target, self._block(target, reason, aliases))
                if response.status_code == 200 and "html" in response.headers.get("content-type", ""):
                    observe(str(target), response.text)
            if not follow or not response.has_redirect_location:
                return response
            aliases.append(target)
            # HTTPX builds the next hop, but it is never sent before its own
            # block check and host slot. All redirects retain our one identity.
            current = response.next_request
            if current is None:
                return response
        raise httpx.TooManyRedirects("Public page redirected too many times.", request=current)

    async def _browser(self, method: str, kwargs: dict[str, Any]) -> httpx.Response:
        target = httpx.URL(str((kwargs.get("json") or {}).get("url", "")))
        if target.scheme not in {"http", "https"} or not target.host:
            raise ValueError("Unsupported public page address.")
        state = host_state(target)
        async with state.lock:
            if block := state.blocked.get(self.tenant):
                return self._refusal(target, block, browser=True)
            if target.username or target.password or re.search(r"/(?:login|signin|sign-in)(?:/|$)", target.path, re.I):
                return self._refusal(target, self._block(target, "login_required", []), browser=True)
            await asyncio.sleep(max(0, state.next_read - asyncio.get_running_loop().time()))
            try:
                response = await self.client.request(method, BROWSER_RENDER, **kwargs)
            finally:
                state.next_read = asyncio.get_running_loop().time() + HOST_INTERVAL
            # A gateway auth/availability failure is not evidence that the site
            # blocked us. Only its page result can establish that.
            if response.status_code != 200:
                return response
            result = response.json()
            reason = "active_block" if result.get("challenge") else blocked_reason(
                int(result.get("status") or 200), httpx.Headers(), str(result.get("html") or ""),
            )
            if reason:
                final = result.get("final_url") or result.get("url") or str(target)
                return self._refusal(target, self._block(final, reason, [target]), browser=True)
            if result.get("ok") and result.get("html"):
                observe(str(result.get("final_url") or result.get("url") or target), str(result["html"]))
            return response


def blocked_result(result: dict[str, Any], blocks: list[dict[str, str]]) -> dict[str, Any]:
    """Existing pipeline fields persist the block; no approved skill is edited."""
    result = dict(result)
    result.update(error="blocked", access_error=blocks[0], blocked_by_check=[block["url"] for block in blocks])
    # Never let a partial scrape or an alternate attempt turn a block into
    # apparently verified evidence. These are the existing scrape/find shapes.
    for key, empty in {"text": "", "html": "", "pages": [], "phones": [], "emails": [], "socials": {}}.items():
        if key in result:
            result[key] = empty
    if "website" in result:
        result["website"] = {"value": None, "source": "blocked", "confidence": 0}
    return result


class BaseBlockResponse:
    """Preserve caught HTTP blocks in Base's normal JSON result, scoped per call."""
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or scope.get("method") != "POST" or not (
            path.startswith("/v1/skala/base.") and path.endswith("/run")
        ):
            await self.app(scope, receive, send)
            return
        blocks: list[dict[str, str]] = []
        token = _records.set(blocks)
        seen: list[dict[str, Any]] = []
        observed_token = observations.set(seen)
        start: Message | None = None
        body = bytearray()

        async def capture(message: Message) -> None:
            nonlocal start
            if not blocks and not seen:
                await send(message)
            elif message["type"] == "http.response.start":
                start = message
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))
                if not message.get("more_body"):
                    assert start is not None
                    result = json.loads(body)
                    result = blocked_result(result, blocks) if blocks else {**result, "_base_observations": seen}
                    output = json.dumps(result).encode()
                    headers = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"]
                    await send({**start, "headers": [*headers, (b"content-length", str(len(output)).encode())]})
                    await send({"type": "http.response.body", "body": output})
            else:
                await send(message)
        try:
            await self.app(scope, receive, capture)
        finally:
            _records.reset(token)
            observations.reset(observed_token)
