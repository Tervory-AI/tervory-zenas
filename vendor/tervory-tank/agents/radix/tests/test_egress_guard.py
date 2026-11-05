"""RX-2 preparation: application guard tests, with no network or database.

These are not proof of container egress isolation or scan accuracy.
"""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import httpx
import pytest

RADIX = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("radix_rx2_net", RADIX / "net.py")
assert _spec is not None and _spec.loader is not None
net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(net)

ALLOW = frozenset({"stage.example"})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("unit test attempted a real network operation")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.delenv("RADIX_ALLOWED_HOSTS", raising=False)


def test_empty_environment_is_sealed():
    assert net.allowed_hosts() == frozenset()
    for url in ("https://stage.example/", "http://1.1.1.1/", "http://tank:8000/"):
        with pytest.raises(net.HostNotAllowed):
            net.Client().get(url)


def test_exact_names_are_normalized():
    assert net.allowed_hosts(" STAGE.Example.,other.example, ") == frozenset(
        {"stage.example", "other.example"}
    )
    assert net.require_allowed("https://STAGE.Example./", ALLOW) == "stage.example"
    assert net.require_allowed("http://stage.example:80/", ALLOW) == "stage.example"
    assert net.require_allowed("https://stage.example:443/", ALLOW) == "stage.example"


@pytest.mark.parametrize("entry", [
    "*.example", ".example", "stage.example..", "https://stage.example",
    "stage.example:443", "user@stage.example", "stage.example/path",
    "127.0.0.1", "127.1", "2130706433", "0x7f000001", "[::1]",
    "0x7f.0x0.0x0.0x1", "localhost", "tank", "-stage.example", "stage_.example", "stagé.example",
    "stage.example\n.other", "stage.example%00", "a" * 64 + ".example",
])
def test_invalid_configuration_is_rejected(entry):
    with pytest.raises(ValueError, match="exact DNS names"):
        net.allowed_hosts(entry)
    with pytest.raises(ValueError):
        net.Client(allow=frozenset({entry}))


@pytest.mark.parametrize("url", [
    "", "/relative", "//stage.example/", "ftp://stage.example/",
    "https://stage.example.evil.example/", "https://sub.stage.example/",
    "https://evil.example/?next=https://stage.example/",
    "https://user:password@stage.example/", "https://@stage.example/",
    "https://stage.example:8443/", "http://stage.example:443/",
    "https://stage.example:/", "https://stage.example:0443/",
    "https://stage.example:+443/", "https://stage.example:99999/",
    "https://stage.example:bad/", "https://stage.example/#fragment",
    "https://stage.example/#", "https://stage.example..",
    "https://%73tage.example/", "https://stage.example%2f.evil.example/",
    "https://stage.example\\@evil.example/", "https://stage.example\n/",
    " https://stage.example/", "https://stage.example/with space",
    "https://stage.example/\x7f", "https://stagé.example/",
    "https://127.0.0.1/", "https://[::1]/", "https://[invalid/",
])
def test_bad_urls_are_refused_before_transport(url):
    calls = []
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200)
    )) as http:
        with pytest.raises(net.HostNotAllowed):
            net.Client(allow=ALLOW, http=http).get(url)
    assert calls == []


def test_empty_host_can_never_be_authorized():
    with pytest.raises(net.HostNotAllowed):
        net.require_allowed("", frozenset({""}))


def test_permitted_request_reaches_only_mock_target():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text="ok")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = net.Client(allow=ALLOW, http=http).get(
            "https://stage.example/path", params={"q": "two words"}
        )
    assert result.text == "ok"
    assert len(calls) == 1
    assert str(calls[0].url) == "https://stage.example/path?q=two+words"


@pytest.mark.parametrize("location", [
    "https://evil.example/", "http://169.254.169.254/latest/meta-data/",
    "http://tank:8000/health", "/same-host",
])
def test_injected_client_cannot_enable_redirects(location):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": location})

    with httpx.Client(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        result = net.Client(allow=ALLOW, http=http).get("https://stage.example/")
    assert result.status_code == 302
    assert calls == ["https://stage.example/"]


@pytest.mark.parametrize("options", [
    {"follow_redirects": True},
    {"extensions": {"sni_hostname": "evil.example"}},
    {"auth": ("user", "password")},
])
def test_extra_request_paths_are_refused(options):
    with pytest.raises(ValueError):
        net.Client(allow=ALLOW).get("https://stage.example/", **options)


@pytest.mark.parametrize("default_header", [False, True])
def test_host_header_override_is_refused(default_header):
    calls = []
    headers = {"Host": "evil.example"}
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200)
        ),
        headers=headers if default_header else None,
    ) as http:
        with pytest.raises(net.HostNotAllowed):
            net.Client(allow=ALLOW, http=http).get(
                "https://stage.example/", **({} if default_header else {"headers": headers})
            )
    assert calls == []


def test_guard_disables_environment_proxies(monkeypatch):
    original_client = httpx.Client
    configuration = []

    def make_client(**kwargs):
        configuration.append(dict(kwargs))
        kwargs.pop("proxy", None)
        return original_client(
            **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200))
        )

    monkeypatch.setenv("HTTPS_PROXY", "http://unapproved.example:8080")
    monkeypatch.delenv("RADIX_EGRESS_PROXY", raising=False)
    with pytest.raises(net.HostNotAllowed):
        net.Client(allow=frozenset({"lokd.co.za"})).get("https://lokd.co.za/")
    assert configuration == []

    monkeypatch.setenv("RADIX_EGRESS_PROXY", net.ROOM_PROXY)
    monkeypatch.setattr(net.httpx, "Client", make_client)
    assert net.Client(allow=frozenset({"lokd.co.za"})).get("https://lokd.co.za/").status_code == 200
    assert configuration == [{
        "proxy": "http://egress-proxy:8080",
        "timeout": 30,
        "trust_env": False,
        "follow_redirects": False,
    }]
    assert configuration[0]["proxy"] == "http://egress-proxy:8080"
