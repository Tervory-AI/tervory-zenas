"""Bounded public-source requests; credentials never leave GitHub and errors omit bodies."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .sources import SOURCES


class SourceError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourceError("Source redirect refused.")


class Client:
    def __init__(self, token: str = ""):
        self.token = token

    def json(self, url: str, body: dict | None = None, *, method: str = "GET"):
        target = urlsplit(url)
        hosts = {urlsplit(source.url).hostname for source in SOURCES}
        if target.scheme != "https" or target.hostname not in hosts or target.username or target.password or target.port not in (None, 443):
            raise SourceError("Source URL is outside Market Lab's allowlist.")
        if not ((method == "GET" and body is None) or (
            method == "POST" and target.hostname == "api.osv.dev"
            and target.path == "/v1/querybatch" and not target.query and isinstance(body, dict)
        )):
            raise SourceError("Only read-only advisory requests are permitted.")
        headers = {"Accept": "application/json", "User-Agent": "tervory-market-lab-supply-chain"}
        if target.hostname == "api.github.com":
            headers["X-GitHub-Api-Version"] = "2022-11-28"
            if self.token:
                headers["Authorization"] = "Bearer " + self.token
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            req = Request(url, data=data, headers=headers, method=method)
            with build_opener(NoRedirect).open(req, timeout=30) as response:
                raw = response.read(25_000_001)
                if len(raw) > 25_000_000:
                    raise SourceError("Source response exceeds limit.")
                return json.loads(raw)
        except HTTPError as error:
            raise SourceError(f"Source {target.hostname} returned HTTP {error.code}.", status=error.code) from None
        except (URLError, TimeoutError, ValueError, OSError):
            raise SourceError(f"Source {target.hostname} unavailable or invalid.") from None
