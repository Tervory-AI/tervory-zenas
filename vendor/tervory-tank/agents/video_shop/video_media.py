"""HTTPS download with public DNS pinned at the transport; no redirects."""
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit

import httpcore

from .video_engine import MAX_BYTES


def download(url, limit=MAX_BYTES):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or len(url) > 2000):
        raise ValueError('url')
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(entry[4][0]) for entry in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
        raise ValueError('url_address')

    class PinnedBackend(httpcore.SyncBackend):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            # Change only TCP resolution. HTTP Host, TLS SNI and certificate
            # verification still use the original URL hostname inside httpcore.
            return super().connect_tcp(str(ips[0]), port, timeout, local_address, socket_options)

    try:
        with httpcore.ConnectionPool(ssl_context=ssl.create_default_context(),
                                     network_backend=PinnedBackend(), max_connections=1,
                                     max_keepalive_connections=0) as pool:
            with pool.stream('GET', url, headers={'Accept-Encoding': 'identity'},
                             extensions={'timeout': {'connect': 15, 'read': 15, 'write': 15, 'pool': 15}}) as response:
                if response.status != 200:
                    raise ValueError('url_response')
                result = bytearray()
                deadline = time.monotonic() + 45
                for chunk in response.iter_stream():
                    result.extend(chunk)
                    if len(result) > limit or time.monotonic() > deadline:
                        raise ValueError('media_limit')
                return bytes(result)
    except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.TimeoutException):
        raise ValueError('url_response') from None
