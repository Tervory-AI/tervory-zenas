"""Fixed-repository, GET-only readers for Command's founder corridor.

No caller-selected host, repository, ref, HTTP method, credential or redirect.
GitHub responses are data; only selected fields enter the saved tool result.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import HTTPException

REPOSITORY = "Tervory-AI/tervory-tank"
API = "https://api.github.com/repos/" + REPOSITORY
SHA = r"[0-9a-f]{40}"
READ_PATH = re.compile(rf"(?:compare/{SHA}\.\.\.{SHA}|git/ref/heads/main|git/commits/{SHA}|pulls(?:/[1-9][0-9]*(?:/files)?)?|commits/{SHA}/(?:check-runs|status)|git/trees/(?:main|{SHA})|git/blobs/{SHA})")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 64_000
MAX_CHECK_PAGES = 10
FAILURE = "The action was refused or failed. It has not been retried."


def configured() -> bool:
    return bool(os.environ.get("TANK_GITHUB_READ_TOKEN", "").strip())


def validate_path(path: str) -> None:
    segments = path.split("/")
    if (not path or len(path) > 1000 or any(p in {"", ".", ".."} for p in segments)
            or any(c in path for c in "\\%?#:") or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or not (path.startswith("docs/") or path.endswith(".md"))):
        raise ValueError("The action arguments are invalid")


@asynccontextmanager
async def github():
    token = os.environ.get("TANK_GITHUB_READ_TOKEN", "").strip()
    if not token:
        raise HTTPException(403, "This tool is not available to this user")
    # Keep the stack's proxy/CA configuration. Never follow a redirect with this header.
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Tervory-Tank",
    }) as client:
        yield client


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> tuple[Any, bool]:
    if not READ_PATH.fullmatch(path):
        raise HTTPException(422, "The action arguments are invalid")
    try:
        # There is deliberately no method argument or arbitrary request/URL adapter.
        async with client.stream("GET", API + "/" + path, params=params, follow_redirects=False) as response:
            if response.status_code != 200:
                raise HTTPException(502, FAILURE)
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise HTTPException(413, "Tool response is too large; outcome needs reconciliation")
            return json.loads(body), 'rel="next"' in response.headers.get("link", "")
    except (httpx.HTTPError, ValueError):
        # Neither a response body, request header nor exception string is returned/logged.
        raise HTTPException(502, FAILURE) from None


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(SHA, value):
        raise HTTPException(502, FAILURE)
    return value


def _rows(body: Any, key: str | None = None) -> list[dict]:
    rows = body.get(key) if key and isinstance(body, dict) else body if key is None else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise HTTPException(502, FAILURE)
    return rows


def _visible(result: dict) -> dict:
    # Existing Command presentation already displays 'text'. Include the complete
    # page here because its generic collection preview shows only the first five.
    result = {**result, "text": json.dumps(result, ensure_ascii=False, indent=2)}
    if len(json.dumps(result).encode()) > 240_000:
        raise HTTPException(413, "Tool response is too large; outcome needs reconciliation")
    return result


async def _checks(client: httpx.AsyncClient, sha: str) -> dict:
    runs, statuses = [], []
    complete = True
    for endpoint, field, target, keys in (
        ("check-runs", "check_runs", runs, ("name", "status", "conclusion")),
        ("status", "statuses", statuses, ("context", "state")),
    ):
        for page in range(1, MAX_CHECK_PAGES + 1):
            body, more = await _get(client, f"commits/{sha}/{endpoint}", {"per_page": 100, "page": page})
            target.extend({key: row.get(key) for key in keys} for row in _rows(body, field))
            if not more:
                break
        else:
            complete = False
    states = [row["state"] for row in statuses]
    failed = any(row["conclusion"] in {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"} for row in runs) or any(s in {"error", "failure"} for s in states)
    passed = (all(row["status"] == "completed" and row["conclusion"] in {"success", "neutral", "skipped"} for row in runs)
              and all(s == "success" for s in states))
    state = "failure" if failed else "unknown" if not complete or not (runs or statuses) else "success" if passed else "pending"
    return {"checks": runs, "statuses": statuses, "checks_complete": complete,
            "checks_state": state, "checks_green": None if state == "unknown" else state == "success"}


async def _pr(client: httpx.AsyncClient, row: dict) -> dict:
    try:
        number, title, draft, head = row["number"], row["title"], row["draft"], row["head"]
        sha, branch = _sha(head["sha"]), head["ref"]
        if (type(number) is not int or number < 1 or not isinstance(title, str)
                or type(draft) is not bool or not isinstance(branch, str)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise HTTPException(502, FAILURE) from None
    return {"number": number, "title": title, "draft": draft, "branch": branch, "head_sha": sha,
            "url": f"https://github.com/{REPOSITORY}/pull/{number}", **await _checks(client, sha)}


async def open_prs(page: int, limit: int) -> dict:
    async with github() as client:
        body, more = await _get(client, "pulls", {"state": "open", "page": page, "per_page": limit})
        semaphore = asyncio.Semaphore(4)

        async def read(row):
            async with semaphore:
                return await _pr(client, row)

        prs = await asyncio.gather(*(read(row) for row in _rows(body)))
    return _visible({"repository": REPOSITORY, "items": prs, "count": len(prs),
                     "page": page, "has_more": more, "next_page": page + 1 if more else None})


async def pull_request(number: int, page: int, limit: int) -> dict:
    async with github() as client:
        body, _ = await _get(client, f"pulls/{number}")
        if not isinstance(body, dict):
            raise HTTPException(502, FAILURE)
        result = await _pr(client, body)
        files, more = await _get(client, f"pulls/{number}/files", {"page": page, "per_page": limit})
    # File content/patches and remote URLs are deliberately not part of this tool.
    rows = [{key: row[key] for key in ("filename", "previous_filename", "status", "additions", "deletions", "changes") if key in row}
            for row in _rows(files)]
    return _visible({**result, "files": rows, "page": page, "has_more": more,
                     "next_page": page + 1 if more else None})


async def read_file(path: str) -> dict:
    validate_path(path)
    async with github() as client:
        tree = "main"
        parts = path.split("/")
        for index, name in enumerate(parts):
            body, _ = await _get(client, "git/trees/" + tree)
            if not isinstance(body, dict) or body.get("truncated") is not False:
                raise HTTPException(502, FAILURE)
            entry = next((row for row in _rows(body, "tree") if row.get("path") == name), None)
            if entry is None:
                raise HTTPException(404, FAILURE)
            last = index == len(parts) - 1
            # Contents API can dereference a symlink and return type=file. Trees
            # expose the actual mode, so neither symlinks nor submodules are read.
            if (entry.get("type"), entry.get("mode")) not in ({("blob", "100644"), ("blob", "100755")} if last else {("tree", "040000")}):
                raise HTTPException(422, "The action arguments are invalid")
            tree = _sha(entry.get("sha"))
        body, _ = await _get(client, "git/blobs/" + tree)
    try:
        if (not isinstance(body, dict) or body.get("encoding") != "base64"
                or type(body.get("size")) is not int or not 0 <= body["size"] <= MAX_FILE_BYTES
                or not isinstance(body.get("content"), str)):
            raise ValueError()
        raw = base64.b64decode("".join(body["content"].splitlines()), validate=True)
        if len(raw) != body["size"] or b"\0" in raw:
            raise ValueError()
        text = raw.decode("utf-8")
    except (ValueError, UnicodeError):
        raise HTTPException(422, "The action arguments are invalid") from None
    return {"name": path, "ref": "main", "sha": tree, "text": text}
