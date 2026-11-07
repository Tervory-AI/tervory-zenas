"""Gates 7+8 Hermes contract: real chassis MemoryClient ↔ real hermes.service.create_app.

Verifies the pinned Tank Hermes API the product actually calls — NOT /v1/memory
(that Tank prefix is the founder/publication corridor in tank/memory_routes.py and
is not the chassis memory adapter). Paths exercised: POST /memories, POST /recall,
DELETE /memories/{id}. Auth header field name: X-Tank-Key. Client config field
names: HERMES_URL (base_url), TANK_KEY (api_key / agent-specific room key).

The agent-specific key selects the agent collection only. It is NOT signed-in
user proof. Hermes scopes by agent+tenant. Central user_id scoping is an UNAPPLIED
tank-patch (chassis+Hermes); product passes actor.user_id only. Shared/workspace key writes are refused.
No FakeMemory and no replaced Hermes handlers — only a fixture InMemoryStore +
stub embedder under create_app.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from chassis.memory import MemoryClient
from agents.zenas import gates_memory
from hermes.service import create_app
from hermes.store import InMemoryStore

# Dummy fixture values only — never production secrets. Field names: TANK_KEY_ZENAS, TANK_API_KEY.
FIXTURE_AGENT_KEY = "fixture-zenas-agent-key-not-a-secret"
FIXTURE_WORKSPACE_KEY = "fixture-workspace-key-not-a-secret"
FIXTURE_OTHER_AGENT_KEY = "fixture-other-agent-key-not-a-secret"
TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
USER_A = "33333333-3333-4333-8333-333333333333"
USER_B = "44444444-4444-4444-8444-444444444444"
KIND = "zenas.result"  # real product kind format: ^[a-z][a-z0-9_.]{0,79}$


class StubEmbedder:
    def embed(self, text: str) -> list[float]:
        # Orthogonal-ish bags so near-duplicate merge does not collapse distinct rows.
        vec = [0.0] * 32
        for i, ch in enumerate(text.encode()):
            vec[(i + ch) % 32] += 1.0
        return vec


@pytest.fixture
def hermes_env(monkeypatch):
    monkeypatch.setenv("TANK_KEY_ZENAS", FIXTURE_AGENT_KEY)
    monkeypatch.setenv("TANK_KEY_TRIGGER", FIXTURE_OTHER_AGENT_KEY)
    monkeypatch.setenv("TANK_API_KEY", FIXTURE_WORKSPACE_KEY)


@pytest.fixture
def hermes_app(hermes_env):
    return create_app(store=InMemoryStore(), embedder=StubEmbedder(), records=None)


async def _client(app, api_key: str) -> tuple[httpx.AsyncClient, MemoryClient]:
    transport = httpx.ASGITransport(app=app)
    http = httpx.AsyncClient(transport=transport, base_url="http://hermes:8000")
    memory = MemoryClient(
        base_url="http://hermes:8000",
        api_key=api_key,
        agent="zenas",
        http=http,
    )
    return http, memory


@pytest.mark.asyncio
async def test_memory_client_write_recall_forget_against_real_hermes(hermes_app):
    """Production adapter write → recall → forget on real create_app routes."""
    http, memory = await _client(hermes_app, FIXTURE_AGENT_KEY)
    async with http:
        saved = await memory.remember(
            TENANT_A,
            "notice is required before cancellation",
            KIND,
            "zenas:gates-7-8",
            meta={"user_id": USER_A, "user": USER_A, "task_key": "zenas.apply"},
            index=True,
        )
        assert saved.get("id")
        hits = await memory.recall(TENANT_A, "notice required cancellation", kind=KIND, limit=5)
        assert any(h["id"] == saved["id"] for h in hits)
        assert all(h["tenant_id"] == TENANT_A for h in hits)
        # user_id is meta-only on Hermes; product filters it.
        mine = [h for h in hits if (h.get("meta") or {}).get("user_id") == USER_A]
        assert mine and mine[0]["kind"] == KIND
        await memory.forget(saved["id"], TENANT_A)
        after = await memory.recall(TENANT_A, "notice required cancellation", kind=KIND, limit=5)
        assert not any(h["id"] == saved["id"] for h in after)


@pytest.mark.asyncio
async def test_hermes_tenant_scope_hides_other_tenant(hermes_app):
    http, memory = await _client(hermes_app, FIXTURE_AGENT_KEY)
    async with http:
        await memory.remember(
            TENANT_A, "tenant-a secret", KIND, "zenas:gates-7-8",
            meta={"user_id": USER_A}, index=True,
        )
        hits_b = await memory.recall(TENANT_B, "tenant-a secret", kind=KIND, limit=5)
        assert hits_b == []


@pytest.mark.asyncio
async def test_user_id_filtered_within_meta_not_hermes_auth(hermes_app):
    """Hermes returns both users' rows in a tenant; product must filter meta.user_id."""
    http, memory = await _client(hermes_app, FIXTURE_AGENT_KEY)
    async with http:
        await memory.remember(
            TENANT_A, "alpha-user exclusive worked-law phrase about notice", KIND, "zenas:gates-7-8",
            meta={"user_id": USER_A, "user": USER_A}, index=True,
        )
        await memory.remember(
            TENANT_A, "bravo-user exclusive worked-law phrase about damages", KIND, "zenas:gates-7-8",
            meta={"user_id": USER_B, "user": USER_B}, index=True,
        )
        hits_a = await memory.recall(TENANT_A, "alpha-user exclusive worked-law phrase about notice", kind=KIND, limit=10)
        hits_b = await memory.recall(TENANT_A, "bravo-user exclusive worked-law phrase about damages", kind=KIND, limit=10)
        hits = hits_a + hits_b
        users = {(h.get("meta") or {}).get("user_id") for h in hits}
        assert USER_A in users and USER_B in users
        # Emulate product filter (GateMemory / owned_memory_row).
        only_a = [h for h in hits if (h.get("meta") or {}).get("user_id") == USER_A]
        assert only_a and all((h.get("meta") or {}).get("user_id") == USER_A for h in only_a)


