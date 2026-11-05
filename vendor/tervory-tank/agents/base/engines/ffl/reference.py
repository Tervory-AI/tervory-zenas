"""Dated ATF reference files. This engine owns all licence-specific mapping.

Saved platform rows are adopted without downloading them again. An empty
reference may start from the latest complete file advertised by ATF. Later
refreshes download only strictly newer dated files.
"""
from __future__ import annotations

import asyncio
import csv
from datetime import date, datetime, timezone
import hashlib
import hmac
import io
import os
import re
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .atf_gold import Links, LISTING, MAX_BYTES, licence_id
from . import reference_source

USER_AGENT = "Tervory/1.0 (+https://tervory.com)"
STATES = "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
TYPES = {"01", "02", "03", "06", "07", "08", "09", "10", "11"}
_lock = asyncio.Lock()
_last_read = 0.0
_blocked = False
_blocked_files: set[str] = set()


def project(row: dict[str, Any]) -> dict[str, Any]:
    values = {re.sub(r"[\s-]+", "_", str(k).lower().strip()): str(v or "").strip() for k, v in row.items()}
    # Stored ATF imports use both PREM_* and PREMISE_* headers, in either case.
    for suffix in ("street", "city", "state", "zip_code"):
        values["premise_" + suffix] = values.get("premise_" + suffix) or values.get("prem_" + suffix, "")
    number = licence_id(values)
    if not number:
        number = values.get("licence_number") or values.get("license_number", "")
        parts = number.split("-")
        if len(parts) == 6:
            values.update(zip(("lic_regn", "lic_dist", "lic_cnty", "lic_type", "lic_xprdte", "lic_seqn"), parts))
            number = licence_id(values)
    if not re.fullmatch(r"\d-\d{2}-\d{3}-\d{2}-[0-9A-Z]{2}-\d{5}", number):
        raise ValueError("An ATF row has no complete licence number.")
    state = (values.get("premise_state") or values.get("state", "")).upper()
    kind = values["lic_type"].zfill(2)
    if not re.fullmatch(r"[A-Z]{2}", state) or kind not in TYPES:
        raise ValueError("An ATF row has an invalid state or licence type.")
    # ATF eZ Check identifies a licence by its first three and last five digits.
    # Expiry, county and address changes therefore do not create a new licensee.
    key = f"{values['lic_regn']}-{values['lic_dist'].zfill(2)}-{values['lic_seqn'].zfill(5)}"
    return {"licence_key": key, "licence_number": number, "state": state, "licence_type": kind,
            "name": values.get("business_name") or values.get("license_name") or values.get("name", ""),
            "address": {"street": values.get("premise_street") or values.get("address", ""),
                        "city": values.get("premise_city") or values.get("city", ""), "state": state,
                        "zip": values.get("premise_zip_code") or values.get("zip", "")}, "raw": row}


