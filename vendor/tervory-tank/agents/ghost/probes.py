"""Read-only probes of fixed public sites; room evidence arrives through Tank."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from .models import Look, Observation, SITES, Signal

REQUIRED_HEADERS = ("strict-transport-security", "content-security-policy", "x-content-type-options")


def public_addresses(host: str) -> list[str]:
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("The public site did not resolve to a public address.")
    return addresses


def certificate_days(host: str) -> int:
    # Connect to the validated address, retaining hostname validation and SNI.
    address = public_addresses(host)[0]
    with socket.create_connection((address, 443), timeout=8) as sock:
        with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
    expires = ssl.cert_time_to_seconds(cert["notAfter"])
    return int((expires - datetime.now(timezone.utc).timestamp()) / 86400)


class Probes:
    def __init__(self, http: httpx.AsyncClient, tank: object, key: str):
        self.http, self.tank, self.key = http, tank, key

    async def reproduce(self, request: Look) -> Observation:
        target, signal = request.target.value, request.signal
        if target not in SITES or signal in {Signal.errors, Signal.deploy, Signal.ci, Signal.radix}:
            result = await self.tank.call("POST", "/v1/ghost/check", request.model_dump(mode="json"))
            return Observation.model_validate(result)
        host = urlparse(SITES[target]).hostname
        if signal == Signal.dns:
            try:
                await asyncio.wait_for(asyncio.to_thread(public_addresses, host), timeout=10)
                failed = False
            except (OSError, ValueError, TimeoutError):
                failed = True
            return Observation(target=request.target, signal=signal, failing=failed)
        if signal == Signal.certificate:
            try:
                days = await asyncio.wait_for(asyncio.to_thread(certificate_days, host), timeout=12)
                failed = days <= 21
            except (OSError, ValueError, TimeoutError):
                failed = True
            return Observation(target=request.target, signal=signal, failing=failed)
        # Own browser container, never Tiger's. Only structured facts return.
        response = await self.http.post("http://ghost-browser:8000/check",
                                        headers={"X-Tank-Key": self.key},
                                        json=request.model_dump(mode="json"), timeout=40)
        response.raise_for_status()
        return Observation.model_validate(response.json())


async def browser_check(request: Look) -> Observation:
    from playwright.async_api import Error as BrowserError, async_playwright
    target = request.target.value
    if target not in SITES or request.signal not in {Signal.health, Signal.slow, Signal.headers}:
        raise ValueError("Only public read-only checks are allowed.")
    url = SITES[target]
    host = urlparse(url).hostname
    await asyncio.wait_for(asyncio.to_thread(public_addresses, host), timeout=10)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        try:
            # No cookies, login, script execution, downloads, form submission or CAPTCHA solver.
            context = await browser.new_context(java_script_enabled=False, accept_downloads=False,
                                                service_workers="block")

            async def guard(route):
                parsed = urlparse(route.request.url)
                if (route.request.method != "GET" or parsed.scheme != "https" or parsed.hostname != host
                        or parsed.port not in {None, 443} or parsed.username or parsed.password
                        or parsed.query or route.request.redirected_from):
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", guard)
            page = await context.new_page()
            started = time.monotonic()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except BrowserError:
                # A completed attempt with a navigation/network failure is a
                # failing check; a missing browser service remains unavailable.
                return Observation(target=request.target, signal=request.signal, failing=True)
            latency = min(120000, int((time.monotonic() - started) * 1000))
            status = response.status if response else 503
            # Inspect a bounded page in memory; never return its text or header values.
            text = (await page.inner_text("body", timeout=3000))[:20000].lower()
            if any(marker in text for marker in ("verify you are human", "captcha", "checking your browser")):
                return Observation(target=request.target, signal=Signal.human_check, failing=True)
            headers = response.headers if response else {}
            missing = sum(not headers.get(header) for header in REQUIRED_HEADERS)
            failed = status >= 400 or (request.signal == Signal.slow and latency > 3000)
            if request.signal == Signal.headers:
                failed = missing > 0
            return Observation(target=request.target, signal=request.signal, failing=failed,
                               count=missing if request.signal == Signal.headers else 1,
                               latency_ms=latency, status_code=status)
        finally:
            await browser.close()
