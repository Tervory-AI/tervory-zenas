"""A re-ingest closes rows an older parser collapsed onto a parent section number."""

import asyncio
from datetime import date

from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.store import MemoryStore
from agents.zenas.pool.textutil import split_parents
from tests.test_zenas_pool_bulk import _uslm

OLD = date(2026, 10, 19)
NEW = date(2026, 10, 31)
SOURCE = next(item for item in CATALOGUE if item.source_id == "govinfo-uscode")


def _open(store: MemoryStore) -> dict[str, str]:
    return {doc.citation: doc.title for doc in store.docs.values() if doc.effective_to is None}


def test_collapsed_parent_closes_and_real_parent_stays():
    asyncio.run(_collapsed())


async def _collapsed():
    store = MemoryStore(home_region="us-east")
    # The old parser read "§80b–21." as "80b": the row holds the child's text.
    old = _uslm([
        ("15", "80b", "Effective date", "This subchapter shall become effective on November 1, 1940."),
        ("42", "300gg", "Definitions", "The terms used in this part have these meanings."),
        ("42", "300gg-1", "Guaranteed issue", "Each issuer shall accept every employer."),
    ])
    await ingest_snapshot(store, SOURCE, [old], on=OLD)
    assert "15 U.S.C. § 80b" in _open(store)

    new = _uslm([
        ("15", "80b-20", "Short title", "This subchapter may be cited as the Investment Advisers Act of 1940."),
        ("15", "80b-21", "Effective date", "This subchapter shall become effective on November 1, 1940."),
        ("42", "300gg", "Definitions", "The terms used in this part have these meanings."),
        ("42", "300gg-1", "Guaranteed issue", "Each issuer shall accept every employer."),
    ])
    await ingest_snapshot(store, SOURCE, [new], on=NEW)
    still = _open(store)
    assert "15 U.S.C. § 80b" not in still
    assert {"15 U.S.C. § 80b-20", "15 U.S.C. § 80b-21", "42 U.S.C. § 300gg", "42 U.S.C. § 300gg-1"} <= set(still)
    closed = [d for d in store.docs.values() if d.citation == "15 U.S.C. § 80b"]
    assert closed and all(d.effective_to == NEW and d.status == "superseded" for d in closed)


def test_parent_with_its_own_text_is_not_closed_by_a_partial_payload():
    asyncio.run(_partial())


async def _partial():
    store = MemoryStore(home_region="us-east")
    await ingest_snapshot(store, SOURCE, [_uslm([
        ("42", "300gg", "Definitions", "The terms used in this part have these meanings."),
    ])], on=OLD)
    # A payload holding only a child does not close a parent with its own text.
    await ingest_snapshot(store, SOURCE, [_uslm([
        ("42", "300gg-1", "Guaranteed issue", "Each issuer shall accept every employer."),
    ])], on=NEW)
    assert {"42 U.S.C. § 300gg", "42 U.S.C. § 300gg-1"} <= set(_open(store))


def test_split_parents_lists_only_missing_parents():
    from agents.zenas.pool.adapters.govinfo import UsCodeAdapter

    docs = UsCodeAdapter().parse(_uslm([
        ("15", "80a-1", "Findings", "Upon the basis of facts disclosed."),
        ("15", "80a–2", "Definitions", "When used in this subchapter."),
        ("42", "300gg", "Definitions", "The terms used in this part."),
        ("42", "300gg-1", "Guaranteed issue", "Each issuer shall accept."),
        ("9", "2", "Validity", "A written arbitration agreement is valid."),
    ]), SOURCE)
    parents = split_parents(docs)
    assert set(parents) == {"15 U.S.C. § 80a"}
    hashes, titles = parents["15 U.S.C. § 80a"]
    assert len(hashes) == 2 and titles == {"Findings", "Definitions"}
