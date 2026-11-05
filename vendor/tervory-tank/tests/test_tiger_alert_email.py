"""Saved pages -> confirmed alerts -> local SMTP catcher; never external mail."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import json
import os
from pathlib import Path
import smtplib
import socket
import ssl
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from aiosmtpd.controller import Controller

from test_tiger import ROOT, TENANT_A, TENANT_B, NOW, _load, _watch
from tiger_works.fixture import LABEL, URL, extract, no_paid_call, saved_page
from alert_email import (
    MailConfigurationError,
    configuration,
    deliver_pending,
    letter_for,
)
from agents.rig.rigbox.reserve import SmtpMailer
from watch import DomainLimiter, MemoryStore, PgStore, ensure_tables, run_check


class Catcher:
    def __init__(self):
        self.messages = []
        self.reject = False
        self.bad_quit = False
        self.tls_calls = 0

    async def handle_DATA(self, server, session, envelope):
        if self.reject:
            return "451 Offline fixture rejects this attempt"
        self.messages.append(
            BytesParser(policy=policy.default).parsebytes(envelope.original_content)
        )
        return "250 Stored by offline fixture"

    async def handle_QUIT(self, server, session, envelope):
        return "451 Offline fixture lost final response" if self.bad_quit else "221 Bye"


@pytest.fixture
def catcher(monkeypatch):
    handler = Catcher()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = Controller(handler, hostname="127.0.0.1", port=port)
    server.start()

    # No certificate/private-key creation. Only STARTTLS is stubbed on the local
    # catcher; the unchanged Rig mailer still creates a verifying SSL context.
    def local_starttls(client, *, context):
        assert client._host == "127.0.0.1"
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        handler.tls_calls += 1
        return 220, b"Local fixture TLS boundary"

    monkeypatch.setattr(smtplib.SMTP, "starttls", local_starttls)
    handler.transport = SmtpMailer("127.0.0.1", port, "", "", "tiger@example.test")
    try:
        yield handler
    finally:
        server.stop()


async def checked(store, watch, price="120", stock="InStock", at=NOW, source="page"):
    async def render(*args):
        if source == "portal":
            return {
                "ok": True,
                "portal_item": {
                    "name": LABEL,
                    "price": price,
                    "currency": "USD",
                    "quantity": 1 if stock == "InStock" else 0,
                },
            }
        return {"ok": True, "html": saved_page(price, stock)}

    async def feed(*args):
        return {
            "found": source == "feed",
            "receipt": {"source": "offline fixture"},
            "item": {
                "name": LABEL,
                "price": price,
                "currency": "USD",
                "quantity": 1 if stock == "InStock" else 0,
            },
        }

    return await run_check(
        store,
        watch["tenant_id"],
        watch,
        "tiger",
        render,
        extract,
        no_paid_call,
        no_paid_call,
        DomainLimiter(0),
        now=lambda: at,
        feed=feed,
    )


async def pending(store=None, tenant=TENANT_A, **preferences):
    store = store or MemoryStore()
    watch = await store.create_watch({**_watch(tenant), "url": URL, **preferences})
    await checked(store, watch)
    await checked(store, watch, "115", at=NOW + timedelta(minutes=1))
    return store, watch


@pytest.mark.asyncio
async def test_five_saved_pages_send_only_changed_page_once(catcher):
    store = MemoryStore()
    watches = [
        await store.create_watch({**_watch(), "url": URL + f"?fixture={n}"})
        for n in range(5)
    ]
    for watch in watches:
        await checked(store, watch)
    assert store.alerts == [] and store.mail == {}
    for n, watch in enumerate(watches):
        await checked(
            store, watch, "115" if n == 2 else "120", at=NOW + timedelta(minutes=1)
        )
    assert len(store.alerts) == len(store.mail) == 1
    for _ in range(3):
        await deliver_pending(
            store,
            TENANT_A,
            transport=catcher.transport,
            recipient="a@example.test",
            clock=lambda: NOW + timedelta(minutes=2),
        )
    assert len(catcher.messages) == catcher.tls_calls == 1
    message = catcher.messages[0]
    assert str(message["To"]) == "a@example.test"
    assert str(message["Subject"]) == "Price went down from $120.00 to $115.00"
    assert message.get_content().splitlines() == [
        LABEL,
        str(message["Subject"]),
        watches[2]["url"],
    ]
    assert (
        str(message["Message-ID"])
        == f"<tiger.{TENANT_A}.{store.alerts[0]['id']}@tervory.com>"
    )
    for n, watch in enumerate(watches):
        await checked(
            store, watch, "115" if n == 2 else "120", at=NOW + timedelta(minutes=3)
        )
    await deliver_pending(
        store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
    )
    assert len(catcher.messages) == 1

    if os.environ.get("TIGER_MAIL_EVIDENCE_DIR"):
        output = Path(os.environ["TIGER_MAIL_EVIDENCE_DIR"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "local-fixture-alert.eml").write_bytes(catcher.messages[0].as_bytes())
        (output / "local-fixture-result.json").write_text(
            json.dumps(
                {
                    "proof": "local, fixture pages and SMTP catcher",
                    "watches": len(watches),
                    "confirmed_alerts": len(store.alerts),
                    "accepted_messages": len(catcher.messages),
                    "alert": store.alerts[0],
                    "subject": str(message["Subject"]),
                    "body": message.get_content().splitlines(),
                },
                indent=2,
            )
            + "\n"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["page", "feed", "portal"])
@pytest.mark.parametrize(
    "price_on,stock_on", [(True, True), (False, True), (True, False), (False, False)]
)
async def test_preferences_gate_page_feed_and_portal_email(
    catcher, source, price_on, stock_on
):
    store = MemoryStore()
    watch = await store.create_watch(
        {**_watch(), "url": URL, "alert_price": price_on, "alert_stock": stock_on}
    )
    await checked(store, watch, source=source)
    await checked(store, watch, "115", "OutOfStock", NOW + timedelta(minutes=1), source)
    await deliver_pending(
        store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
    )
    assert len(catcher.messages) == int(price_on) + int(stock_on)
    assert {str(m["Subject"]) for m in catcher.messages} == (
        ({"Price went down from $120.00 to $115.00"} if price_on else set())
        | ({"Stock changed from In stock to Out of stock."} if stock_on else set())
    )


@pytest.mark.asyncio
async def test_rejected_send_retries_after_backoff_without_duplicate(catcher):
    store, _ = await pending()
    catcher.reject = True
    tick = NOW + timedelta(minutes=2)
    first = await deliver_pending(
        store,
        TENANT_A,
        transport=catcher.transport,
        recipient="a@example.test",
        clock=lambda: tick,
    )
    assert first["pending"] == 1 and catcher.messages == []
    catcher.reject = False
    assert (
        await deliver_pending(
            store,
            TENANT_A,
            transport=catcher.transport,
            recipient="a@example.test",
            clock=lambda: tick,
        )
    )["sent"] == 0
    assert (
        await deliver_pending(
            store,
            TENANT_A,
            transport=catcher.transport,
            recipient="a@example.test",
            clock=lambda: tick + timedelta(seconds=60),
        )
    )["sent"] == 1
    await deliver_pending(
        store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
    )
    assert len(catcher.messages) == 1
    assert next(iter(store.mail.values()))["attempts"] == 2


@pytest.mark.asyncio
async def test_concurrent_workers_and_two_tenants_are_isolated(catcher):
    store, _ = await pending()
    await pending(store, TENANT_B)
    a_id = next(a["id"] for a in store.alerts if a["tenant_id"] == TENANT_A)
    assert await store.claim_mail(TENANT_B, a_id, NOW + timedelta(days=1)) is None
    await asyncio.gather(
        *[
            deliver_pending(
                store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
            )
            for _ in range(4)
        ]
    )
    assert (
        len(catcher.messages) == 1
        and str(catcher.messages[0]["To"]) == "a@example.test"
    )
    assert len(await store.pending_mail(TENANT_B, NOW + timedelta(days=1))) == 1
    await deliver_pending(
        store, TENANT_B, transport=catcher.transport, recipient="b@example.test"
    )
    assert [str(m["To"]) for m in catcher.messages] == [
        "a@example.test",
        "b@example.test",
    ]
    assert len({str(m["Message-ID"]) for m in catcher.messages}) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["preference", "pause", "delete"])
async def test_latest_preferences_suppress_unsent_mail(catcher, change):
    store, watch = await pending()
    if change == "delete":
        await store.delete_watch(TENANT_A, watch["id"])
    else:
        await store.update_watch(
            TENANT_A,
            watch["id"],
            {"alert_price": False} if change == "preference" else {"enabled": False},
        )
    result = await deliver_pending(
        store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
    )
    assert result["suppressed"] == 1 and catcher.messages == []


@pytest.mark.asyncio
async def test_post_acceptance_error_is_never_retried(catcher):
    store, _ = await pending()
    catcher.bad_quit = True
    assert (
        await deliver_pending(
            store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
        )
    )["uncertain"] == 1
    assert len(catcher.messages) == 1
    catcher.bad_quit = False
    await deliver_pending(
        store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
    )
    assert len(catcher.messages) == 1


@pytest.mark.asyncio
async def test_failed_receipt_after_acceptance_cannot_resend(catcher, monkeypatch):
    store, _ = await pending()

    async def disconnected(*args):
        raise ConnectionError("offline fixture database unavailable")

    monkeypatch.setattr(store, "finish_mail", disconnected)
    with pytest.raises(ConnectionError):
        await deliver_pending(
            store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
        )
    await deliver_pending(
        store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
    )
    assert len(catcher.messages) == 1
    assert next(iter(store.mail.values()))["status"] == "sending"


def test_tenant_address_is_explicit_and_tiger_settings_are_separate(monkeypatch):
    monkeypatch.setenv("TIGER_SMTP_HOST", "localhost")
    monkeypatch.setenv("TIGER_SMTP_FROM", "tiger@example.test")
    monkeypatch.setenv(
        "TIGER_SMTP_ALERT_ADDRESSES",
        json.dumps({TENANT_A: "a@example.test", TENANT_B: "b@example.test"}),
    )
    monkeypatch.setenv("SMTP_HOST", "unused.example.test")
    monkeypatch.setenv("SMTP_FROM", "unused@example.test")
    assert configuration(TENANT_A)[1] == "a@example.test"
    assert configuration(TENANT_B)[1] == "b@example.test"
    assert configuration(TENANT_A)[0].host == "localhost"
    with pytest.raises(MailConfigurationError):
        configuration(str(uuid4()))
    monkeypatch.setenv(
        "TIGER_SMTP_ALERT_ADDRESSES",
        json.dumps({TENANT_A: "a@example.test\r\nBcc:b@example.test"}),
    )
    with pytest.raises(MailConfigurationError):
        configuration(TENANT_A)


@pytest.mark.asyncio
async def test_missing_configuration_keeps_mail_pending(monkeypatch):
    store, _ = await pending()
    monkeypatch.delenv("TIGER_SMTP_ALERT_ADDRESSES", raising=False)
    assert (await deliver_pending(store, TENANT_A))["mail"] == "not_configured"
    assert next(iter(store.mail.values()))["status"] == "pending"


def test_email_copy_matches_actual_workspace_formatter():
    examples = []
    for kind, before, after, currency in [
        ("price", "120", "115", "USD"),
        ("price", "9", "12", "ZAR"),
        ("stock", "in_stock", "out_of_stock", "USD"),
        ("stock", "PreOrder", "InStock", "USD"),
    ]:
        alert = {
            "id": str(uuid4()),
            "kind": kind,
            "before": before,
            "after": after,
            "url": URL,
            "ts": NOW.isoformat(),
            "product_name": LABEL,
            "currency": currency,
        }
        observation = {
            "id": str(uuid4()),
            "url": URL,
            "ts": NOW.isoformat(),
            "currency": currency,
            "product_name": LABEL,
            "price": "120",
            "method": "jsonld",
            "availability": "in_stock",
        }
        examples.append({"alert": alert, "observation": observation})
    result = subprocess.run(
        ["node", "tests/tiger_alert_email/page_copy.cjs"],
        cwd=ROOT,
        input=json.dumps(examples),
        text=True,
        capture_output=True,
        check=True,
    )
    for example, row in zip(examples, json.loads(result.stdout)):
        letter = letter_for(example["alert"])
        assert not row["reason"]
        assert letter == {
            "subject": row["change"],
            "text": "\n".join((row["product"], row["change"], row["url"])),
        }


@pytest.mark.asyncio
async def test_dispatch_job_runs_delivery(catcher, monkeypatch):
    module = _load("tiger_alert_email_app", ROOT / "agents/tiger/app.py")
    store, _ = await pending()
    monkeypatch.setattr(module, "PgStore", lambda *args: store)
    monkeypatch.setattr(module.app.state, "pool", None, raising=False)
    monkeypatch.setattr(module.app.state, "http", None, raising=False)
    reconciled = []

    class RigDelivery:
        def __init__(self, watches):
            assert watches is store

        async def deliver(self, tenant, client):
            reconciled.append(tenant)

    monkeypatch.setattr(module, "RigWatches", RigDelivery)

    async def deliver(s, tenant):
        return await deliver_pending(
            s, tenant, transport=catcher.transport, recipient="a@example.test"
        )

    monkeypatch.setattr(module, "deliver_pending", deliver)
    result = await module.dispatch(SimpleNamespace(tenant_id=TENANT_A))
    assert result["email"]["sent"] == 1 and len(catcher.messages) == 1
    assert reconciled == [TENANT_A]


@pytest.mark.asyncio
async def test_postgres_atomic_receipt_and_restart(catcher):
    url = os.environ.get("TIGER_MAIL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Disposable local Postgres fixture only")
    from urllib.parse import urlsplit

    assert urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}, (
        "Use only a disposable loopback database"
    )
    schema = "tiger_mail_test_" + uuid4().hex[:12]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            await conn.execute(f"CREATE SCHEMA {schema}")
            # tiger/schema.sql always writes agent_tiger.memory_* (the room schema exists in production).
            await conn.execute("CREATE SCHEMA IF NOT EXISTS agent_tiger")
            await ensure_tables(conn, schema)
        store = PgStore(pool, schema)
        # No jobs/scheduler schema is needed for this focused store integration.
        async with pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {schema}.watchlist
                (tenant_id, url, every) VALUES ($1, $2, '1h'), ($3, $2, '1h')""",
                TENANT_A,
                URL,
                TENANT_B,
            )
        row = {
            "tenant_id": TENANT_A,
            "url": URL,
            "ts": NOW,
            "kind": "price",
            "before": "120",
            "after": "115",
            "product_name": LABEL,
            "currency": "USD",
        }
        alert = await store.add_alert(row)
        await store.add_alert({**row, "tenant_id": TENANT_B})
        tick = datetime.now(timezone.utc) + timedelta(seconds=1)
        catcher.reject = True
        result = await deliver_pending(
            store,
            TENANT_A,
            transport=catcher.transport,
            recipient="a@example.test",
            clock=lambda: tick,
        )
        assert result["pending"] == 1 and catcher.messages == []
        assert await store.pending_mail(TENANT_A, tick) == []
        assert await store.pending_mail(TENANT_A, tick + timedelta(seconds=60)) == [
            alert["id"]
        ]
        catcher.reject = False
        await asyncio.gather(
            *[
                deliver_pending(
                    PgStore(pool, schema),
                    TENANT_A,
                    transport=catcher.transport,
                    recipient="a@example.test",
                    clock=lambda: tick + timedelta(seconds=60),
                )
                for _ in range(3)
            ]
        )
        assert len(catcher.messages) == 1
        assert (
            await store.claim_mail(TENANT_B, alert["id"], NOW + timedelta(days=1))
            is None
        )
        # Fresh connections/store after process-style restart still see sent.
        await pool.close()
        pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
        store = PgStore(pool, schema)
        await deliver_pending(
            store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
        )
        assert len(catcher.messages) == 1
        await deliver_pending(
            store, TENANT_B, transport=catcher.transport, recipient="b@example.test"
        )
        assert len(catcher.messages) == 2
        async with pool.acquire() as conn:
            assert (
                await conn.fetchval(
                    f"SELECT count(*) FROM {schema}.alert_mail WHERE status='sent'"
                )
                == 2
            )
            # A queue failure rolls the alert back with it; no unqueued alert.
            await conn.execute(f"""CREATE FUNCTION {schema}.reject_mail() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'offline fixture rejection'; END;
                $$ LANGUAGE plpgsql""")
            await conn.execute(f"""CREATE TRIGGER reject_mail BEFORE INSERT ON {schema}.alert_mail
                FOR EACH ROW EXECUTE FUNCTION {schema}.reject_mail()""")
        with pytest.raises(asyncpg.RaiseError):
            await store.add_alert(row)
        async with pool.acquire() as conn:
            assert await conn.fetchval(f"SELECT count(*) FROM {schema}.alerts") == 2
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP SCHEMA {schema} CASCADE")
        await pool.close()


