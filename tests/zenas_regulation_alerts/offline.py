import socket
import sys
import ipaddress
import pytest


def offline(event, args):
    if event == "socket.connect" and args[0].family in (
        socket.AF_INET,
        socket.AF_INET6,
    ):
        try:
            allowed = ipaddress.ip_address(args[1][0]).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise RuntimeError("External network disabled for offline tests")


sys.addaudithook(offline)
raise SystemExit(pytest.main(sys.argv[1:]))
