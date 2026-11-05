"""Immutable public widget files and a configured test page on Rig's own host."""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import os
from functools import lru_cache
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

NAME = re.compile(r"tervory-rig\.[a-f0-9]{64}\.umd\.js")

# Retailer test page only: same-origin script/fonts/API, inline widget styles,
# https/data images from feeds. No framing, no base navigation, no plugins.
TEST_PAGE_CSP = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'none'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "worker-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "img-src 'self' data: https: blob:; "
    "connect-src 'self'"
)


def root() -> Path:
    return Path(os.environ.get("RIG_WIDGET_DIR", str(Path(__file__).parent / "widget"))).resolve()


@lru_cache(maxsize=8)
def _verified_asset(path: str, mtime_ns: int, size: int) -> bytes:
    """Read and verify a bundle once per (path, mtime, size); skip re-hash/gzip."""
    body = Path(path).read_bytes()
    name = Path(path).name
    if hashlib.sha256(body).hexdigest() != name.split(".")[1] or len(gzip.compress(body)) > 150 * 1024:
        raise ValueError("Widget bundle failed verification")
    return body


def asset(name: str) -> bytes:
    if not NAME.fullmatch(name):
        raise HTTPException(404, "No such widget bundle")
    path = (root() / name).resolve()
    if path.parent != root() or not path.is_file():
        raise HTTPException(404, "No such widget bundle")
    st = path.stat()
    try:
        return _verified_asset(str(path), st.st_mtime_ns, st.st_size)
    except ValueError:
        raise HTTPException(503, "Widget bundle failed verification") from None


def manifest() -> dict:
    try:
        data = json.loads((root() / "manifest.json").read_text())
        asset(data["file"])
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "Widget bundle is not installed") from None


def mount(application: FastAPI) -> None:
    @application.get("/widget/fonts/{name}", include_in_schema=False)
    async def widget_font(name: str):
        allowed = {"custom-nation.css", "barlow-condensed-latin-600-normal.woff2",
                   "inter-latin-400-normal.woff2", "inter-latin-500-normal.woff2", "inter-latin-600-normal.woff2"}
        if name not in allowed:
            raise HTTPException(404, "No such font")
        body = (Path(__file__).parent / "fonts" / name).read_bytes()
        return Response(body, media_type="text/css" if name.endswith(".css") else "font/woff2",
                        headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*",
                                 "X-Content-Type-Options": "nosniff"})

    @application.get("/widget/manifest.json", include_in_schema=False)
    async def widget_manifest():
        return JSONResponse(manifest(), headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"})

    @application.get("/widget/assets/{name}", include_in_schema=False)
    async def widget_asset(name: str):
        return Response(asset(name), media_type="text/javascript", headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Access-Control-Allow-Origin": "*", "X-Content-Type-Options": "nosniff",
        })

    @application.get("/widget/test", include_in_schema=False)
    async def widget_test(request: Request):
        key = os.environ.get("RIG_WIDGET_PUBLISHABLE_KEY", "")
        if not key:
            raise HTTPException(503, "RIG_WIDGET_PUBLISHABLE_KEY is not configured")
        rig = getattr(request.app.state, "rig", None)
        retailer = None
        if rig is not None:
            from rigbox.service import hash_key
            row = await rig.store.key(hash_key(key))
            if row:
                retailer = await rig.store.retailer(str(row["tenant_id"]))
        theme = html.escape(str((retailer or {}).get("theme") or "neutral"), quote=True)
        fonts = ' fonts="/widget/fonts/custom-nation.css"' if theme == "custom-nation" else ""
        file = html.escape(manifest()["file"], quote=True)
        public_key = html.escape(key, quote=True)
        from rigbox.rooms import api_base, owner
        base = api_base() if owner() else ""
        return HTMLResponse(f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rig · retailer test page</title><style>body{{margin:0}}tervory-rig{{display:block}}</style>
</head><body><tervory-rig api-base="{base}" rig-key="{public_key}" theme="{theme}"{fonts}></tervory-rig>
<script src="/widget/assets/{file}"></script></body></html>''', headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": TEST_PAGE_CSP,
        })
