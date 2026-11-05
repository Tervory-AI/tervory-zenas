"""Passive live check of the one approved host.

The approved name is lokd.co.za, HTTPS on port 443. This does not crawl, does
not follow a redirect to any other host, and does not send attack payloads.
A real check dials the room proxy. It does not open a socket to the site.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

import egress
from net import Client, HostNotAllowed, allowed_hosts, require_allowed

APPROVED_HOST = "lokd.co.za"
HOME_URL = "https://lokd.co.za/"
USER_AGENT = "Tervory-Radix/0.1"
_REDIRECTS = {301, 302, 303, 307, 308}
_HEADER_RULES: tuple[tuple[str, str, str, str], ...] = (
    (
        "strict-transport-security",
        "medium",
        "missing-strict-transport-security",
        "Send Strict-Transport-Security so browsers stay on HTTPS.",
    ),
    (
        "content-security-policy",
        "medium",
        "missing-content-security-policy",
        "Send a Content-Security-Policy header.",
    ),
    (
        "x-frame-options",
        "medium",
        "missing-frame-protection",
        "Send X-Frame-Options or a Content-Security-Policy frame-ancestors directive.",
    ),
    (
        "x-content-type-options",
        "low",
        "missing-content-type-options",
        "Send X-Content-Type-Options: nosniff.",
    ),
    (
        "referrer-policy",
        "low",
        "missing-referrer-policy",
        "Send a Referrer-Policy header.",
    ),
    (
        "cross-origin-resource-policy",
        "low",
        "missing-cross-origin-resource-policy",
        "Send a Cross-Origin-Resource-Policy header.",
    ),
    (
        "cross-origin-embedder-policy",
        "low",
        "missing-cross-origin-embedder-policy",
        "Send a Cross-Origin-Embedder-Policy header.",
    ),
)
_SHOW = {rule[0] for rule in _HEADER_RULES}
_HIDDEN_HEADERS = {"set-cookie", "cookie", "authorization", "proxy-authorization"}


class ScanRefused(PermissionError):
    """The request is outside the one approved host."""


def approved_allow() -> frozenset[str]:
    return frozenset({APPROVED_HOST})


def assert_exact_environment() -> None:
    """A wider process allowlist must fail before any lookup."""
    raw = os.environ.get("RADIX_ALLOWED_HOSTS", "")
    if raw.strip() and allowed_hosts(raw) != approved_allow():
        raise ScanRefused("RADIX_ALLOWED_HOSTS is not exactly lokd.co.za")


def home_url(url: str) -> str:
    """Accept only the approved home page."""
    host = require_allowed(url, approved_allow())
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.path not in ("", "/") or parsed.query:
        raise ScanRefused("the first request is only https://lokd.co.za/")
    if host != APPROVED_HOST:
        raise ScanRefused("host not approved")
    return HOME_URL


def _finding(tool: str, severity: str, rule: str, fix: str, url: str) -> dict[str, Any]:
    return {
        "tool": tool,
        "severity": severity,
        "file": url,
        "line": None,
        "rule": rule,
        "fix": fix,
    }


def _safe_location(current: str, location: str) -> str:
    if not location or not location.isascii():
        return "(unreadable redirect)"
    try:
        joined = urljoin(current, location.strip())
        parsed = urlsplit(joined)
    except ValueError:
        return "(unreadable redirect)"
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host:
        return "(unreadable redirect)"
    path = parsed.path or "/"
    return f"{parsed.scheme}://{host}{path}"[:200]


def _same_host_target(current: str, location: str) -> str | None:
    if not location or not location.isascii() or "\\" in location:
        return None
    try:
        joined = urljoin(current, location.strip())
    except ValueError:
        return None
    parsed = urlsplit(joined)
    if parsed.scheme != "https" or parsed.query or parsed.username or parsed.password:
        return None
    try:
        host = require_allowed(joined, approved_allow())
    except HostNotAllowed:
        return None
    if host != APPROVED_HOST or len(parsed.path) > 200:
        return None
    return f"https://{host}{parsed.path or '/'}"


def _hsts_disabled(value: str) -> bool:
    match = re.search(r"max-age=(\d+)", value.lower().replace(" ", ""))
    return bool(match) and int(match.group(1)) == 0


def _cookie_findings(response: httpx.Response, url: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for raw in response.headers.get_list("set-cookie"):
        parts = [part.strip() for part in raw.split(";") if part.strip()]
        if not parts:
            continue
        name = parts[0].split("=", 1)[0].strip()[:80]
        flags = {part.split("=", 1)[0].strip().lower() for part in parts[1:]}
        missing = [
            label
            for label, present in (
                ("Secure", "secure" in flags),
                ("HttpOnly", "httponly" in flags),
                ("SameSite", "samesite" in flags),
            )
            if not present
        ]
        if not missing or not name:
            continue
        joined = ", ".join(missing)
        findings.append(
            _finding(
                "scan.headers",
                "medium" if "Secure" in missing or "HttpOnly" in missing else "low",
                "cookie-missing-flags",
                f"The {name} cookie is missing {joined}.",
                url,
            )
        )
    return findings


def _header_findings(response: httpx.Response, url: str, *, full: bool) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    headers = response.headers
    if full:
        framed = "frame-ancestors" in headers.get("content-security-policy", "").lower()
        for name, severity, rule, fix in _HEADER_RULES:
            if name == "x-frame-options" and framed:
                continue
            if name == "strict-transport-security":
                value = headers.get(name, "")
                if value and not _hsts_disabled(value):
                    continue
                if value:
                    findings.append(
                        _finding(
                            "scan.headers",
                            "medium",
                            "strict-transport-security-disabled",
                            "Strict-Transport-Security is present with max-age=0, which turns it off.",
                            url,
                        )
                    )
                    continue
            if name == "x-content-type-options":
                value = headers.get(name, "")
                if value.lower() == "nosniff":
                    continue
            elif name == "x-frame-options":
                value = headers.get(name, "")
                if value.lower() in {"deny", "sameorigin"}:
                    continue
            elif headers.get(name):
                continue
            findings.append(_finding("scan.headers", severity, rule, fix, url))
        powered = headers.get("x-powered-by", "")
        if powered:
            findings.append(
                _finding(
                    "scan.headers",
                    "low",
                    "technology-header",
                    "Remove the X-Powered-By header.",
                    url,
                )
            )
    elif not headers.get("strict-transport-security"):
        findings.append(
            _finding(
                "scan.headers",
                "medium",
                "missing-strict-transport-security",
                "Send Strict-Transport-Security on the first response so browsers stay on HTTPS.",
                url,
            )
        )
    return findings


def _public_header(value: str) -> str:
    """Keep policy text. A nonce is a secret and is never stored."""
    redacted = re.sub(r"nonce-[A-Za-z0-9+/=_-]+", "nonce-redacted", value)
    return redacted[:2000]


def _shown_headers(response: httpx.Response) -> dict[str, str]:
    shown: dict[str, str] = {}
    for name in _SHOW:
        value = response.headers.get(name)
        if value and name not in _HIDDEN_HEADERS:
            shown[name] = _public_header(value)
    return shown


def scan_http(url: str, *, http: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch the approved home page once. Follow one same-host HTTPS redirect."""
    assert_exact_environment()
    current = home_url(url)
    client = Client(allow=approved_allow(), http=http)
    requests: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    shown: dict[str, str] = {}
    stopped = "complete"
    for hop in range(2):
        response = client.get(
            current,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )
        try:
            requests.append({"url": current, "status": response.status_code})
            findings.extend(_cookie_findings(response, current))
            location = response.headers.get("location", "")
            redirecting = response.status_code in _REDIRECTS
            nxt = _same_host_target(current, location) if redirecting and hop == 0 else None
            if nxt and nxt != current:
                if not response.headers.get("strict-transport-security"):
                    findings.append(
                        _finding(
                            "scan.headers",
                            "medium",
                            "missing-strict-transport-security",
                            "Send Strict-Transport-Security on the first response so browsers stay on HTTPS.",
                            current,
                        )
                    )
                current = nxt
                continue
            if redirecting:
                findings.append(
                    _finding(
                        "scan.headers",
                        "low",
                        "redirect-not-followed",
                        f"The response points to {_safe_location(current, location)}, which this scan does not open.",
                        current,
                    )
                )
                stopped = "redirect-not-followed"
            findings.extend(_header_findings(response, current, full=not redirecting))
            shown = _shown_headers(response)
            break
        finally:
            response.close()
    return {
        "host": APPROVED_HOST,
        "url": HOME_URL,
        "requests": requests,
        "stopped": stopped,
        "headers": shown,
        "findings": findings,
    }


