"""Shopper and dealer pages. Calls this host's /v1/rig. Never logs a key."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from chassis.tenant_db import tenant_scope
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

_PAGE = Path(__file__).resolve().parent / "page"
_V5 = _PAGE / "v5"
_V5_MEDIA = {
    ".css": "text/css",
    ".js": "text/javascript",
    ".svg": "image/svg+xml",
}
_ASSETS = {
    "search.js": "text/javascript",
    "search.css": "text/css",
    "admin.js": "text/javascript",
    "tervory-logo-on-white.png": "image/png",
}


def _v5_files() -> dict[str, Path]:
    root = _V5.resolve()
    files = {}
    for candidate in root.rglob("*"):
        path = candidate.resolve()
        if root in path.parents and path.is_file() and path.suffix.lower() in _V5_MEDIA:
            files[candidate.relative_to(root).as_posix()] = path
    return files


def _v5_objects() -> dict[tuple[str, str], Path]:
    root = (_V5 / "assets").resolve()
    objects = {}
    for name in ("rig.css", "rig.js"):
        for candidate in root.glob(f"*-{name}"):
            path = candidate.resolve()
            if path.parent != root or not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if candidate.name == f"{digest}-{name}":
                objects[digest, name] = path
    return objects


async def _published_page(request: Request) -> tuple[str, str]:
    """Use only the existing configured store, with products in its own store."""
    key = os.environ.get("RIG_WIDGET_PUBLISHABLE_KEY", "")
    service = getattr(request.app.state, "rig", None)
    if not key or service is None:
        return "", ""
    try:
        owner = await service.authenticate(key)
        tenant = str(owner["tenant_id"])
        with tenant_scope(tenant):
            if not await service.store.products(tenant):
                return "", ""
            retailer = await service.store.retailer(tenant) or {}
        return key, str(retailer.get("name") or "")
    except Exception:
        return "", ""


def _v5_asset_url(name: str) -> str:
    digest = hashlib.sha256((_V5 / name).read_bytes()).hexdigest()
    return f"/page/v5/assets/{digest}/{name}"


async def _v5_html(request: Request, skin: str) -> HTMLResponse:
    html = (_V5 / "index.html").read_text(encoding="utf-8")
    key, shop = await _published_page(request)
    safe_skin = "cn" if skin == "cn" else "rig"
    html = html.replace("__RIG_SKIN__", safe_skin)
    html = html.replace("__RIG_KEY__", json.dumps(key).replace("<", "\\u003c"))
    html = html.replace("__RIG_SHOP__", json.dumps(shop).replace("<", "\\u003c"))
    for name in ("rig.css", "rig.js"):
        html = html.replace(f"/page/v5/{name}", _v5_asset_url(name))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


async def _shopper_html(request: Request) -> HTMLResponse:
    html = (_PAGE / "index.html").read_text(encoding="utf-8")
    key, _ = await _published_page(request)
    html = html.replace("__RIG_PUBLISHABLE_KEY__", json.dumps(key).replace("<", "\\u003c"))
    return HTMLResponse(html)


def mount_page(application: FastAPI) -> None:
    @application.get("/", include_in_schema=False)
    async def search_page(request: Request) -> HTMLResponse:
        return await _v5_html(request, "rig")

    @application.get("/product/{product_id}", include_in_schema=False)
    async def product_page(request: Request, product_id: str) -> HTMLResponse:
        del product_id
        return await _shopper_html(request)

    @application.get("/cn-classic", include_in_schema=False)
    async def cn_classic_page(request: Request) -> HTMLResponse:
        """Classic Custom Nation sheet UI. Secondary path."""
        return await _shopper_html(request)

    @application.get("/house", include_in_schema=False)
    async def house_page(request: Request) -> HTMLResponse:
        """Alias of /cn-classic for the house catalogue page."""
        return await _shopper_html(request)

    @application.get("/v5", include_in_schema=False)
    async def v5_page(request: Request, skin: str = "rig") -> HTMLResponse:
        return await _v5_html(request, skin)

    @application.get("/v5/cn", include_in_schema=False)
    async def v5_cn_page(request: Request) -> HTMLResponse:
        return await _v5_html(request, "cn")

    @application.get("/page/v5/assets/{digest}/{name}", include_in_schema=False)
    async def v5_versioned_asset(digest: str, name: str) -> FileResponse:
        # Request values select a server-enumerated, verified object; they never form a path.
        path = _v5_objects().get((digest, name))
        if path is None:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(path, media_type=_V5_MEDIA[Path(name).suffix],
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @application.get("/page/v5/{name:path}", include_in_schema=False)
    async def v5_asset(name: str) -> FileResponse:
        path = _v5_files().get(name)
        if path is None:
            raise HTTPException(status_code=404, detail="Not found")
        media = _V5_MEDIA[path.suffix.lower()]
        return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})

    @application.get("/admin", include_in_schema=False)
    async def admin_page() -> HTMLResponse:
        return HTMLResponse((_PAGE / "admin.html").read_text(encoding="utf-8"))

    @application.get("/page/{name}", include_in_schema=False)
    async def search_asset(name: str) -> FileResponse:
        media = _ASSETS.get(name)
        path = (_PAGE / name).resolve()
        if media is None or path.parent != _PAGE.resolve() or not path.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(path, media_type=media)
