"""Tiger's public-address check, shared by rooms accepting client feed URLs."""
import ipaddress
import socket
from urllib.parse import urlparse


def is_public_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost":
        return False
    if not _is_ip(host) and "." not in host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            address = info[4][0]
        except (IndexError, TypeError):
            return False
        if _blocked_address(address):
            return False
    return True


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _blocked_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified)


def check_url(url: str) -> str:
    text = url.strip()
    if len(text) > 2000:
        raise ValueError("url is too long")
    if not is_public_url(text):
        raise ValueError("url not allowed")
    return text