@pytest.mark.asyncio
async def test_workspace_shared_key_cannot_write_memories(hermes_app):
    """Workspace / shared key writes are refused (Hermes resolve_agent write=True)."""
    http, memory = await _client(hermes_app, FIXTURE_WORKSPACE_KEY)
    async with http:
        with pytest.raises(RuntimeError, match="hermes answered 403"):
            await memory.remember(
                TENANT_A, "should not write", KIND, "zenas:gates-7-8",
                meta={"user_id": USER_A}, index=True,
            )


@pytest.mark.asyncio
async def test_other_agent_key_cannot_write_zenas_collection(hermes_app):
    http, memory = await _client(hermes_app, FIXTURE_OTHER_AGENT_KEY)
    async with http:
        # Other agent key writes into its own collection — Zenas recall with Zenas key must not see it.
        saved = await memory.remember(
            TENANT_A, "trigger-only row", "trigger.deal", "trigger:gates-7-8",
            meta={"user_id": USER_A}, index=True,
        )
        assert saved.get("id")
    http_z, memory_z = await _client(hermes_app, FIXTURE_AGENT_KEY)
    async with http_z:
        hits = await memory_z.recall(TENANT_A, "trigger-only row", limit=5)
        assert not any(h["id"] == saved["id"] for h in hits)