def tls_findings(cert: dict[str, Any] | None, protocol: str | None, url: str = HOME_URL, *, host: str = APPROVED_HOST) -> list[dict[str, Any]]:
    """Read a verified certificate dict. Do not include other names on it."""
    findings: list[dict[str, Any]] = []
    if protocol not in {"TLSv1.2", "TLSv1.3"}:
        findings.append(
            _finding(
                "scan.tls",
                "high",
                "tls-version",
                "The handshake did not use TLS 1.2 or TLS 1.3.",
                url,
            )
        )
    if not cert:
        findings.append(
            _finding(
                "scan.tls",
                "high",
                "tls-certificate",
                "The handshake did not present a verified certificate.",
                url,
            )
        )
        return findings
    names = {
        str(value).lower().rstrip(".")
        for kind, value in cert.get("subjectAltName", ())
        if kind == "DNS"
    }
    name_matches = host in names
    if not name_matches:
        try:
            name_matches = any(ssl._dnsname_match(name, host) for name in names)
        except (ssl.CertificateError, ValueError):
            pass
    if not name_matches:
        findings.append(
            _finding(
                "scan.tls",
                "high",
                "tls-name",
                f"The certificate name does not match {host}.",
                url,
            )
        )
    now = datetime.now(timezone.utc)
    try:
        starts = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notBefore"]), timezone.utc)
        ends = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)
    except (KeyError, ValueError):
        findings.append(
            _finding(
                "scan.tls",
                "high",
                "tls-dates",
                "The certificate dates could not be read.",
                url,
            )
        )
        return findings
    if starts > now or ends <= now:
        findings.append(
            _finding(
                "scan.tls",
                "high",
                "tls-expired",
                "The certificate is outside its valid dates.",
                url,
            )
        )
    elif (ends - now).days < 14:
        findings.append(
            _finding(
                "scan.tls",
                "medium",
                "tls-expiring",
                "The certificate expires within 14 days.",
                url,
            )
        )
    return findings


