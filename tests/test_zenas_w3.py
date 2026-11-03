"""Offline contracts for direct drafting, tenant guides and ZA watch snapshots."""

from datetime import datetime, timezone
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

import command
from agents.zenas.core.regulation_sources import GOVZA_SOURCE_IDS, laws_africa_snapshot
from agents.zenas.core.scope_rules import REFUSED
from agents.zenas.core.service import add_playbook, draft_contract, list_playbooks
from agents.zenas.core.store import MemoryStore
from zenas_commands import draft_payload


def test_direct_draft_catalogue_and_payload_are_code_only(monkeypatch):
    actor = command.Actor(uuid4(), "placeholder-user", False, squads=frozenset({"zenas"}))
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(box_region=lambda: "us-east"))
    args = {"template": "letter", "jurisdiction": "us", "terms": {"subject": "placeholder"},
            "text": "placeholder civil letter"}
    method, path, query, body = command.adapter_request(
        command.PlannedAction(tool="zenas.draft", arguments=args), actor)
    assert (method, path, query) == ("POST", "/v1/zenas/drafts", {"tenant_id": str(actor.tenant_id),
                                                                  "home_region": "us-east"})
    assert body == draft_payload(tenant_id=str(actor.tenant_id), home_region="us-east",
                                 template="letter", jurisdiction="us", terms=args["terms"],
                                 text=args["text"], matter_id=None)
    with pytest.raises(HTTPException) as error:
        command.adapter_request(command.PlannedAction(tool="zenas.draft", arguments={**args,
                                "template": "placeholder-unknown"}), actor)
    assert error.value.status_code == 422
    assert "zenas.draft" in command.TOOLS["zenas"]


@pytest.mark.asyncio
async def test_criminal_direct_draft_refuses_before_drafting():
    store = MemoryStore()
    tenant = str(uuid4())
    await store.register_tenant(tenant, "us-east")
    result = await draft_contract(store, None, None, tenant_id=tenant, home_region="us-east",
                                  box_region="us-east", template="letter", jurisdiction="us",
                                  narrative="placeholder criminal sentencing", terms={})
    assert result["ok"] is False and result["reason"] == REFUSED
    assert store.documents == []


@pytest.mark.asyncio
async def test_playbooks_list_only_the_home_tenant():
    store = MemoryStore()
    tenant, other = str(uuid4()), str(uuid4())
    await store.register_tenant(tenant, "us-east")
    await store.register_tenant(other, "us-east")
    first = await add_playbook(store, tenant_id=tenant, home_region="us-east", box_region="us-east",
                               name="placeholder guide", template="letter",
                               positions=[{"kind": "subject", "text": "placeholder position"}])
    await add_playbook(store, tenant_id=other, home_region="us-east", box_region="us-east",
                       name="placeholder foreign", template="letter",
                       positions=[{"kind": "subject", "text": "placeholder foreign position"}])
    result = await list_playbooks(store, tenant_id=tenant, home_region="us-east", box_region="us-east")
    assert result["items"] == [{"id": first["playbook"]["id"], "name": "placeholder guide",
                                "template": "letter", "positions": 1, "created_at": None}]


class _PoolConnection:
    def __init__(self, rows, status="error"):
        self.rows, self.status = rows, status

    async def execute(self, statement, *_args):
        return "SELECT 1"

    async def fetchrow(self, statement, *_args):
        if "zenas_pool.updates" in statement:
            return {"last_status": self.status, "last_update": datetime.now(timezone.utc)}
        return None

    async def fetch(self, statement, source_ids):
        assert GOVZA_SOURCE_IDS.issubset(set(source_ids))
        assert "effective_to IS NULL" in statement
        return self.rows


def _gov_row(source="govza-acts", content_hash="placeholder-hash"):
    return {"document_id": "placeholder-document", "source_id": source,
            "title": "Placeholder Companies Act", "text": "placeholder companies act text",
            "url": "https://www.gov.za/documents/placeholder", "doc_date": "2026-11-01",
            "effective_from": None, "content_hash": content_hash, "kind": "act",
            "licence": "placeholder public record"}


@pytest.mark.asyncio
async def test_failed_laws_africa_uses_loaded_govza_with_own_attribution():
    result = await laws_africa_snapshot(_PoolConnection([_gov_row()]), {"topic": "Companies Act"})
    assert result[0]["source"] == "govza-acts"
    assert result[0]["version"] == "placeholder-hash"
    assert result[0]["attribution"] == "placeholder public record"


@pytest.mark.asyncio
async def test_no_za_pool_documents_is_unavailable():
    with pytest.raises(ValueError, match="^source_unavailable$"):
        await laws_africa_snapshot(_PoolConnection([]), {"topic": "placeholder"})