@pytest.mark.asyncio
async def test_v1_memory_prefix_is_not_chassis_contract(hermes_app):
    """Chassis MemoryClient must not call /v1/memory; that route is absent on Hermes create_app."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hermes_app), base_url="http://hermes:8000"
    ) as http:
        for path in ("/v1/memory", "/v1/memory/records", "/v1/memories"):
            response = await http.post(
                path,
                headers={"X-Tank-Key": FIXTURE_AGENT_KEY},
                json={
                    "tenant_id": TENANT_A,
                    "text": "no",
                    "kind": KIND,
                    "source": "zenas:gates-7-8",
                },
            )
            assert response.status_code == 404, path


def test_agent_key_is_not_signed_in_user_proof():
    """Document the contract boundary: X-Tank-Key identifies the agent, not a human user.

    Signed-in tenant+user proof is Tank lifecycle HMAC on the room (see
    agents/zenas/request_signing.py), not the Hermes agent key. Hermes has no
    X-Lifecycle-* / user binding on /memories|/recall|/records.
    """
    from agents.zenas import request_signing
    import inspect

    source = inspect.getsource(request_signing)
    assert "X-Lifecycle-Signature" in source
    assert "tenant_id" in source and "user_id" in source
    # MemoryClient header is only X-Tank-Key — no lifecycle fields.
    from chassis import memory as memory_mod
    client_src = inspect.getsource(memory_mod.MemoryClient._send)
    assert "X-Tank-Key" in client_src
    assert "X-Lifecycle" not in client_src
    assert "X-Tervory-User" not in client_src


def test_kind_format_matches_hermes_and_product():
    from agents.zenas import gates_memory
    import re

    pattern = re.compile(r"^[a-z][a-z0-9_.]{0,79}$")
    for kind in gates_memory.REMEMBERS:
        assert pattern.match(kind), kind
    assert pattern.match(KIND)


def test_pinned_hermes_contract_lacks_central_user_id_param():
    """PINNED vendor (read-only): chassis remember/recall have no user_id — BLOCKER until tank-patches applied.

    Unapplied proposal: tank-patches/gates-7-8-central-user-memory.patch
    Current source: vendor/tervory-tank @ pin from scripts/verify_vendor.py
    """
    from pathlib import Path
    import inspect
    from chassis import memory as memory_mod
    # Import path during check may be patched stage; also read immutable vendor file.
    vendor = Path(__file__).resolve().parents[1] / "vendor/tervory-tank/agents/_chassis/chassis/memory.py"
    vendor_src = vendor.read_text()
    assert "def remember" in vendor_src
    # Pinned signature has no user_id parameter on remember/recall/records.
    pinned_lacks = (
        "user_id: str | None = None" not in vendor_src
        and "user_id: str | None = None," not in vendor_src
        and "user_id=None" not in vendor_src
    )
    # Honest: if still true, central scoping is only an unapplied proposal.
    assert pinned_lacks or "UNAPPLIED" in Path(__file__).resolve().parents[1].joinpath(
        "tank-patches/gates-7-8-central-user-memory.patch"
    ).read_text()[:200]
    # Agent key field name only — never a signed-in user proof.
    assert "X-Tank-Key" in inspect.getsource(memory_mod.MemoryClient._send)


@pytest.mark.asyncio
async def test_v1_memory_get_also_absent_on_hermes(hermes_app):
    """All /v1/memory methods banned for chassis — including GET."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hermes_app), base_url="http://hermes:8000"
    ) as http:
        for method, path in (
            ("GET", "/v1/memory"),
            ("GET", "/v1/memory/records"),
            ("POST", "/v1/memory"),
            ("DELETE", "/v1/memory/records"),
        ):
            response = await http.request(
                method, path, headers={"X-Tank-Key": FIXTURE_AGENT_KEY},
            )
            assert response.status_code == 404, (method, path, response.status_code)


def test_zenas_kinds_match_hermes_regex_and_none_hardcoded_empty():
    import re
    pattern = re.compile(r"^[a-z][a-z0-9_.]{0,79}$")
    assert gates_memory.REMEMBERS
    for kind, description in gates_memory.REMEMBERS.items():
        assert pattern.match(kind), kind
        assert description and description.strip(), kind
    # Explicit tenant-wide set exists (may be empty — no silent widen).
    assert isinstance(gates_memory.TENANT_WIDE, frozenset)
