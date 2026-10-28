"""Capture the founder's real Boston route proof. Requires existing US credentials.

Creates one matter for the exact public question below. Never seeds law, creates
a tenant, changes the review switch, supplies an answer, or retries a write.
The output contains the public question/answer, source links and saved matter ID.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.zenas.core.citation_sources import public_source_url  # noqa: E402

QUESTION = "Is a forum selection clause in a B2B supply contract enforceable in Delaware?"
CRIMINAL_QUESTION = "How do I get bail for theft?"
REQUIRED = ("TANK_URL", "TANK_API_KEY", "ZENAS_PROOF_TENANT_ID", "ZENAS_PROOF_USER_ID")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def prove(output: Path) -> None:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    require(not missing, "Missing environment bindings: " + ", ".join(missing))
    base = os.environ["TANK_URL"].rstrip("/")
    address = urlsplit(base)
    require(address.scheme == "https" and bool(address.hostname) and not address.username and not address.password
            and not address.query and not address.fragment and address.hostname != "za.tervory.com", "Use the authorized Boston HTTPS Tank endpoint.")
    tenant = str(UUID(os.environ["ZENAS_PROOF_TENANT_ID"]))
    headers = {"x-tank-key": os.environ["TANK_API_KEY"]}
    query = {"tenant_id": tenant, "home_region": "us-east"}
    opened_body = {**query, "jurisdiction": "us", "title": QUESTION, "parties": [],
                   "created_by": os.environ["ZENAS_PROOF_USER_ID"], "user_id": os.environ["ZENAS_PROOF_USER_ID"]}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=120, follow_redirects=False) as client:
        before = await client.get("/v1/zenas/matters", params=query)
        require(before.status_code == 200, "US tenant matters are unavailable; no matter was created.")
        opened = await client.post("/v1/zenas/matters", json=opened_body)
        require(opened.status_code == 201, "The US matter was not opened. Do not retry an uncertain write.")
        matter_id = str(UUID(opened.json()["matter"]["id"]))
        # Retain the created ID even if answering fails, so an operator need not create it again.
        evidence = {"question": QUESTION, "matter_id": matter_id, "status": "answer_pending"}
        output.write_text(json.dumps(evidence, indent=2) + "\n")
        answer_body = {**query, "jurisdiction": "us", "question": QUESTION, "matter_id": matter_id}
        answered = await client.post("/v1/zenas/answers", json=answer_body)
        require(answered.status_code == 200, "The answer route failed; the created matter ID is retained in the output file.")
        answer = answered.json()
        require(answer.get("ok") is True and answer.get("state") == "released" and answer.get("delivered") is True,
                "No released, verified answer was returned. Do not bypass review or invent one.")
        memo = answer.get("memo") or {}
        sources = memo.get("sources") or []
        require(bool(memo.get("text")) and bool(sources), "A cited answer is required.")
        require({row["citation"] for row in sources} == set(memo.get("citations") or []), "Every citation needs its stored source link.")
        links = []
        # No authentication is forwarded to public sources.
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as public:
            for source in sources:
                url = public_source_url(source.get("url"))
                require(bool(url) and bool(source.get("quote")), "A source has no public link or checked quote.")
                current = url
                for _ in range(5):
                    async with public.stream("GET", current) as response:
                        if response.is_redirect:
                            next_url = public_source_url(str(response.url.join(response.headers.get("location", ""))))
                            require(bool(next_url), "A source redirect is not a public source link.")
                            current = next_url
                            continue
                        require(response.status_code == 200, "A cited source link did not open successfully.")
                        links.append({"citation": source["citation"], "url": url, "final_url": current, "http_status": response.status_code})
                        break
                else:
                    raise RuntimeError("A cited source redirected too many times.")
        refused = await client.post("/v1/zenas/matters", json={**opened_body, "title": CRIMINAL_QUESTION})
        require(refused.status_code == 422 and "criminal" in str(refused.json().get("detail", "")).lower(), "Criminal question was not refused.")
    # A fresh client reads the persisted result through the same existing route.
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30, follow_redirects=False) as client:
        loaded = await client.get("/v1/zenas/matters", params=query)
        require(loaded.status_code == 200, "The saved matter could not be reloaded.")
        matters = loaded.json().get("matters") or []
        saved = next((row for row in matters if row.get("id") == matter_id), None)
        require(saved is not None and saved.get("title") == QUESTION, "The reloaded matter differs from the opened matter.")
        document = next((row for row in saved.get("documents", []) if row.get("id") == answer.get("document_id")), None)
        require(document is not None and document.get("body") == memo["text"] and document.get("sources") == sources,
                "The reloaded answer or its citations differ from the saved response.")
        require(not any(row.get("title") == CRIMINAL_QUESTION for row in matters), "A criminal matter was saved.")
    evidence.update(status="route_checks_passed", request={"method": "POST", "path": "/v1/zenas/answers",
                    "body": {**answer_body, "tenant_id": "$ZENAS_PROOF_TENANT_ID"}},
                    answer=memo["text"], sources=sources, citation_links=links, reloaded=True, criminal_refused=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(prove(args.output))
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        # Never echo remote bodies, credentials, or exception URLs.
        raise SystemExit(f"Route proof stopped: {type(error).__name__}") from None
