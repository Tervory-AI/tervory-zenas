"""Z1: Zenas room, scope gate, and personal-information detection. No database and no network."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import skala

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.zenas.core.matters import (  # noqa: E402
    CONSENT_MESSAGE,
    CURRENT_WORDING_VERSION,
    ZA_HOME_MESSAGE,
    consider,
    model_prompt,
    open_matter,
    record_consent,
    record_review,
    upload_document,
)
from agents.zenas.core.scope_rules import classify  # noqa: E402
from agents.zenas.core.seal import key_bytes, open_seal, seal  # noqa: E402
from agents.zenas.core.store import (  # noqa: E402
    CURRENT_CONSENT,
    DOCUMENT_FOR_TENANT,
    INSERT_PII,
    MATTERS_FOR_TENANT,
    MemoryStore,
    READS,
    schema_statements,
)
from agents.zenas.pool.catalogue import CATALOGUE  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000010"
OTHER = "00000000-0000-0000-0000-000000000011"
KEY = bytes(range(32))
SCOPE = ROOT / "tank" / "skills" / "zenas.scope" / "1.0.0"
PII = ROOT / "tank" / "skills" / "zenas.pii" / "1.0.0"
NDA = "Draft a mutual NDA under Delaware law in the United States."


class MockTank:
    def __init__(self, scope: dict | None = None) -> None:
        self.calls: list[str] = []
        self.payloads: list[dict] = []
        self.scope = scope

    async def run_skill(self, tenant_id: str, skill_id: str, payload: dict) -> dict:
        self.calls.append(skill_id)
        self.payloads.append({"tenant_id": tenant_id, "skill": skill_id, "input": payload})
        if skill_id == "zenas.scope":
            if self.scope is not None:
                return self.scope
            return classify(str(payload.get("text") or ""), payload.get("jurisdiction"))
        return {"skill": skill_id, "tenant_id": tenant_id}


def _load_run(package: Path):
    path = package / "run.py"
    spec = importlib.util.spec_from_file_location(f"run_{package.parent.name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


def _cases(package: Path) -> list[dict]:
    rows = []
    for line in (package / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


class _Model:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0
        self.messages: list[object] = []

    async def model(self, messages: object, *_args: object, **_kwargs: object) -> str:
        self.calls += 1
        self.messages.append(messages)
        return self.content


def _service_block(compose: str, name: str) -> str:
    lines = compose.splitlines()
    start = next(index for index, line in enumerate(lines) if line == f"  {name}:")
    chunk: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("  ") and not line.startswith("   "):
            break
        chunk.append(line)
    return "\n".join(chunk)


@pytest.mark.asyncio
async def test_scope_evals_and_the_required_groups() -> None:
    run = _load_run(SCOPE)
    cases = _cases(SCOPE)
    assert len(cases) >= 35
    groups = {"criminal": 0, "regulatory": 0, "not_yet": 0}
    failed: list[str] = []
    for case in cases:
        group = case.get("group")
        if group in groups:
            groups[group] += 1
        ctx = _Model("{}")
        output = await run(case["input"], ctx)
        ok, why = skala.score_case(case["expected"], output, case["tolerance"])
        if not ok:
            failed.append(f"{case['name']}: {why}")
        if group in ("criminal", "not_yet", "regulatory", "allowed"):
            assert ctx.calls == 0, case["name"]
    assert groups["criminal"] >= 10
    assert groups["regulatory"] >= 5
    assert groups["not_yet"] >= 5
    assert not failed, "\n".join(failed)
    criminal = [case for case in cases if case.get("group") == "criminal"]
    not_yet = [case for case in cases if case.get("group") == "not_yet"]
    regulatory = [case for case in cases if case.get("group") == "regulatory"]
    assert all(case["expected"]["criminal"] is True and case["expected"]["in_scope"] is False for case in criminal)
    assert all("not offered yet" in case["expected"]["reason"].lower() for case in not_yet)
    assert all(case["expected"]["in_scope"] is True and case["expected"]["needs_lawyer"] is True for case in regulatory)
    assert all(case["expected"]["criminal"] is False for case in regulatory)


@pytest.mark.asyncio
async def test_pii_evals_hide_the_raw_value() -> None:
    run = _load_run(PII)
    cases = _cases(PII)
    assert len(cases) >= 10
    for case in cases:
        output = await run(case["input"], None)
        ok, why = skala.score_case(case["expected"], output, case["tolerance"])
        assert ok, f"{case['name']}: {why}"
        blob = json.dumps(output)
        for secret in ("8001015009087", "219-09-9999", "A12345678"):
            if secret in case["input"]["text"] and secret not in case["expected"]["text"]:
                assert secret not in blob


@pytest.mark.asyncio
async def test_criminal_question_never_reaches_another_skill() -> None:
    tank = MockTank(
        {
            "in_scope": True,
            "area": "contracts",
            "jurisdiction": "us",
            "criminal": False,
            "needs_lawyer": True,
            "reason": "in",
        }
    )
    result = await consider(
        tank,
        tenant_id=TENANT,
        text="We have a bail hearing tomorrow in the magistrates court.",
        follow=[
            {"skill": "zenas.pii", "input": {"text": "secret"}},
            {"skill": "zenas.memo", "input": {"text": "secret"}},
        ],
    )
    assert result["stopped"] is True
    assert result["scope"]["criminal"] is True
    assert tank.calls == ["zenas.scope"]
    assert result["outputs"] == []


@pytest.mark.asyncio
async def test_in_scope_question_may_call_the_next_skill() -> None:
    tank = MockTank()
    result = await consider(
        tank,
        tenant_id=TENANT,
        text=NDA,
        follow=[{"skill": "zenas.memo", "input": {"question": "terms"}}],
    )
    assert result["stopped"] is False
    assert tank.calls == ["zenas.scope", "zenas.memo"]


@pytest.mark.asyncio
async def test_za_home_tenant_is_refused_in_us_east_and_nothing_is_stored() -> None:
    tank = MockTank()
    store = MemoryStore()
    await store.register_tenant(TENANT, "za")
    result = await open_matter(
        store,
        tank,
        tenant_id=TENANT,
        home_region="za",
        jurisdiction="za",
        title=NDA + " ID 8001015009087",
        created_by="clerk",
        user_id="user-1",
        box_region="us-east",
        key=KEY,
    )
    assert result["ok"] is False
    assert ZA_HOME_MESSAGE in result["reason"]
    assert tank.calls == []
    assert store.matters == []
    assert store.documents == []
    assert store.pii == []
    assert store.consents == []
    consent = await record_consent(
        store,
        tenant_id=TENANT,
        home_region="za",
        user_id="user-1",
        jurisdiction="za",
        wording="I agree",
        ip="127.0.0.1",
        box_region="us-east",
    )
    assert consent["ok"] is False
    assert store.consents == []


@pytest.mark.asyncio
async def test_za_tenant_needs_consent_on_the_za_box() -> None:
    tank = MockTank()
    store = MemoryStore()
    await store.register_tenant(TENANT, "za")
    refused = await open_matter(
        store,
        tank,
        tenant_id=TENANT,
        home_region="za",
        jurisdiction="za",
        title=NDA,
        created_by="clerk",
        user_id="user-1",
        box_region="za",
        key=KEY,
    )
    assert refused["ok"] is False
    assert refused["reason"] == CONSENT_MESSAGE
    assert tank.calls == []
    assert store.matters == []
    saved = await record_consent(
        store,
        tenant_id=TENANT,
        home_region="za",
        user_id="user-1",
        jurisdiction="za",
        wording="I agree to the wording",
        ip="10.0.0.8",
        box_region="za",
    )
    assert saved["ok"] is True
    assert store.consents[0]["wording_version"] == CURRENT_WORDING_VERSION
    assert store.consents[0]["text_hash"] != "I agree to the wording"
    opened = await open_matter(
        store,
        tank,
        tenant_id=TENANT,
        home_region="za",
        jurisdiction="za",
        title=NDA,
        created_by="clerk",
        user_id="user-1",
        box_region="za",
        key=KEY,
    )
    assert opened["ok"] is True
    assert opened["matter"]["jurisdiction"] == "za"


@pytest.mark.asyncio
async def test_us_tenant_may_ask_about_south_african_law() -> None:
    tank = MockTank()
    store = MemoryStore()
    await store.register_tenant(TENANT, "us-east")
    opened = await open_matter(
        store,
        tank,
        tenant_id=TENANT,
        home_region="us-east",
        jurisdiction="za",
        title="Draft a supply agreement for a company in Cape Town under South African law.",
        created_by="clerk",
        user_id="user-1",
        box_region="us-east",
        key=KEY,
    )
    assert opened["ok"] is True
    assert opened["matter"]["jurisdiction"] == "za"
    assert opened["matter"]["area"] == "contracts"
    assert store.consents == []


@pytest.mark.asyncio
async def test_upload_redacts_and_a_criminal_upload_stores_nothing() -> None:
    tank = MockTank()
    store = MemoryStore()
    await store.register_tenant(TENANT, "us-east")
    opened = await open_matter(
        store,
        tank,
        tenant_id=TENANT,
        home_region="us-east",
        jurisdiction="us",
        title="Draft a mutual NDA for 219-09-9999 under Delaware law in the United States.",
        created_by="clerk",
        user_id="user-1",
        box_region="us-east",
        parties=[{"name": "8001015009087"}],
        key=KEY,
    )
    assert opened["ok"] is True
    matter = opened["matter"]
    assert "219-09-9999" not in matter["title"]
    assert "8001015009087" not in json.dumps(matter["parties"])
    assert "[SSN_1]" in matter["title"]
    assert store.pii
    for row in store.pii:
        assert "219-09-9999" not in row["ciphertext"]
        assert "8001015009087" not in row["ciphertext"]
        assert open_seal(row["ciphertext"], KEY) in {"219-09-9999", "8001015009087"}
    tank.calls.clear()
    uploaded = await upload_document(
        store,
        tank,
        tenant_id=TENANT,
        matter_id=matter["id"],
        kind="notes",
        body="Passport number A12345678 sits in this mutual NDA draft.",
        box_region="us-east",
        home_region="us-east",
        key=KEY,
    )
    assert uploaded["ok"] is True
    assert "A12345678" not in uploaded["document"]["body"]
    assert "A12345678" not in uploaded["prompt"]
    assert uploaded["prompt"] == model_prompt(uploaded["document"]["body"])
    assert tank.calls == ["zenas.scope"]
    assert "A12345678" not in tank.payloads[-1]["input"]["text"]
    criminal = MockTank(
        {
            "in_scope": True,
            "area": "contracts",
            "jurisdiction": "us",
            "criminal": False,
            "needs_lawyer": True,
            "reason": "in",
        }
    )
    before = (len(store.documents), len(store.pii))
    refused = await upload_document(
        store,
        criminal,
        tenant_id=TENANT,
        matter_id=matter["id"],
        kind="notes",
        body="We have a bail hearing tomorrow. SSN 219-09-9999.",
        box_region="us-east",
        home_region="us-east",
        key=KEY,
    )
    assert refused["ok"] is False
    assert criminal.calls == ["zenas.scope"]
    assert (len(store.documents), len(store.pii)) == before
    assert "219-09-9999" not in criminal.payloads[0]["input"]["text"]


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_the_matter() -> None:
    tank = MockTank()
    store = MemoryStore()
    await store.register_tenant(TENANT, "us-east")
    opened = await open_matter(
        store,
        tank,
        tenant_id=TENANT,
        home_region="us-east",
        jurisdiction="us",
        title=NDA,
        created_by="clerk",
        user_id="user-1",
        box_region="us-east",
    )
    matter_id = opened["matter"]["id"]
    assert await store.get_matter(OTHER, matter_id) is None
    missed = await upload_document(
        store,
        MockTank(),
        tenant_id=OTHER,
        matter_id=matter_id,
        kind="notes",
        body=NDA,
        box_region="us-east",
        home_region="us-east",
    )
    assert missed["ok"] is False
    assert store.documents == []


@pytest.mark.asyncio
async def test_review_is_scoped_to_the_tenant() -> None:
    tank = MockTank()
    store = MemoryStore()
    await store.register_tenant(TENANT, "us-east")
    opened = await open_matter(
        store,
        tank,
        tenant_id=TENANT,
        home_region="us-east",
        jurisdiction="us",
        title=NDA,
        created_by="clerk",
        user_id="user-1",
        box_region="us-east",
    )
    uploaded = await upload_document(
        store,
        tank,
        tenant_id=TENANT,
        matter_id=opened["matter"]["id"],
        kind="draft",
        body=NDA,
        box_region="us-east",
        home_region="us-east",
    )
    saved = await record_review(
        store,
        tenant_id=TENANT,
        document_id=uploaded["document"]["id"],
        reviewer="Ada Counsel",
        jurisdiction="us",
        admission_no="12345",
        decision="approved",
        changes="none",
    )
    assert saved["ok"] is True
    missed = await record_review(
        store,
        tenant_id=OTHER,
        document_id=uploaded["document"]["id"],
        reviewer="Ada Counsel",
        jurisdiction="us",
        admission_no="12345",
        decision="approved",
    )
    assert missed["ok"] is False
    assert len(store.reviews) == 1


def test_schema_is_additive_and_queries_are_tenant_scoped() -> None:
    text = (ROOT / "agents" / "zenas" / "schema.sql").read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS agent_zenas" in text
    for table in ("matters", "documents", "reviews", "consents", "pii"):
        assert f"agent_zenas.{table}" in text
        assert "tenant_id" in text
    for column in (
        "jurisdiction",
        "parties",
        "created_by",
        "wording_version",
        "text_hash",
        "ciphertext",
        "admission_no",
        "draft",
        "in_review",
        "released",
        "rejected",
    ):
        assert column in text
    lowered = text.lower()
    assert "drop " not in lowered
    assert "rename " not in lowered
    for statement in schema_statements():
        upper = statement.upper()
        if upper.startswith("INSERT"):
            assert "ON CONFLICT" in upper and "DO NOTHING" in upper
        else:
            assert "IF NOT EXISTS" in upper
    for sql in READS:
        assert "tenant_id" in sql
    assert "tenant_id" in MATTERS_FOR_TENANT
    assert "tenant_id" in DOCUMENT_FOR_TENANT
    assert "tenant_id" in CURRENT_CONSENT
    assert "tenant_id" in INSERT_PII


def test_room_files_and_main_compose_add_the_corridor() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count("TANK_KEY_ZENAS: ${TANK_KEY_ZENAS:-}") == 2
    assert "AGENT_DB_PASSWORD_ZENAS: ${AGENT_DB_PASSWORD_ZENAS:-}" in compose
    assert "name: tervory-gw-zenas" in compose
    for service in ("redis", "qdrant", "litellm", "searxng", "tiger", "workspace", "agent-roles"):
        assert "tervory-gw-zenas" not in _service_block(compose, service)
    for service in ("postgres", "hermes", "tank"):
        assert "tervory-gw-zenas" in _service_block(compose, service)
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "TANK_KEY_ZENAS=" in example
    assert "AGENT_DB_PASSWORD_ZENAS=" in example
    assert "ZENAS_PII_KEY=" in example
    agent = (ROOT / "agents" / "zenas" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "name: tervory-gw-zenas" in agent
    assert "postgresql://agent_zenas:" in agent
    assert "TANK_KEY: ${TANK_KEY_ZENAS:-}" in agent
    assert "HERMES_URL: http://hermes:8000" in agent
    assert "mem_limit: 512m" in agent
    assert "POSTGRES_PASSWORD" not in agent
    assert "POSTGRES_USER" not in agent
    readme = (ROOT / "agents" / "README.md").read_text(encoding="utf-8")
    assert "tervory-gw-zenas" in readme
    assert "agent_zenas" in readme
    assert "TANK_KEY_ZENAS" in readme


def test_skill_config_matches_the_box_copy_and_policy_row() -> None:
    assert (SCOPE / "rules.py").read_bytes() == (ROOT / "agents" / "zenas" / "core" / "scope_rules.py").read_bytes()
    assert (SCOPE / "areas.json").read_bytes() == (ROOT / "agents" / "zenas" / "core" / "areas.json").read_bytes()
    current_pii = ROOT / "tank" / "skills" / "zenas.pii" / "1.1.0" / "detect.py"
    assert current_pii.read_bytes() == (ROOT / "agents" / "zenas" / "core" / "pii_detect.py").read_bytes()
    policy = (SCOPE / "policy.sql").read_text(encoding="utf-8")
    assert "zenas.scope" in policy
    assert "'tenant'" in policy
    assert "'luna'" in policy
    meta = (SCOPE / "skill.yaml").read_text(encoding="utf-8")
    assert "task_type: zenas.scope" in meta
    assert "cache_scope: tenant" in meta
    dockerfile = (ROOT / "agents" / "zenas" / "Dockerfile").read_text(encoding="utf-8")
    assert "zenas/pool" in dockerfile
    assert 'cryptography==50.0.2' in dockerfile
    chassis = (ROOT / "agents" / "_chassis" / "requirements.txt").read_text(encoding="utf-8")
    tank_req = (ROOT / "tank" / "requirements.txt").read_text(encoding="utf-8")
    assert "cryptography" not in chassis
    assert "cryptography" not in tank_req
    app = (ROOT / "agents" / "zenas" / "app.py").read_text(encoding="utf-8")
    assert "agents.zenas.pool" in app
    assert len(CATALOGUE) > 0


def test_seal_round_trip() -> None:
    token = seal("219-09-9999", KEY)
    again = seal("219-09-9999", KEY)
    assert token != again
    assert "219-09-9999" not in token
    assert open_seal(token, KEY) == "219-09-9999"
    assert open_seal(again, KEY) == "219-09-9999"
    with pytest.raises(ValueError):
        open_seal(token, bytes(range(1, 33)))
    raw = bytearray(base64.b64decode(token))
    raw[-1] ^= 0x01
    with pytest.raises(ValueError):
        open_seal(base64.b64encode(raw).decode("ascii"), KEY)
    assert key_bytes(KEY.hex()) == KEY
    assert key_bytes("not-hex") is None
    assert key_bytes(KEY.hex()[:-2]) is None
    assert key_bytes(KEY.hex() + "aa") is None


@pytest.mark.asyncio
async def test_model_cannot_overrule_a_rule_and_can_name_an_allowed_area() -> None:
    run = _load_run(SCOPE)
    ctx = _Model('{"area":"contracts","jurisdiction":"us"}')
    criminal = await run({"text": "We have a bail hearing tomorrow."}, ctx)
    assert criminal["criminal"] is True
    assert criminal["in_scope"] is False
    assert ctx.calls == 0
    vague = await run({"text": "Please look at the attached note."}, ctx)
    assert ctx.calls == 1
    assert vague["in_scope"] is True
    assert vague["area"] == "contracts"
    assert vague["criminal"] is False


@pytest.mark.asyncio
async def test_scope_model_never_sees_an_identifier() -> None:
    run = _load_run(SCOPE)
    ctx = _Model('{"area":"contracts","jurisdiction":"za"}')
    text = "Please look at this note. The ID is 8001015009087. Call +27 82 123 4567."
    await run({"text": text}, ctx)
    assert ctx.calls == 1
    sent = json.dumps(ctx.messages)
    assert "8001015009087" not in sent
    assert "+27 82 123 4567" not in sent
    assert "[SA_ID_1]" in sent
    assert "[PHONE_1]" in sent


def test_app_imports_the_pool_package() -> None:
    path = ROOT / "agents" / "zenas" / "app.py"
    spec = importlib.util.spec_from_file_location("zenas_box_app", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CATALOGUE is CATALOGUE
    assert any(item.source_id for item in module.CATALOGUE)