@pytest.mark.asyncio
async def test_configured_recipients_deliver_to_their_own_tenants(catcher, monkeypatch):
    store, _ = await pending()
    await pending(store, TENANT_B)
    monkeypatch.setenv("TIGER_SMTP_HOST", catcher.transport.host)
    monkeypatch.setenv("TIGER_SMTP_PORT", str(catcher.transport.port))
    monkeypatch.setenv("TIGER_SMTP_FROM", catcher.transport.sender)
    monkeypatch.delenv("TIGER_SMTP_USER", raising=False)
    monkeypatch.delenv("TIGER_SMTP_PASSWORD", raising=False)
    monkeypatch.setenv(
        "TIGER_SMTP_ALERT_ADDRESSES",
        json.dumps({TENANT_A: "a@example.test", TENANT_B: "b@example.test"}),
    )
    await deliver_pending(store, TENANT_A)
    await deliver_pending(store, TENANT_B)
    assert [str(m["To"]) for m in catcher.messages] == [
        "a@example.test",
        "b@example.test",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["blocked", "timeout", "missing_name", "missing_currency"]
)
async def test_failed_checks_never_queue_or_send_mail(catcher, failure):
    store = MemoryStore()
    watch = await store.create_watch({**_watch(), "url": URL})
    await checked(store, watch)

    async def render(*args):
        if failure == "blocked":
            return {"ok": False, "challenge": True}
        if failure == "timeout":
            return {"ok": False, "error": "offline fixture timeout"}
        name = "no-name.html" if failure == "missing_name" else "no-currency.html"
        return {
            "ok": True,
            "html": (ROOT / "tests/tiger_missing_name/fixtures" / name).read_text(),
        }

    await run_check(
        store,
        TENANT_A,
        watch,
        "tiger",
        render,
        extract,
        no_paid_call,
        no_paid_call,
        DomainLimiter(0),
        now=lambda: NOW + timedelta(minutes=1),
    )
    await deliver_pending(
        store, TENANT_A, transport=catcher.transport, recipient="a@example.test"
    )
    assert store.alerts == [] and store.mail == {} and catcher.messages == []
