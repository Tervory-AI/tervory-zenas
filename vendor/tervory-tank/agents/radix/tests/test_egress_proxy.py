"""The egress proxy dials only lokd.co.za:443. These tests do not touch the network."""

from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
from pathlib import Path

import httpx
import pytest
import yaml

RADIX = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADIX))
import egress  # noqa: E402
import net  # noqa: E402

PUBLIC = "8.8.8.8"


@pytest.fixture(autouse=True)
def no_real_dial(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", secrets.token_hex(16))
    real_lookup = socket.getaddrinfo

    def lookup(host, *args, **kwargs):
        # The local test server is the only address these tests may resolve.
        if host in {"127.0.0.1", "::1"}:
            return real_lookup(host, *args, **kwargs)
        raise AssertionError("test attempted a real dial or lookup")

    def refuse(*args, **kwargs):
        raise AssertionError("test attempted a real dial or lookup")

    monkeypatch.setattr(egress, "connect_pinned", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.delenv("RADIX_EGRESS_PROXY", raising=False)
    monkeypatch.delenv("RADIX_EGRESS_REVOKED", raising=False)


def _server(proxy: egress.Proxy) -> egress.Server:
    return egress.Server(proxy).start()


def _exchange(port: int, payload: bytes) -> bytes:
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    try:
        sock.settimeout(2)
        sock.sendall(payload)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 1024:
            chunk = sock.recv(256)
            if not chunk:
                break
            data += chunk
        return data
    finally:
        sock.close()


def _connect(host: str = "lokd.co.za", port: int = 443) -> bytes:
    return f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()


def test_policy_file_has_only_approved_destinations():
    egress.load_policy(RADIX / "egress-policy.json")
    assert net.ROOM_PROXY == egress.ROOM_PROXY == "http://egress-proxy:8080"


@pytest.mark.parametrize("patch", [
    {"destinations": [{"host": "lokd.co.za", "port": 443}, {"host": "www.lokd.co.za", "port": 443}]},
    {"destinations": [{"host": "www.lokd.co.za", "port": 443}]},
    {"destinations": [{"host": "*.lokd.co.za", "port": 443}]},
    {"destinations": [{"host": "1.1.1.1", "port": 443}]},
    {"destinations": [{"host": "lokd.co.za", "port": 80}]},
    {"destinations": []},
    {"ownership": "revoked"},
    {"max_response_bytes": 999999999},
    {"extra": True},
])
def test_invalid_policy_fails_closed(tmp_path, patch):
    data = json.loads((RADIX / "egress-policy.json").read_text(encoding="utf-8"))
    data.update(patch)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(egress.PolicyError):
        egress.load_policy(path)


def test_missing_policy_fails_closed(tmp_path):
    with pytest.raises(egress.PolicyError):
        egress.load_policy(tmp_path / "missing.json")


def test_routable_rejects_special_addresses():
    for address in (
        "127.0.0.1", "10.1.1.1", "169.254.169.254", "192.168.1.1", "0.0.0.0",
        "100.64.0.1", "198.18.0.1", "203.0.113.10", "::1", "2001:db8::1", "fe80::1",
    ):
        assert egress.routable(address) is False
    assert egress.routable(PUBLIC) is True


def test_system_resolve_pins_one_public_address(monkeypatch):
    def answers(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", answers)
    assert egress.system_resolve("lokd.co.za") == PUBLIC


def test_system_resolve_rejects_mixed_answers(monkeypatch):
    def answers(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", answers)
    with pytest.raises(egress.ProxyError):
        egress.system_resolve("lokd.co.za")


def test_system_resolve_does_not_look_up_other_names(monkeypatch):
    def answers(*args, **kwargs):
        raise AssertionError("lookup")

    monkeypatch.setattr(socket, "getaddrinfo", answers)
    with pytest.raises(egress.ProxyError):
        egress.system_resolve("www.lokd.co.za")


def test_approved_tunnel_uses_the_pinned_address_once():
    calls = []
    lookups = {"n": 0}

    def resolve(host):
        assert host == "lokd.co.za"
        lookups["n"] += 1
        if lookups["n"] > 1:
            return "10.0.0.1"
        return PUBLIC

    def connect(address, port):
        calls.append((address, port))
        left, right = socket.socketpair()
        right.close()
        return left

    server = _server(egress.Proxy(resolve=resolve, connect=connect, max_duration_seconds=0.5))
    try:
        data = _exchange(server.port, _connect())
    finally:
        server.close()
    assert data.startswith(b"HTTP/1.1 200")
    assert calls == [(PUBLIC, 443)]
    assert lookups["n"] == 1


@pytest.mark.parametrize("payload", [
    b"CONNECT www.lokd.co.za:443 HTTP/1.1\r\nHost: www.lokd.co.za:443\r\n\r\n",
    b"CONNECT lokd.co.za:80 HTTP/1.1\r\nHost: lokd.co.za:80\r\n\r\n",
    b"CONNECT lokd.co.za:8443 HTTP/1.1\r\nHost: lokd.co.za:8443\r\n\r\n",
    b"CONNECT 1.1.1.1:443 HTTP/1.1\r\nHost: 1.1.1.1:443\r\n\r\n",
    b"CONNECT [::1]:443 HTTP/1.1\r\nHost: [::1]:443\r\n\r\n",
    b"CONNECT 169.254.169.254:80 HTTP/1.1\r\nHost: 169.254.169.254:80\r\n\r\n",
    b"CONNECT tank:8000 HTTP/1.1\r\nHost: tank:8000\r\n\r\n",
    b"CONNECT postgres:5432 HTTP/1.1\r\nHost: postgres:5432\r\n\r\n",
    b"CONNECT user:secret@lokd.co.za:443 HTTP/1.1\r\n\r\n",
    b"GET http://lokd.co.za/ HTTP/1.1\r\nHost: lokd.co.za\r\n\r\n",
    b"GET http://169.254.169.254/latest/meta-data/ HTTP/1.1\r\n\r\n",
])
def test_unapproved_targets_are_refused_before_connect(payload):
    def connect(address, port):
        raise AssertionError((address, port))

    server = _server(egress.Proxy(resolve=lambda host: PUBLIC, connect=connect))
    try:
        data = _exchange(server.port, payload)
    finally:
        server.close()
    assert data.startswith(b"HTTP/1.1 403")
    assert b"secret" not in data
    assert b"169.254.169.254" not in data


def test_private_pin_does_not_connect():
    def connect(address, port):
        raise AssertionError((address, port))

    server = _server(egress.Proxy(resolve=lambda host: "10.0.0.1", connect=connect))
    try:
        data = _exchange(server.port, _connect())
    finally:
        server.close()
    assert data.startswith(b"HTTP/1.1 403")


def test_revoked_policy_does_not_connect():
    def connect(address, port):
        raise AssertionError((address, port))

    server = _server(egress.Proxy(resolve=lambda host: PUBLIC, connect=connect, revoked=True))
    try:
        data = _exchange(server.port, _connect())
    finally:
        server.close()
    assert data.startswith(b"HTTP/1.1 403")


def test_health_does_not_connect():
    def connect(address, port):
        raise AssertionError((address, port))

    server = _server(egress.Proxy(resolve=lambda host: PUBLIC, connect=connect))
    try:
        data = _exchange(server.port, b"GET /health HTTP/1.1\r\nHost: egress-proxy\r\n\r\n")
    finally:
        server.close()
    assert data.startswith(b"HTTP/1.1 200")


def test_rate_limit_stops_before_connect():
    calls = []

    def connect(address, port):
        calls.append((address, port))
        left, right = socket.socketpair()
        right.close()
        return left

    server = _server(egress.Proxy(
        resolve=lambda host: PUBLIC,
        connect=connect,
        max_requests_per_minute=1,
        max_duration_seconds=0.3,
    ))
    try:
        first = _exchange(server.port, _connect())
        second = _exchange(server.port, _connect())
    finally:
        server.close()
    assert first.startswith(b"HTTP/1.1 200")
    assert second.startswith(b"HTTP/1.1 403")
    assert calls == [(PUBLIC, 443)]


def test_concurrency_limit_stops_before_a_second_dial():
    started = threading.Event()
    release = threading.Event()

    def connect(address, port):
        started.set()
        release.wait(2)
        left, right = socket.socketpair()
        right.close()
        return left

    server = _server(egress.Proxy(
        resolve=lambda host: PUBLIC,
        connect=connect,
        max_concurrent=1,
        max_duration_seconds=1,
    ))
    holder = threading.Thread(target=_exchange, args=(server.port, _connect()), daemon=True)
    try:
        holder.start()
        assert started.wait(2)
        refused = _exchange(server.port, _connect())
    finally:
        release.set()
        holder.join(2)
        server.close()
    assert refused.startswith(b"HTTP/1.1 403")


def test_response_is_cut_at_the_byte_limit():
    peers = []

    def connect(address, port):
        left, right = socket.socketpair()
        peers.append(right)
        return left

    server = _server(egress.Proxy(
        resolve=lambda host: PUBLIC,
        connect=connect,
        max_response_bytes=4,
        max_duration_seconds=1,
    ))
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=2)
    try:
        sock.settimeout(2)
        sock.sendall(_connect())
        header = b""
        while b"\r\n\r\n" not in header:
            header += sock.recv(64)
        peers[0].sendall(b"abcdefghij")
        body = b""
        while len(body) < 8:
            try:
                chunk = sock.recv(16)
            except TimeoutError:
                break
            if not chunk:
                break
            body += chunk
    finally:
        sock.close()
        for peer in peers:
            peer.close()
        server.close()
    assert header.startswith(b"HTTP/1.1 200")
    assert body == b"abcd"


def test_app_does_not_dial_without_the_room_proxy(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("direct socket")

    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(net.HostNotAllowed):
        net.Client(allow=frozenset({"lokd.co.za"})).get("https://lokd.co.za/")


def test_app_dials_the_proxy_and_not_the_site(monkeypatch):
    calls = []

    def answers(host, *args, **kwargs):
        assert host == "egress-proxy"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8080))]

    def connect(address, timeout=None, *args, **kwargs):
        calls.append(address)
        raise OSError("proxy stopped")

    monkeypatch.setenv("RADIX_EGRESS_PROXY", net.ROOM_PROXY)
    monkeypatch.setattr(socket, "getaddrinfo", answers)
    monkeypatch.setattr(socket, "create_connection", connect)
    with pytest.raises(httpx.HTTPError):
        net.Client(allow=frozenset({"lokd.co.za", "evil.example"})).get("https://lokd.co.za/")
    assert calls
    assert all(item == ("egress-proxy", 8080) for item in calls)
    with pytest.raises(net.HostNotAllowed):
        net.Client(allow=frozenset({"lokd.co.za", "evil.example"})).get("https://evil.example/")
    with pytest.raises(net.HostNotAllowed):
        net.Client(allow=frozenset({"lokd.co.za"})).get("http://lokd.co.za/")


def test_wrong_proxy_is_refused_before_a_socket(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("socket")

    monkeypatch.setenv("RADIX_EGRESS_PROXY", "http://evil.example:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(net.HostNotAllowed):
        net.Client(allow=frozenset({"lokd.co.za"})).get("https://lokd.co.za/")


def test_room_compose_wires_only_the_proxy_outward():
    box = yaml.safe_load((RADIX / "docker-compose.yml").read_text(encoding="utf-8"))
    app = box["services"]["agent-radix"]
    proxy = box["services"]["egress-proxy"]
    assert app["networks"] == ["gw", "proxy-link"]
    assert "egress" not in app["networks"]
    assert list(proxy["networks"]) == ["proxy-link", "egress"]
    assert proxy["networks"]["proxy-link"] == {"interface_name": "radix-link"}
    assert proxy["networks"]["egress"] == {}
    passive = box["services"]["passive-egress"]
    assert set(passive["networks"]) == {"gw", "proxy-link", "egress"}
    assert passive["networks"]["proxy-link"] == {
        "interface_name": "radix-link", "aliases": ["radix-passive-proxy"]}
    assert passive["networks"]["gw"] == {} and passive["networks"]["egress"] == {}
    assert "gw" not in proxy["networks"]
    assert box["networks"]["gw"]["external"] is True
    assert box["networks"]["gw"]["name"] == "tervory-gw-radix"
    assert box["networks"]["proxy-link"]["internal"] is True
    assert box["networks"]["egress"]["internal"] is False
    assert app["environment"]["RADIX_ALLOWED_HOSTS"] == "lokd.co.za"
    assert app["environment"]["RADIX_EGRESS_PROXY"] == "http://egress-proxy:8080"
    assert "ports" not in app and "ports" not in proxy
    assert proxy["command"] == ["python", "egress.py"]
    for name in ("live.py", "net.py", "app.py"):
        text = (RADIX / name).read_text(encoding="utf-8")
        assert "create_connection" not in text
        assert "getaddrinfo" not in text
