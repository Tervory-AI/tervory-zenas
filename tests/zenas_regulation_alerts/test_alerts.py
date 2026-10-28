"""Saved official source responses -> daily records -> actual loopback SMTP mailbox."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from agents.tiger.alert_email import configuration, MailConfigurationError
from agents.zenas.core.regulation_api import register_routes
from agents.zenas.core.regulation_sources import Sources, safe_url
from agents.zenas.core.regulation_store import MemoryWatches, PgWatches
from agents.zenas.core.regulations import add_watch, run_daily
from agents.zenas.core.service import _LEGAL_NOTICE
from agents.zenas.core.store import PgStore
from agents.zenas.pool.licence import FetchNotConfigured

ROOT = Path(__file__).resolve().parents[2]
SAVED = ROOT / "agents/zenas/pool/fixtures"
FR = json.loads((SAVED / "saved/federal-register/19-cfr-141.json").read_text())[
    "results"
]
NOW = datetime(2026, 10, 7, 2, tzinfo=timezone.utc)
TENANT = "19e2718c-6811-4d49-ad20-46fbe7e5f15a"
OTHER = "e0d99bed-d501-49cb-9df4-e8178bc173c5"


class RecordedHTTP:
    def __init__(self):
        self.rows = [FR[1]]
        self.failed = False
        self.calls = []

    async def __call__(self, url, headers):
        self.calls.append((url, headers))
        if self.failed:
            raise OSError("offline outage")
        return json.dumps(
            {
                "results": self.rows,
                "count": len(self.rows),
                "total_pages": 1,
                "next_page_url": None,
            }
        ).encode()


class TankStub:
    """No model call. Return a recorded source excerpt to exercise the Tank boundary."""

    def __init__(self):
        self.calls = []
        self.failed = False

    async def route(self, tenant, task, messages):
        self.calls.append((tenant, task, messages))
        if self.failed:
            raise OSError("offline Tank outage")
        current = json.loads(messages[0]["content"].split("\n", 1)[1])["current"]
        return {"content": current["text"]}


async def setup_watch(store=None, tenant=TENANT):
    store = store or MemoryWatches()
    watch = await add_watch(
        store,
        tenant_id=tenant,
        region="us-east",
        source="federal-register",
        topic="customs",
        options={},
        now=NOW,
    )
    http, tank = RecordedHTTP(), TankStub()
    return store, watch, http, tank


async def tick(store, http, tank, day, **kwargs):
    return await run_daily(
        store,
        Sources("us-east", http_get=http),
        tank,
        region="us-east",
        now=NOW + timedelta(days=day),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_saved_response_to_mailbox_once(catcher):
    store, watch, http, tank = await setup_watch()
    args = dict(transport=catcher.transport, recipient="watcher@example.test")
    # No prior run: the Oct 8 publication must alert on the first night.
    http.rows = [FR[0], FR[1]]
    result = await tick(store, http, tank, 1, **args)
    assert result["changes"] == result["summarized"] == result["sent"] == 1
    for day in (1, 2, 3):
        assert (await tick(store, http, tank, day, **args))["changes"] == 0
    rows = await store.changes(TENANT)
    assert len(rows) == len(catcher.messages) == len(tank.calls) == 1
    row = rows[0]
    message = catcher.messages[0]
    assert row["watch_id"] == watch["id"] and row["mail_state"] == "sent"
    assert tank.calls[0][:2] == (TENANT, "zenas.plain_language")
    assert row["summary"].endswith(_LEGAL_NOTICE)
    assert str(message["Subject"]) == "Law change: " + FR[0]["title"]
    assert message.get_content().splitlines() == [
        FR[0]["title"],
        FR[0]["abstract"],
        "Source: " + FR[0]["html_url"],
        "Date: " + FR[0]["publication_date"],
        _LEGAL_NOTICE,
    ]
    assert str(message["Message-ID"]) == f"<zenas.{TENANT}.{row['id']}@tervory.com>"
    assert catcher.tls_calls == 1
    if os.environ.get("ZENAS_ALERT_EVIDENCE_DIR"):
        path = Path(os.environ["ZENAS_ALERT_EVIDENCE_DIR"])
        path.mkdir(parents=True, exist_ok=True)
        (path / "local-mailbox.eml").write_bytes(message.as_bytes())
        (path / "local-mailbox.json").write_text(
            json.dumps(
                {
                    "fixture": "agents/zenas/pool/fixtures/saved/federal-register/19-cfr-141.json",
                    "source_title": FR[0]["title"],
                    "source_link": FR[0]["html_url"],
                    "watch_id": watch["id"],
                    "change_id": row["id"],
                    "tenant_id": TENANT,
                    "mail_state": row["mail_state"],
                    "messages_received": len(catcher.messages),
                    "tank_route": "zenas.plain_language",
                    "tank_stub": "source excerpt, no model inference",
                    "smtp": "loopback only; existing STARTTLS boundary stub; verifying TLS requested",
                    "subject": str(message["Subject"]),
                    "body": message.get_content(),
                },
                indent=2,
            )
            + "\n"
        )


@pytest.mark.asyncio
async def test_outage_and_partial_paging_never_advance_or_email(catcher):
    store, watch, http, tank = await setup_watch()
    await tick(store, http, tank, 0)
    baseline = dict(store.rows[TENANT, watch["id"]])
    http.failed = True
    result = await tick(
        store,
        http,
        tank,
        1,
        transport=catcher.transport,
        recipient="watcher@example.test",
    )
    assert result["unavailable"] == 1 and not await store.changes(TENANT)
    assert (
        store.rows[TENANT, watch["id"]] == baseline
        and not tank.calls
        and not catcher.messages
    )
    calls = []

    async def partial(url, headers):
        calls.append(url)
        if len(calls) > 1:
            raise OSError("offline second page outage")
        return json.dumps(
            {
                "results": [FR[0]],
                "next_page_url": "https://www.federalregister.gov/api/v1/documents.json?page=2",
                "total_pages": 2,
            }
        ).encode()

    result = await tick(store, partial, tank, 1)
    assert result["unavailable"] == 1 and store.rows[TENANT, watch["id"]] == baseline
    http.failed = False
    http.rows = [FR[0], FR[1]]
    assert (
        await tick(
            store,
            http,
            tank,
            2,
            transport=catcher.transport,
            recipient="watcher@example.test",
        )
    )["sent"] == 1


@pytest.mark.asyncio
async def test_failed_tank_retries_without_losing_or_redetecting(catcher):
    store, watch, http, tank = await setup_watch()
    await tick(store, http, tank, 0)
    http.rows = [FR[0], FR[1]]
    tank.failed = True
    await tick(store, http, tank, 1)
    assert len(await store.changes(TENANT)) == 1 and not catcher.messages
    tank.failed = False
    result = await tick(
        store,
        http,
        tank,
        1,
        transport=catcher.transport,
        recipient="watcher@example.test",
    )
    assert result["changes"] == 0 and result["sent"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,region,key,options",
    [
        ("congress", "us-east", "CONGRESS_GOV_API_KEY", {}),
        ("legiscan", "us-east", "LEGISCAN_API_KEY", {"state": "CA"}),
        ("laws-africa", "za", "LAWS_AFRICA_API_TOKEN", {}),
    ],
)
async def test_keyed_sources_off_without_keys(
    monkeypatch, source, region, key, options
):
    monkeypatch.delenv(key, raising=False)

    async def forbidden(*args):
        pytest.fail("unconfigured source touched a network or database")

    sources = Sources(region, http_get=forbidden, laws_snapshot=forbidden)
    with pytest.raises(FetchNotConfigured, match=key):
        await sources.fetch(dict(source=source, topic="contracts", options=options))


@pytest.mark.asyncio
async def test_za_source_never_leaves_home(monkeypatch):
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", "LAWS_AFRICA_API_TOKEN")

    async def forbidden(*args):
        pytest.fail("Boston fetched ZA law")

    with pytest.raises(ValueError):
        await Sources("us-east", http_get=forbidden, laws_snapshot=forbidden).fetch(
            dict(source="laws-africa", topic="contracts", options={})
        )


@pytest.mark.asyncio
async def test_federal_register_paging_and_changed_version():
    store, watch, http, tank = await setup_watch()
    calls = []

    async def pages(url, headers):
        calls.append(url)
        return json.dumps(
            {
                "results": [FR[len(calls) - 1]],
                "total_pages": 2,
                "next_page_url": "https://www.federalregister.gov/api/v1/documents.json?page=2"
                if len(calls) == 1
                else None,
            }
        ).encode()

    docs = await Sources("us-east", http_get=pages).fetch(watch)
    assert len(docs) == 2 and "conditions%5Bterm%5D=customs" in calls[0]
    store.rows[TENANT, watch["id"]]["created_at"] = NOW + timedelta(days=2)
    await store.record_snapshot(TENANT, watch["id"], docs, NOW)
    changed = dict(
        docs[0],
        version="offline-changed-version",
        date="2026-10-10",
        text=docs[0]["text"] + "\n" + FR[1]["abstract"],
    )
    assert (
        await store.record_snapshot(
            TENANT, watch["id"], [changed, docs[1]], NOW + timedelta(days=1)
        )
        == 1
    )
    change = (await store.changes(TENANT))[0]
    assert change["previous"] == docs[0] and change["document"] == changed
    assert (
        await store.record_snapshot(
            TENANT, watch["id"], [changed], NOW + timedelta(days=2)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_ecfr_uses_official_date_and_existing_xml_parser():
    seen = []

    async def get(url, headers):
        seen.append(url)
        if url.endswith("titles.json"):
            return json.dumps(
                {
                    "titles": [
                        {
                            "number": 19,
                            "latest_issue_date": "2026-10-07",
                            "up_to_date_as_of": "2026-10-07",
                        }
                    ]
                }
            ).encode()
        return (SAVED / "saved/ecfr/19-cfr-141.xml").read_bytes()

    watch = dict(source="ecfr", topic="customs", options={"title": "19", "part": "141"})
    rows = await Sources("us-east", http_get=get).fetch(watch)
    assert rows and all(
        r["date"] == "2026-10-07" and r["link"].startswith("https://www.ecfr.gov/")
        for r in rows
    )
    assert "/full/2026-10-07/title-19.xml?part=141" in seen[-1]

    async def missing(url, headers):
        return b'{"titles":[]}'

    with pytest.raises(KeyError):
        await Sources("us-east", http_get=missing).fetch(watch)


@pytest.mark.asyncio
async def test_congress_header_auth_paging_and_bill_status(monkeypatch):
    # Recorded field shape from the official Congress BillEndpoint documentation.
    monkeypatch.setenv("CONGRESS_GOV_API_KEY", "CONGRESS_GOV_API_KEY")
    calls = []

    async def get(url, headers):
        calls.append(url)
        assert "api_key=" not in url and headers["X-Api-Key"] == "CONGRESS_GOV_API_KEY"
        return json.dumps(
            {
                "bills": (
                    [
                        {
                            "congress": 117,
                            "type": "HR",
                            "number": "3076",
                            "title": "Postal Service Reform Act of 2022",
                            "updateDate": "2022-04-07",
                            "latestAction": {
                                "actionDate": "2022-04-06",
                                "text": "Became Public Law No: 117-108.",
                            },
                        }
                    ]
                    if len(calls) == 1
                    else []
                ),
                "pagination": {
                    "next": "https://api.congress.gov/v3/bill?offset=250"
                    if len(calls) == 1
                    else None
                },
            }
        ).encode()

    rows = await Sources("us-east", http_get=get).fetch(
        dict(
            source="congress",
            topic="Postal",
            options={},
            created_at=NOW,
            last_success=None,
        )
    )
    assert len(calls) == 2 and len(rows) == 1
    assert (
        rows[0]["kind"] == "bill"
        and rows[0]["link"]
        == "https://www.congress.gov/bill/117th-congress/house-bill/3076"
    )


@pytest.mark.asyncio
async def test_eurlex_public_sparql_no_key_and_official_celex():
    async def get(url, headers):
        assert url.startswith("https://publications.europa.eu/webapi/rdf/sparql?")
        assert "Authorization" not in headers and "X-Api-Key" not in headers
        assert headers["Accept"] == "application/sparql-results+json"
        assert "expression_belongs_to_work" in url
        return (SAVED / "saved/eur-lex/consumer-rights.json").read_bytes()

    rows = await Sources("us-east", http_get=get).fetch(
        dict(
            source="eur-lex",
            topic="consumer",
            options={},
            created_at=NOW,
            last_success=None,
        )
    )
    assert (
        rows[0]["link"]
        == "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32011L0083"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://api.laws.africa@evil.test/x",
        "https://127.0.0.1/x",
        "http://www.ecfr.gov/x",
        "https://www.ecfr.gov/x?key=KEY_NAME",
        "https://www.ecfr.gov:8443/x",
        "https://www.ecfr.gov/\nextra-line",
    ],
)
def test_untrusted_links_and_paging_never_escape_allowlist(url):
    with pytest.raises(ValueError):
        safe_url(url, {"www.ecfr.gov"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "topic",
    [
        "criminal charges",
        "bail application",
        "Criminal Procedure Act",
        "sentencing",
        "murder",
    ],
)
async def test_criminal_watch_refused(topic):
    with pytest.raises(ValueError):
        await add_watch(
            MemoryWatches(),
            tenant_id=TENANT,
            region="za",
            source="laws-africa",
            topic=topic,
            options={},
        )


@pytest.mark.asyncio
async def test_criminal_documents_suppressed_and_tenants_isolated(catcher):
    store, watch, http, tank = await setup_watch()
    await tick(store, http, tank, 0)
    http.rows = [dict(FR[0], title="Criminal charges and sentencing")]
    await tick(
        store,
        http,
        tank,
        1,
        transport=catcher.transport,
        recipient="watcher@example.test",
    )
    assert not await store.changes(TENANT) and not tank.calls
    assert (
        await store.watches(OTHER, "us-east") == [] and await store.changes(OTHER) == []
    )
    assert await store.set_enabled(OTHER, watch["id"], False) is None


@pytest.mark.asyncio
async def test_no_config_and_disabled_watch_keep_delivery_safe(monkeypatch, catcher):
    for name in ("HOST", "FROM", "ALERT_ADDRESSES"):
        monkeypatch.delenv("ZENAS_SMTP_" + name, raising=False)
    store, watch, http, tank = await setup_watch()
    await tick(store, http, tank, 0)
    http.rows = [FR[0]]
    await tick(store, http, tank, 1)
    assert (await store.changes(TENANT))[0]["mail_state"] == "pending"
    await store.set_enabled(TENANT, watch["id"], False)
    await tick(
        store,
        http,
        tank,
        1,
        transport=catcher.transport,
        recipient="watcher@example.test",
    )
    assert (
        not catcher.messages
        and (await store.changes(TENANT))[0]["mail_state"] == "suppressed"
    )


@pytest.mark.asyncio
async def test_smtp_rejection_retries_but_ambiguous_acceptance_does_not(catcher):
    store, watch, http, tank = await setup_watch()
    args = dict(transport=catcher.transport, recipient="watcher@example.test")
    await tick(store, http, tank, 0, **args)
    http.rows = [FR[0]]
    catcher.reject = True
    await tick(store, http, tank, 1, **args)
    assert (await store.changes(TENANT))[0]["mail_state"] == "pending"
    catcher.reject = False
    catcher.bad_quit = True
    await tick(store, http, tank, 2, **args)
    assert (await store.changes(TENANT))[0]["mail_state"] == "uncertain"
    await tick(store, http, tank, 3, **args)
    assert len(catcher.messages) == 1


def test_zenas_mail_settings_map_exact_tenant(monkeypatch):
    monkeypatch.setenv("ZENAS_SMTP_HOST", "localhost")
    monkeypatch.setenv("ZENAS_SMTP_FROM", "zenas@example.test")
    monkeypatch.setenv(
        "ZENAS_SMTP_ALERT_ADDRESSES", json.dumps({TENANT: "watcher@example.test"})
    )
    mail, recipient = configuration(TENANT, prefix="ZENAS")
    assert mail.host == "localhost" and recipient == "watcher@example.test"
    with pytest.raises(MailConfigurationError):
        configuration(OTHER, prefix="ZENAS")


@pytest.mark.asyncio
async def test_postgres_persists_atomic_changes_and_claims(tmp_path, catcher):
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path / "db")
    conn = await asyncpg.connect(server.get_uri())
    pool = await asyncpg.create_pool(server.get_uri(), min_size=1, max_size=2)
    try:
        core = PgStore(pool)
        await core.apply_schema()
        await core.register_tenant(TENANT, "us-east")
        store = PgWatches(conn)
        await store.apply_schema()
        await store.apply_schema()
        store, watch, http, tank = await setup_watch(store)
        await tick(store, http, tank, 0)
        http.rows = [FR[0], FR[1]]
        # Persist a pending record, close the connection, then use a fresh store.
        await tick(store, http, tank, 1)
        await conn.close()
        conn = await asyncpg.connect(server.get_uri())
        store = PgWatches(conn)
        assert len(await store.changes(TENANT)) == 1
        row = (await store.changes(TENANT))[0]
        await store.mark_event_sent(OTHER, row["id"])
        assert not (await store.changes(TENANT))[0]["event_sent"]
        await store.mark_event_sent(TENANT, row["id"])
        assert (await store.changes(TENANT))[0]["event_sent"]
        assert await store.changes(OTHER) == []
        assert await store.set_enabled(OTHER, watch["id"], False) is None
        catcher.reject = True
        args = dict(transport=catcher.transport, recipient="watcher@example.test")
        await tick(store, http, tank, 2, **args)
        assert (await store.changes(TENANT))[0]["mail_state"] == "pending"
        catcher.reject = False
        await tick(store, http, tank, 3, **args)
        await tick(store, http, tank, 4, **args)
        assert (
            len(catcher.messages) == 1
            and (await store.changes(TENANT))[0]["mail_state"] == "sent"
        )
        async with pool.acquire() as other:
            async with store.daily_lock() as first:
                async with PgWatches(other).daily_lock() as second:
                    assert first and not second
    finally:
        await conn.close()
        await pool.close()
        server.cleanup()


@pytest.mark.asyncio
async def test_api_real_routes_tenant_home_auth_and_persistence(tmp_path, monkeypatch):
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path / "api-db")
    pool = await asyncpg.create_pool(server.get_uri(), min_size=1, max_size=2)
    try:
        core = PgStore(pool)
        await core.apply_schema()
        await core.register_tenant(TENANT, "us-east")
        await core.register_tenant(OTHER, "us-east")

        @asynccontextmanager
        async def pool_store():
            async with pool.acquire() as conn:
                yield SimpleNamespace(_conn=conn)

        app = FastAPI()
        register_routes(app, pool_store, lambda: core, lambda: "us-east")
        monkeypatch.setenv("TANK_KEY", "TANK_KEY_ZENAS")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = dict(
                tenant_id=TENANT,
                home_region="us-east",
                source="federal-register",
                topic="customs",
            )
            assert (
                await client.post("/regulations/watches", json=body)
            ).status_code == 401
            headers = {"X-Tank-Key": "TANK_KEY_ZENAS"}
            response = await client.post(
                "/regulations/watches", json=body, headers=headers
            )
            assert response.status_code == 201, response.text
            watch = response.json()["watch"]
            assert "seen" not in watch
            response = await client.get(
                "/regulations/watches",
                params={"tenant_id": TENANT, "home_region": "us-east"},
                headers=headers,
            )
            assert response.json()["watches"][0]["id"] == watch["id"]
            assert (
                await client.get(
                    "/regulations/watches",
                    params={"tenant_id": OTHER, "home_region": "us-east"},
                    headers=headers,
                )
            ).json()["watches"] == []
            assert (
                await client.post(
                    "/regulations/watches/" + watch["id"],
                    json={
                        "tenant_id": OTHER,
                        "home_region": "us-east",
                        "enabled": False,
                    },
                    headers=headers,
                )
            ).status_code == 404
            assert (
                await client.post(
                    "/regulations/watches",
                    json={**body, "home_region": "za"},
                    headers=headers,
                )
            ).status_code == 403
    finally:
        await pool.close()
        server.cleanup()


@pytest.mark.asyncio
async def test_congress_untrusted_next_cannot_receive_key(monkeypatch):
    monkeypatch.setenv("CONGRESS_GOV_API_KEY", "CONGRESS_GOV_API_KEY")
    calls = []

    async def get(url, headers):
        calls.append(url)
        return b'{"bills":[],"pagination":{"next":"https://untrusted.example/bill"}}'

    with pytest.raises(ValueError):
        await Sources("us-east", http_get=get).fetch(
            dict(
                source="congress",
                topic="consumer",
                options={},
                created_at=NOW,
                last_success=None,
            )
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ecfr_removed_section_changes_part_but_new_date_alone_does_not():
    from xml.etree import ElementTree as ET

    xml = (SAVED / "saved/ecfr/19-cfr-141.xml").read_bytes()
    on = "2026-10-07"

    async def get(url, headers):
        if url.endswith("titles.json"):
            return json.dumps(
                {"titles": [{"number": 19, "latest_issue_date": on}]}
            ).encode()
        return xml

    sources = Sources("us-east", http_get=get)
    watch = dict(source="ecfr", topic="customs", options={"title": "19", "part": "141"})
    before = (await sources.fetch(watch))[0]
    on = "2026-10-08"
    assert (await sources.fetch(watch))[0]["version"] == before["version"]
    root = ET.fromstring(xml)
    parents = {child: parent for parent in root.iter() for child in parent}
    section = next(node for node in root.iter() if node.attrib.get("TYPE") == "SECTION")
    parents[section].remove(section)
    xml = ET.tostring(root)
    after = (await sources.fetch(watch))[0]
    assert after["version"] != before["version"] and "/on/2026-10-08/" in after["link"]


@pytest.mark.asyncio
async def test_civil_not_yet_refusal_keeps_existing_words():
    from agents.zenas.core.scope_rules import NOT_YET

    with pytest.raises(ValueError) as refused:
        await add_watch(
            MemoryWatches(),
            tenant_id=TENANT,
            region="us-east",
            source="federal-register",
            topic="family law",
            options={},
        )
    assert str(refused.value) == NOT_YET