def parse_file(content: bytes) -> list[dict[str, Any]]:
    if not content or len(content) > MAX_BYTES:
        raise ValueError("The ATF file is empty or too large.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    delimiter = "\t" if "\t" in text.splitlines()[0] else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in reader:
        # The published file includes tab-filled separator rows. Ignore only
        # wholly blank rows, never incomplete licence records.
        if not any(str(value or "").strip() for value in row.values()):
            continue
        if None in row or any(value is None for value in row.values()):
            raise ValueError("The ATF file has an incomplete row.")
        entry = project(row)
        if entry["licence_key"] in seen:
            raise ValueError("The ATF file has a duplicate licence identity.")
        seen.add(entry["licence_key"])
        rows.append(entry)
    if not rows:
        raise ValueError("The ATF file has no licence rows.")
    return rows


def changes(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before = {row["licence_key"]: row for row in previous}
    after = {row["licence_key"]: row for row in current}
    result = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        kind = "new" if old is None else "lapsed" if new is None else "moved" if old["address"] != new["address"] else "continuing"
        result.append({"licence_key": key, "change": kind, "previous": old, "current": new})
    return result


def official_url(url: str) -> bool:
    try:
        p = urlsplit(url)
        return p.scheme == "https" and p.hostname in {"atf.gov", "www.atf.gov"} and not (
            p.username or p.password or p.fragment or p.port not in {None, 443}
        )
    except ValueError:
        return False


async def read_public(http: Any, url: str, *, configured: bool = False) -> tuple[bytes, str]:
    global _last_read, _blocked
    source = hashlib.sha256(url.encode()).hexdigest()
    for _ in range(5):
        atf = official_url(url)
        if not configured and not atf:
            raise ValueError("The reference link is not an official ATF address.")
        target, host = await reference_source.destination(url) if configured else (url, None)
        async with _lock:
            if (atf and _blocked) or (configured and source in _blocked_files):
                if not atf:
                    raise ValueError("file-blocked:")
                raise ValueError("blocked: ATF public reading has stopped")
            await asyncio.sleep(max(0, _last_read + 3 - asyncio.get_running_loop().time()))
            try:
                headers = {"User-Agent": USER_AGENT, **({"Host": host} if host else {})}
                options = {"extensions": {"sni_hostname": host}} if host else {}
                async with http.stream("GET", target, headers=headers, timeout=60, follow_redirects=False, **options) as response:
                    if response.status_code in {401, 402, 403, 429, 451}:
                        if atf:
                            _blocked = True
                        if configured:
                            _blocked_files.add(source)
                        raise ValueError(f"{'blocked' if atf else 'file-blocked'}: HTTP {response.status_code}")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_BYTES:
                            raise ValueError("The official reference file is too large.")
                    content = bytes(chunks)
            finally:
                _last_read = asyncio.get_running_loop().time()
            sample = content[:20000].lower()
            if any(marker in sample for marker in (
                b"captcha", b"verify you are human", b"access denied", b"cf-chl-", b"sign in to continue", b"subscribe to read",
            )):
                if atf:
                    _blocked = True
                if configured:
                    _blocked_files.add(source)
                raise ValueError("blocked: " + urlsplit(url)._replace(query="", fragment="").geturl() if atf else "file-blocked:")
        if response.status_code in {301, 302, 303, 307, 308}:
            url = urljoin(url, response.headers.get("location", ""))
            continue
        if response.status_code != 200 or len(content) > MAX_BYTES:
            raise ValueError("The official reference file is unavailable.")
        return content, url
    raise ValueError("The official reference redirected too many times.")


async def refresh(http: Any, since: date | None, today: date) -> dict[str, Any]:
    configured = os.environ.get("BASE_REFERENCE_FILE_URL", "").strip()
    if configured:
        reference_source.validate(configured)
        stamp = re.search(r"(?:^|/)(\d{2})(\d{2})-ffl-list(?:-complete)?\.(?:csv|txt)$",
                          unquote(urlsplit(configured).path), re.I)
        try:
            published = date(2000 + int(stamp[2]), int(stamp[1]), 1) if stamp else None
        except ValueError:
            published = None
        if published is None or published > today:
            raise ValueError("No dated complete ATF list was found on the official page.")
        checked_at = datetime.now(timezone.utc).isoformat()
        if since is not None and published <= since:
            return {"changed": False, "checked_at": checked_at}
        async with reference_source.file_client() as client:
            data, url = await read_public(client, configured, configured=True)
        snapshot = {"published_on": published.isoformat(),
                    "source_url": urlsplit(url)._replace(query="", fragment="").geturl(),
                    "checked_at": checked_at, "sha256": hashlib.sha256(data).hexdigest(), "rows": parse_file(data)}
        return snapshot if since is None else {"changed": True, **snapshot, "files": [snapshot]}
    content, page = await read_public(http, LISTING)
    links = Links()
    links.feed(content.decode("utf-8", errors="replace"))
    files: dict[date, str] = {}
    for href, title in links.links:
        description = (href + " " + title).lower()
        # ATF publishes both file names (.txt) and Drupal download slugs (txt).
        stamp = re.search(r"(?<!\d)(\d{2})(\d{2})-ffl-list-complete\.?txt(?=$|[/?#\s])", description)
        if not stamp:
            continue
        try:
            published = date(2000 + int(stamp[2]), int(stamp[1]), 1)
        except ValueError:
            continue
        target = urljoin(page, href)
        if (since is None or since < published) and published <= today and official_url(target):
            files[published] = target
    checked_at = datetime.now(timezone.utc).isoformat()
    if not files:
        if since is None:
            raise ValueError("No dated complete ATF list was found on the official page.")
        return {"changed": False, "checked_at": checked_at}
    if since is None:
        # Initial population needs only the latest file, not the full archive.
        newest = max(files)
        files = {newest: files[newest]}
    snapshots = []
    for published, target in sorted(files.items()):
        data, url = await read_public(http, target)
        snapshots.append({"published_on": published.isoformat(), "source_url": url,
            "checked_at": checked_at, "sha256": hashlib.sha256(data).hexdigest(), "rows": parse_file(data)})
    return snapshots[0] if since is None else {"changed": True, **snapshots[-1], "files": snapshots}


class ReferenceRequest(BaseModel):
    rows: list[dict[str, Any]] | None = Field(default=None, max_length=2000)
    since: date | None = None
    initial: bool = False


def register(app: Any) -> None:
    router = APIRouter()

    @router.post("/reference/ffl")
    async def reference(body: ReferenceRequest, x_tank_key: str = Header(default="")) -> dict[str, Any]:
        expected = os.environ.get("TANK_KEY", "")
        if not expected or not hmac.compare_digest(expected, x_tank_key):
            raise HTTPException(401, "Base corridor required.")
        try:
            if body.initial:
                if body.rows is not None or body.since is not None:
                    raise ValueError("An initial reference download cannot also project or refresh saved rows.")
                return await refresh(app.state.http, None, date.today())
            if body.rows is not None:
                return {"rows": [project(row) for row in body.rows]}
            if body.since is None:
                raise ValueError("A known reference file date is required.")
            return await refresh(app.state.http, body.since, date.today())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    app.include_router(router)
