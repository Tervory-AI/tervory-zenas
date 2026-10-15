"""Case adapters keep federal appellate commercial matters and drop the rest.

The paragraphs below are fixture markers, not opinions.
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters.cases import CapAdapter, CourtListenerAdapter
from agents.zenas.pool.adapters.ecfr import EcfrAdapter, ecfr_url
from agents.zenas.pool.adapters.states import NewYorkAdapter, TexasAdapter
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.store import MemoryStore

_CASES = {
    "results": [
        {
            "court": "ca2",
            "case_name": "Fixture Carrier v. Fixture Shipper",
            "date_filed": "2010-05-01",
            "nature_of_suit": "120 Marine Contract",
            "citations": [{"volume": 600, "reporter": "F.3d", "page": "10"}],
            "plain_text": "FIXTURE COMMERCIAL PARAGRAPH",
            "absolute_url": "/opinion/1/fixture/",
        },
        {
            "court": "ca2",
            "case_name": "Fixture People",
            "date_filed": "2011-01-01",
            "nature_of_suit": "Criminal",
            "citations": [{"volume": 1, "reporter": "F.3d", "page": "1"}],
            "plain_text": "OMITTED",
        },
        {
            "court": "nysd",
            "case_name": "Fixture District",
            "date_filed": "2012-01-01",
            "nature_of_suit": "Contract",
            "citations": [{"cite": "1 F. Supp. 1"}],
            "plain_text": "OMITTED-DISTRICT",
        },
    ]
}


def test_courtlistener_filters_and_does_not_store_dropped_text():
    asyncio.run(_cases())


async def _cases():
    source = next(item for item in CATALOGUE if item.source_id == "courtlistener")
    adapter = CourtListenerAdapter()
    documents = adapter.parse(json.dumps(_CASES).encode(), source)
    assert len(documents) == 1
    assert documents[0].citation == "600 F.3d 10"
    assert documents[0].kind == "case"
    assert "OMITTED" not in documents[0].text
    assert any(item.startswith("criminal:") for item in adapter.skipped)
    assert any(item.startswith("not-appellate:") for item in adapter.skipped)

    store = MemoryStore(home_region="us-east")
    blob = json.dumps(_CASES).encode()
    await ingest_snapshot(store, source, [blob], on=date(2026, 10, 13))
    stored = await store.all_text()
    assert "FIXTURE COMMERCIAL PARAGRAPH" in stored
    assert "OMITTED" not in stored
    assert "OMITTED-DISTRICT" not in stored


def test_cap_without_commercial_marker_is_not_stored():
    source = next(item for item in CATALOGUE if item.source_id == "cap")
    payload = {
        "name": "Fixture v. Fixture",
        "decision_date": "1999-01-01",
        "court": {"slug": "ca2"},
        "citations": [{"cite": "1 F.2d 1"}],
        "casebody": {"data": {"opinions": [{"text": "FIXTURE OPINION"}]}},
    }
    documents = CapAdapter().parse(json.dumps(payload).encode(), source)
    assert documents == []


def test_state_parsers_skip_empty_text():
    ny = next(item for item in CATALOGUE if item.source_id == "ny-laws")
    tx = next(item for item in CATALOGUE if item.source_id == "texas-statutes")
    empty = json.dumps({"result": {"locationId": "2-201", "title": "Fixture", "text": ""}}).encode()
    assert NewYorkAdapter().parse(empty, ny) == []
    filled = json.dumps(
        {"result": {"locationId": "2-201", "title": "Fixture title", "text": "FIXTURE ENACTED PARAGRAPH"}}
    ).encode()
    parsed = NewYorkAdapter().parse(filled, ny)
    assert parsed[0].citation == "N.Y. U.C.C. Law § 2-201"
    assert parsed[0].level == "state"
    texas = json.dumps(
        {"citation": "Tex. Bus. Orgs. Code § 1.002", "title": "Fixture", "text": "FIXTURE TEXAS PARAGRAPH"}
    ).encode()
    assert TexasAdapter().parse(texas, tx)[0].citation == "Tex. Bus. Orgs. Code § 1.002"


def test_ecfr_wave_urls_stay_on_the_official_host():
    asyncio.run(_urls())


async def _urls():
    seen = []

    async def get(url, headers):
        seen.append(url)
        assert headers["User-Agent"].startswith("TervoryZenasPool")
        return b"<html></html>"

    await EcfrAdapter().fetch(get)
    assert len(seen) == 4
    assert ecfr_url("2024-07-01", "19", "141", "141.1") in seen
    assert ecfr_url("2024-07-01", "15", "730", "730.1") in seen
    assert ecfr_url("2024-07-01", "22", "120", "120.1") in seen
    assert ecfr_url("2024-07-01", "31", "501", "501.101") in seen
    assert all(url.startswith("https://www.ecfr.gov/") for url in seen)
