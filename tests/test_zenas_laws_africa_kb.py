import asyncio
import json

import pytest

from agents.zenas.pool.adapters import laws_africa_kb as kb
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.region import RegionError

TOKEN = "t" * 40


def _result(**meta):
    base = {"title": "Rental Housing Act, 1999", "work_frbr_uri": "/akn/za/act/1999/50",
            "public_url": "https://lawlibrary.org.za/akn/za/act/1999/50", "portion_title": "Section 4"}
    base.update(meta)
    return {"content": {"text": "A landlord may not lock out a tenant."}, "metadata": base, "score": 0.2}


def test_refuses_other_region_and_missing_token(monkeypatch):
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", TOKEN)
    with pytest.raises(RegionError):
        kb.KnowledgeBaseClient(region="us-east")
    monkeypatch.delenv("LAWS_AFRICA_API_TOKEN")
    with pytest.raises(FetchNotConfigured):
        kb.KnowledgeBaseClient(region="za")
    assert asyncio.run(kb.za_passages("eviction")) == []


def test_retrieve_posts_bearer_never_in_url(monkeypatch):
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", TOKEN)
    calls = []

    def post(url, headers, body):
        calls.append((url, headers, json.loads(body)))
        return json.dumps({"results": [_result()]}).encode()

    client = kb.KnowledgeBaseClient(region="za", post=post)
    rows = asyncio.run(kb.za_passages("eviction lockout", client=client))
    urls = [c[0] for c in calls]
    assert sorted(urls) == [kb.KB_API + "/judgments-za/retrieve", kb.KB_API + "/legislation-za/retrieve"]
    assert all(TOKEN not in u for u in urls)
    assert all(c[1]["Authorization"] == "Bearer " + TOKEN for c in calls)
    leg = next(c[2] for c in calls if "legislation" in c[0])
    assert leg["filters"] == {"principal": True, "repealed": False}
    assert rows[0]["doc_id"] == "/akn/za/act/1999/50"
    assert rows[0]["url"].startswith("https://lawlibrary.org.za/")
    assert rows[0]["citation"] == "Rental Housing Act, 1999, Section 4"


def test_unknown_code_and_unlinked_results_dropped(monkeypatch):
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", TOKEN)
    client = kb.KnowledgeBaseClient(region="za", post=lambda *a: b"{}")
    with pytest.raises(ValueError):
        asyncio.run(client.retrieve("legislation-gh", "x"))
    assert kb.passage(_result(public_url="", portion_public_url=""), code="legislation-za") is None
    assert kb.passage(_result(work_frbr_uri="/akn/gh/act/1"), code="legislation-za") is None


def test_outage_yields_no_passages(monkeypatch):
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", TOKEN)

    def post(url, headers, body):
        raise OSError("down")

    client = kb.KnowledgeBaseClient(region="za", post=post)
    assert asyncio.run(kb.za_passages("eviction", client=client)) == []
