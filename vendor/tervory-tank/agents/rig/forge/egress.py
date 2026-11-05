"""Bounded HTTPS CONNECT gateway; exact HF/R2 hosts and pinned public DNS answers."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import sys

MAX_BYTES = 256_000_000
MAX_SECONDS = 180
slots = asyncio.Semaphore(4)


def destinations():
    account = os.environ.get("R2_ACCOUNT_ID", "")
    hosts = {"huggingface.co"}
    if re.fullmatch(r"[a-f0-9]{32}", account):
        hosts.add(account + ".r2.cloudflarestorage.com")
    return hosts


def target(line: str):
    pieces = line.split(" ")
    if len(pieces) != 3 or pieces[0] != "CONNECT" or pieces[2] != "HTTP/1.1":
        raise ValueError("Only HTTPS CONNECT is accepted")
    if pieces[1] not in {host + ":443" for host in destinations()}:
        raise ValueError("Destination is not allowed")
    return pieces[1][:-4]


def public(address):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve(host):
    if host not in destinations():
        raise ValueError("Destination is not allowed")
    answers = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not answers or any(not public(answer[4][0]) for answer in answers):
        raise ValueError("Destination resolved to a nonpublic address")
    return answers[0][4][0]


def private_interface():
    # Choose the interface on the forge-only link, never 0.0.0.0 or the outbound
    # interface. UDP connect selects a route without sending any datagram.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((socket.gethostbyname("rig-forge"), 8010))
        return sock.getsockname()[0]


async def copy(source, destination):
    count = 0
    while data := await source.read(65536):
        count += len(data)
        if count > MAX_BYTES:
            raise ValueError("Tunnel byte limit exceeded")
        destination.write(data)
        await destination.drain()


async def handle(reader, writer):
    upstream = None
    try:
        peer = writer.get_extra_info("peername")[0]
        permitted = {row[4][0] for row in await asyncio.get_running_loop().getaddrinfo(
            "rig-forge", 8010, type=socket.SOCK_STREAM)} | {private_interface()}
        if peer not in permitted:
            raise ValueError("Only the forge may use this proxy")
        async with asyncio.timeout(10):
            headers = await reader.readuntil(b"\r\n\r\n")
        if len(headers) > 8192:
            raise ValueError("Oversized CONNECT request")
        line = headers.split(b"\r\n", 1)[0].decode("ascii")
        if line == "GET /health HTTP/1.1":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
            return
        host = target(line)
        async with slots, asyncio.timeout(MAX_SECONDS):
            pinned = await resolve(host)
            remote, upstream = await asyncio.open_connection(pinned, 443)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            uploads = [asyncio.create_task(copy(reader, upstream)), asyncio.create_task(copy(remote, writer))]
            try:
                done, _ = await asyncio.wait(uploads, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in uploads:
                    task.cancel()
                await asyncio.gather(*uploads, return_exceptions=True)
    except Exception:
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
    finally:
        if upstream:
            upstream.close()
        writer.close()


async def main():
    server = await asyncio.start_server(handle, private_interface(), 8080, limit=8192)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if sys.argv[1:] == ["health"]:
        with socket.create_connection((private_interface(), 8080), timeout=3) as conn:
            conn.sendall(b"GET /health HTTP/1.1\r\nHost: rig-forge-egress\r\n\r\n")
            if b"200 OK" not in conn.recv(128):
                raise SystemExit(1)
    else:
        asyncio.run(main())