def summarize(findings: list[dict[str, Any]]) -> str:
    count = len(findings)
    high = sum(1 for item in findings if item.get("severity") == "high")
    noun = "finding" if count == 1 else "findings"
    if count == 0:
        return "Checked https://lokd.co.za/. No header or certificate findings."
    return (
        f"Checked https://lokd.co.za/. {count} {noun}, {high} high. "
        "This is a passive check of one host."
    )


def _tls_summary(cert: dict[str, Any] | None, protocol: str | None) -> dict[str, Any]:
    ends = ""
    if cert and cert.get("notAfter"):
        ends = datetime.fromtimestamp(
            ssl.cert_time_to_seconds(str(cert["notAfter"])), timezone.utc
        ).date().isoformat()
    findings = tls_findings(cert, protocol)
    return {
        "protocol": protocol,
        "not_after": ends,
        "name_matches": not any(item["rule"] == "tls-name" for item in findings),
        "findings": findings,
    }


def run(url: str = HOME_URL, *, http: httpx.Client | None = None) -> dict[str, Any]:
    assert_exact_environment()
    result = scan_http(url, http=http)
    if http is None:
        cert, protocol = egress.read_certificate()
        tls = _tls_summary(cert, protocol)
        result["findings"] = [*result["findings"], *tls["findings"]]
        result["tls"] = {
            "protocol": tls["protocol"],
            "not_after": tls["not_after"],
            "name_matches": tls["name_matches"],
        }
    else:
        result["tls"] = None
    result["summary"] = summarize(result["findings"])
    result["txt_present"] = None
    return result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    url = HOME_URL
    if args and args[0] not in {HOME_URL, f"https://{APPROVED_HOST}"}:
        print("only https://lokd.co.za/ is approved", file=sys.stderr)
        return 2
    if args[:1] == [f"https://{APPROVED_HOST}"]:
        url = HOME_URL
    try:
        result = run(url)
    except (ScanRefused, HostNotAllowed, egress.ProxyError, httpx.HTTPError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
