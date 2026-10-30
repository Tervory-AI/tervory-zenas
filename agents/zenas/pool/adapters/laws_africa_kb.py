"""Laws.Africa Knowledge Base retrieval for South African answers.

The founder's plan is Build (Knowledge Bases: legislation + judgments for one
country). Build does not include the full Legislation Content API: on that plan
``/v3/akn/za/`` answers 403 and only the Cape Town by-laws are open. See
https://developers.laws.africa/get-started/pricing and
https://developers.laws.africa/knowledge-bases/quick-start.

So South African law is fetched at question time from
``POST https://api.laws.africa/ai/v1/knowledge-bases/{code}/retrieve``.
The token is sent only as ``Authorization: Bearer``; it is never put in a URL,
logged, or returned. Redirects are refused so the header cannot leave the host.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from agents.zenas.pool.adapters.za_common import bearer_headers, laws_africa_token, reject_credential_url
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.region import RegionError

KB_API = "https://api.laws.africa/ai/v1/knowledge-bases"
LEGISLATION = ("legislation-za", "legislation-za-provincial", "legislation-za-municipal")
JUDGMENTS = ("judgments-za",)
CODES = frozenset(LEGISLATION + JUDGMENTS)
MAX_TOP_K = 100


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib hook
        return None


def _post(url: str, headers: dict[str, str], body: bytes) -> bytes:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
        return response.read()


class KnowledgeBaseClient:
    def __init__(self, *, region: str, post=None):
        if region != "za":
            raise RegionError("this box does not read za law")
        token = laws_africa_token()
        if not token:
            raise FetchNotConfigured("LAWS_AFRICA_API_TOKEN is not set")
        self._token = token
        self._headers = {**bearer_headers(token, "application/json"), "Content-Type": "application/json"}
        self._post = post or _post

    async def retrieve(self, code: str, text: str, *, top_k: int = 5,
                       filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if code not in CODES:
            raise ValueError("unknown Knowledge Base")
        if not isinstance(text, str) or not text.strip():
            return []
        url = f"{KB_API}/{code}/retrieve"
        reject_credential_url(url, self._token)
        body = json.dumps({"text": text.strip()[:2000], "top_k": max(1, min(int(top_k), MAX_TOP_K)),
                           "filters": filters or {}}).encode()
        try:
            raw = await asyncio.to_thread(self._post, url, self._headers, body)
        except urllib.error.HTTPError as exc:
            code_ = exc.code
            exc.close()
            raise FetchNotConfigured(f"HTTP {code_} from Laws.Africa Knowledge Base {code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise FetchNotConfigured("Laws.Africa Knowledge Base is unreachable") from None
        value = json.loads(raw)
        results = value.get("results") if isinstance(value, dict) else None
        return results if isinstance(results, list) else []


def passage(result: dict[str, Any], *, code: str) -> dict[str, Any] | None:
    """One retrieve result as an answer passage. Needs a public source URL."""
    if not isinstance(result, dict):
        return None
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    content = result.get("content") if isinstance(result.get("content"), dict) else {}
    text = str(content.get("text") or "").strip()
    url = str(meta.get("portion_public_url") or meta.get("public_url") or "")
    work = str(meta.get("work_frbr_uri") or "")
    title = str(meta.get("title") or "").strip()
    if not text or not url.startswith("https://") or not work.startswith("/akn/za") or not title:
        return None
    portion = str(meta.get("portion_title") or "").strip()
    version = str(meta.get("expression_frbr_uri") or meta.get("expression_date") or work)
    judgment = code in JUDGMENTS
    return {
        "doc_id": work,
        "version_id": version,
        "citation": title if not portion else f"{title}, {portion}",
        "heading": portion or title,
        "text": text,
        "path": str(meta.get("portion_id") or ""),
        "jurisdiction": "za",
        "kind": "case" if judgment else "statute",
        "level": "",
        "status": "effective",
        "url": url,
        "source_id": "laws-africa-kb:" + code,
        "attribution": "Laws.Africa",
    }


async def za_passages(query: str, *, client: KnowledgeBaseClient | None = None,
                      top_k: int = 4) -> list[dict[str, Any]]:
    """Current principal national legislation plus judgment summaries.

    A Knowledge Base outage or missing token yields no passages, so the answer
    path falls back to the stored pool or refuses with "could not verify".
    """
    try:
        client = client or KnowledgeBaseClient(region="za")
    except (FetchNotConfigured, RegionError):
        return []
    calls = (("legislation-za", {"principal": True, "repealed": False}), ("judgments-za", {}))
    found = await asyncio.gather(*(client.retrieve(code, query, top_k=top_k, filters=filters)
                                   for code, filters in calls), return_exceptions=True)
    passages: list[dict[str, Any]] = []
    for (code, _filters), results in zip(calls, found):
        if isinstance(results, BaseException):
            continue
        for result in results:
            item = passage(result, code=code)
            if item is not None:
                passages.append(item)
    return passages
