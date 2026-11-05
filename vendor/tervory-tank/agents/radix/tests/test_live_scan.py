"""The first live check may call only https://lokd.co.za/. No network in these tests."""

from __future__ import annotations

import importlib
import json
import socket
import sys
from pathlib import Path

import httpx
import pytest

RADIX = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADIX))
live = importlib.import_module("live")

SECRET = "super-secret-cookie-value"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("unit test attempted a real network operation")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.delenv("RADIX_ALLOWED_HOSTS", raising=False)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_only_the_approved_home_page_is_accepted():
    assert live.approved_allow() == frozenset({"lokd.co.za"})
    assert live.home_url("https://lokd.co.za") == "https://lokd.co.za/"
    for url in (
        "https://www.lokd.co.za/",
        "https://lokd.co.za/admin",
        "https://lokd.co.za/?q=1",
        "http://lokd.co.za/",
        "https://evil.example/",
        "https://lokd.co.za.evil.example/",
    ):
        with pytest.raises((live.ScanRefused, live.HostNotAllowed)):
            live.home_url(url)


def test_wider_allowlist_is_refused_before_transport(monkeypatch):
    monkeypatch.setenv("RADIX_ALLOWED_HOSTS", "lokd.co.za, evil.example")
    calls = []
    with _client(lambda request: calls.append(request) or httpx.Response(200)) as http:
        with pytest.raises(live.ScanRefused):
            live.scan_http("https://lokd.co.za/", http=http)
    assert calls == []


def test_other_host_makes_no_request():
    calls = []
    with _client(lambda request: calls.append(request) or httpx.Response(200)) as http:
        with pytest.raises(live.HostNotAllowed):
            live.scan_http("https://example.com/", http=http)
    assert calls == []


def test_missing_headers_are_findings_and_cookie_values_are_dropped():
    def handle(request):
        assert request.url.host == "lokd.co.za"
        return httpx.Response(
            200,
            headers={
                "X-Frame-Options": "SAMEORIGIN",
                "Set-Cookie": f"session={SECRET}; HttpOnly",
                "X-Powered-By": "Example",
            },
        )

    with _client(handle) as http:
        result = live.scan_http("https://LOKD.CO.ZA/", http=http)
    blob = json.dumps(result)
    assert SECRET not in blob
    rules = {item["rule"] for item in result["findings"]}
    assert "missing-content-security-policy" in rules
    assert "missing-strict-transport-security" in rules
    assert "cookie-missing-flags" in rules
    assert "technology-header" in rules
    assert "missing-frame-protection" not in rules
    cookie = next(item for item in result["findings"] if item["rule"] == "cookie-missing-flags")
    assert "session" in cookie["fix"]
    assert SECRET not in cookie["fix"]
    assert result["requests"] == [{"url": "https://lokd.co.za/", "status": 200}]


def test_off_host_redirect_is_not_followed():
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://www.lokd.co.za/go?token=secret"})

    with _client(handle) as http:
        result = live.scan_http("https://lokd.co.za/", http=http)
    assert calls == ["https://lokd.co.za/"]
    assert result["stopped"] == "redirect-not-followed"
    blob = json.dumps(result)
    assert "secret" not in blob
    assert "https://www.lokd.co.za/go" in blob
    assert "www.lokd.co.za" not in live.approved_allow()


def test_one_same_host_redirect_is_followed():
    calls = []

    def handle(request):
        calls.append(str(request.url))
        if str(request.url) == "https://lokd.co.za/":
            return httpx.Response(
                302,
                headers={
                    "Location": "/welcome",
                    "Strict-Transport-Security": "max-age=31536000",
                },
            )
        return httpx.Response(
            200,
            headers={
                "Strict-Transport-Security": "max-age=31536000",
                "Content-Security-Policy": "default-src 'self'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Cross-Origin-Embedder-Policy": "require-corp",
                "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
            },
        )

    with _client(handle) as http:
        result = live.scan_http("https://lokd.co.za/", http=http)
    assert calls == ["https://lokd.co.za/", "https://lokd.co.za/welcome"]
    assert result["stopped"] == "complete"
    assert "missing-frame-protection" not in {item["rule"] for item in result["findings"]}


def test_certificate_notes_do_not_list_other_names():
    cert = {
        "subjectAltName": (("DNS", "lokd.co.za"), ("DNS", "www.lokd.co.za")),
        "notBefore": "Jan  1 00:00:00 2020 GMT",
        "notAfter": "Jan  1 00:00:00 2035 GMT",
    }
    assert live.tls_findings(cert, "TLSv1.3") == []
    mismatch = {
        "subjectAltName": (("DNS", "www.lokd.co.za"),),
        "notBefore": "Jan  1 00:00:00 2020 GMT",
        "notAfter": "Jan  1 00:00:00 2035 GMT",
    }
    findings = live.tls_findings(mismatch, "TLSv1.3")
    assert [item["rule"] for item in findings] == ["tls-name"]
    assert "www.lokd.co.za" not in json.dumps(findings)


def test_csp_nonce_is_not_stored():
    nonce = "nonce-ThisValueMustNotBeStored12"
    def handle(request):
        return httpx.Response(
            200,
            headers={
                "Content-Security-Policy": f"default-src 'self'; script-src 'self' '{nonce}'",
                "Strict-Transport-Security": "max-age=60",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Cross-Origin-Embedder-Policy": "require-corp",
            },
        )

    with _client(handle) as http:
        result = live.scan_http("https://lokd.co.za/", http=http)
    assert nonce not in json.dumps(result)
    assert "nonce-redacted" in result["headers"]["content-security-policy"]


def test_cli_rejects_any_other_url():
    assert live.main(["https://example.com/"]) == 2
