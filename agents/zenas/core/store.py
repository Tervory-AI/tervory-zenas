"""Tenant-scoped rows for the Zenas box. Tests use the memory store."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"

MATTERS_FOR_TENANT = """
SELECT id, tenant_id, jurisdiction, area, title, parties, status, created_by, created_at
FROM agent_zenas.matters
WHERE tenant_id = $1 AND id = $2
""".strip()

INSERT_MATTER = """
INSERT INTO agent_zenas.matters
    (id, tenant_id, jurisdiction, area, title, parties, status, created_by)
VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
""".strip()

INSERT_DOCUMENT = """
INSERT INTO agent_zenas.documents
    (id, matter_id, tenant_id, kind, version, body, sources, state)
VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
""".strip()

NEXT_VERSION = """
SELECT COALESCE(MAX(version), 0) FROM agent_zenas.documents
WHERE tenant_id = $1 AND matter_id = $2 AND kind = $3
""".strip()

DOCUMENT_FOR_TENANT = """
SELECT id, matter_id, tenant_id, kind, version, body, sources, state
FROM agent_zenas.documents
WHERE tenant_id = $1 AND id = $2
""".strip()

INSERT_REVIEW = """
INSERT INTO agent_zenas.reviews
    (id, document_id, tenant_id, reviewer, jurisdiction, admission_no, decision, changes)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
""".strip()

INSERT_CONSENT = """
INSERT INTO agent_zenas.consents
    (id, tenant_id, user_id, jurisdiction, wording_version, text_hash, ip)
VALUES ($1, $2, $3, $4, $5, $6, $7)
""".strip()

CURRENT_CONSENT = """
SELECT 1 FROM agent_zenas.consents
WHERE tenant_id = $1 AND user_id = $2 AND wording_version = $3
LIMIT 1
""".strip()

INSERT_PII = """
INSERT INTO agent_zenas.pii (id, matter_id, tenant_id, kind, ciphertext)
VALUES ($1, $2, $3, $4, $5)
""".strip()

TENANT_HOME = """
SELECT tenant_id, home_region
FROM agent_zenas.tenants
WHERE tenant_id = $1
""".strip()

INSERT_TENANT = """
INSERT INTO agent_zenas.tenants (tenant_id, home_region)
VALUES ($1, $2)
ON CONFLICT (tenant_id) DO NOTHING
""".strip()

OPEN_DOCUMENTS = """
SELECT id, matter_id, tenant_id, kind, version, body, sources, state
FROM agent_zenas.documents
WHERE tenant_id = $1 AND state IN ('draft', 'in_review')
ORDER BY created_at DESC
""".strip()

READS = (
    MATTERS_FOR_TENANT,
    NEXT_VERSION,
    DOCUMENT_FOR_TENANT,
    CURRENT_CONSENT,
    TENANT_HOME,
    OPEN_DOCUMENTS,
)


def schema_statements() -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    for line in SCHEMA_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(buf).strip().rstrip(";").strip()
            buf = []
            if statement:
                statements.append(statement)
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


def _id() -> str:
    return str(uuid.uuid4())


def _as_uuid(value: str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class MemoryStore:
    def __init__(self) -> None:
        self.matters: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.consents: list[dict[str, Any]] = []
        self.pii: list[dict[str, Any]] = []
        self.tenants: list[dict[str, Any]] = []

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        for row in self.tenants:
            if row["tenant_id"] == str(tenant_id):
                return dict(row)
        return None

    async def register_tenant(self, tenant_id: str, home_region: str) -> dict[str, Any]:
        from agents.zenas.core.engine import normalize_home

        home = normalize_home(home_region)
        if home not in ("us-east", "za"):
            return {"ok": False, "reason": "home region must be us-east or za"}
        existing = await self.get_tenant(tenant_id)
        if existing is not None:
            if existing["home_region"] != home:
                return {"ok": False, "reason": "home region is already set"}
            return {"ok": True, "tenant_id": str(tenant_id), "home_region": home, "created": False}
        self.tenants.append({"tenant_id": str(tenant_id), "home_region": home})
        return {"ok": True, "tenant_id": str(tenant_id), "home_region": home, "created": True}

    async def list_open_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.documents
            if row["tenant_id"] == str(tenant_id) and row.get("state") in ("draft", "in_review")
        ]

    async def add_matter(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["tenant_id"] = str(saved["tenant_id"])
        self.matters.append(saved)
        return saved

    async def get_matter(self, tenant_id: str, matter_id: str) -> dict[str, Any] | None:
        for row in self.matters:
            if row["tenant_id"] == str(tenant_id) and row["id"] == str(matter_id):
                return row
        return None

    async def add_document(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["tenant_id"] = str(saved["tenant_id"])
        saved["matter_id"] = str(saved["matter_id"])
        self.documents.append(saved)
        return saved

    async def next_version(self, tenant_id: str, matter_id: str, kind: str) -> int:
        versions = [
            int(row["version"])
            for row in self.documents
            if row["tenant_id"] == str(tenant_id) and row["matter_id"] == str(matter_id) and row["kind"] == kind
        ]
        return (max(versions) if versions else 0) + 1

    async def get_document(self, tenant_id: str, document_id: str) -> dict[str, Any] | None:
        for row in self.documents:
            if row["tenant_id"] == str(tenant_id) and row["id"] == str(document_id):
                return row
        return None

    async def add_review(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["tenant_id"] = str(saved["tenant_id"])
        self.reviews.append(saved)
        return saved

    async def add_consent(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["tenant_id"] = str(saved["tenant_id"])
        self.consents.append(saved)
        return saved

    async def has_current_consent(self, tenant_id: str, user_id: str, version: str) -> bool:
        return any(
            row["tenant_id"] == str(tenant_id) and row["user_id"] == user_id and row["wording_version"] == version
            for row in self.consents
        )

    async def add_pii(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["tenant_id"] = str(saved["tenant_id"])
        saved["matter_id"] = str(saved["matter_id"])
        self.pii.append(saved)
        return saved

    async def set_document_state(self, tenant_id: str, document_id: str, state: str) -> dict[str, Any] | None:
        document = await self.get_document(tenant_id, document_id)
        if document is None:
            return None
        document["state"] = state
        return document


class PgStore:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def apply_schema(self) -> None:
        statements = schema_statements()
        async with self.pool.acquire() as conn:
            for statement in statements:
                if statement.upper().startswith("CREATE SCHEMA"):
                    try:
                        await conn.execute(statement)
                    except Exception:
                        present = await conn.fetchval(
                            "SELECT 1 FROM pg_namespace WHERE nspname = 'agent_zenas'"
                        )
                        if present != 1:
                            raise
                    continue
                await conn.execute(statement)

    async def add_matter(self, row: dict[str, Any]) -> dict[str, Any]:
        matter_id = str(row.get("id") or _id())
        async with self.pool.acquire() as conn:
            await conn.execute(
                INSERT_MATTER,
                _as_uuid(matter_id),
                _as_uuid(row["tenant_id"]),
                row["jurisdiction"],
                row["area"],
                row["title"],
                json.dumps(row.get("parties") or []),
                row.get("status") or "open",
                row["created_by"],
            )
        saved = dict(row)
        saved["id"] = matter_id
        saved["tenant_id"] = str(row["tenant_id"])
        return saved

    async def get_matter(self, tenant_id: str, matter_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(MATTERS_FOR_TENANT, _as_uuid(tenant_id), _as_uuid(matter_id))
        if row is None:
            return None
        return _public_matter(row)

    async def add_document(self, row: dict[str, Any]) -> dict[str, Any]:
        document_id = str(row.get("id") or _id())
        async with self.pool.acquire() as conn:
            await conn.execute(
                INSERT_DOCUMENT,
                _as_uuid(document_id),
                _as_uuid(row["matter_id"]),
                _as_uuid(row["tenant_id"]),
                row["kind"],
                int(row["version"]),
                row["body"],
                json.dumps(row.get("sources") or []),
                row.get("state") or "draft",
            )
        saved = dict(row)
        saved["id"] = document_id
        saved["tenant_id"] = str(row["tenant_id"])
        saved["matter_id"] = str(row["matter_id"])
        return saved

    async def next_version(self, tenant_id: str, matter_id: str, kind: str) -> int:
        async with self.pool.acquire() as conn:
            current = await conn.fetchval(NEXT_VERSION, _as_uuid(tenant_id), _as_uuid(matter_id), kind)
        return int(current or 0) + 1

    async def get_document(self, tenant_id: str, document_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(DOCUMENT_FOR_TENANT, _as_uuid(tenant_id), _as_uuid(document_id))
        if row is None:
            return None
        data = dict(row)
        data["id"] = str(data["id"])
        data["matter_id"] = str(data["matter_id"])
        data["tenant_id"] = str(data["tenant_id"])
        return data

    async def add_review(self, row: dict[str, Any]) -> dict[str, Any]:
        review_id = str(row.get("id") or _id())
        async with self.pool.acquire() as conn:
            await conn.execute(
                INSERT_REVIEW,
                _as_uuid(review_id),
                _as_uuid(row["document_id"]),
                _as_uuid(row["tenant_id"]),
                row["reviewer"],
                row["jurisdiction"],
                row["admission_no"],
                row["decision"],
                row.get("changes") or "",
            )
        saved = dict(row)
        saved["id"] = review_id
        saved["tenant_id"] = str(row["tenant_id"])
        return saved

    async def add_consent(self, row: dict[str, Any]) -> dict[str, Any]:
        consent_id = str(row.get("id") or _id())
        async with self.pool.acquire() as conn:
            await conn.execute(
                INSERT_CONSENT,
                _as_uuid(consent_id),
                _as_uuid(row["tenant_id"]),
                row["user_id"],
                row["jurisdiction"],
                row["wording_version"],
                row["text_hash"],
                row["ip"],
            )
        saved = dict(row)
        saved["id"] = consent_id
        saved["tenant_id"] = str(row["tenant_id"])
        return saved

    async def has_current_consent(self, tenant_id: str, user_id: str, version: str) -> bool:
        async with self.pool.acquire() as conn:
            found = await conn.fetchval(CURRENT_CONSENT, _as_uuid(tenant_id), user_id, version)
        return found is not None

    async def add_pii(self, row: dict[str, Any]) -> dict[str, Any]:
        pii_id = str(row.get("id") or _id())
        async with self.pool.acquire() as conn:
            await conn.execute(
                INSERT_PII,
                _as_uuid(pii_id),
                _as_uuid(row["matter_id"]),
                _as_uuid(row["tenant_id"]),
                row["kind"],
                row["ciphertext"],
            )
        saved = dict(row)
        saved["id"] = pii_id
        saved["tenant_id"] = str(row["tenant_id"])
        saved["matter_id"] = str(row["matter_id"])
        return saved

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(TENANT_HOME, _as_uuid(tenant_id))
        if row is None:
            return None
        return {"tenant_id": str(row["tenant_id"]), "home_region": str(row["home_region"])}

    async def register_tenant(self, tenant_id: str, home_region: str) -> dict[str, Any]:
        from agents.zenas.core.engine import normalize_home

        home = normalize_home(home_region)
        if home not in ("us-east", "za"):
            return {"ok": False, "reason": "home region must be us-east or za"}
        existing = await self.get_tenant(tenant_id)
        if existing is not None:
            if existing["home_region"] != home:
                return {"ok": False, "reason": "home region is already set", "home_region": existing["home_region"]}
            return {"ok": True, "tenant_id": str(tenant_id), "home_region": home, "created": False}
        async with self.pool.acquire() as conn:
            await conn.execute(INSERT_TENANT, _as_uuid(tenant_id), home)
        return {"ok": True, "tenant_id": str(tenant_id), "home_region": home, "created": True}

    async def list_open_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(OPEN_DOCUMENTS, _as_uuid(tenant_id))
        found = []
        for row in rows:
            data = dict(row)
            data["id"] = str(data["id"])
            data["matter_id"] = str(data["matter_id"])
            data["tenant_id"] = str(data["tenant_id"])
            sources = data.get("sources")
            if isinstance(sources, str):
                data["sources"] = json.loads(sources)
            found.append(data)
        return found

    async def set_document_state(self, tenant_id: str, document_id: str, state: str) -> dict[str, Any] | None:
        sql = """
        UPDATE agent_zenas.documents SET state = $3
        WHERE tenant_id = $1 AND id = $2
        """.strip()
        async with self.pool.acquire() as conn:
            await conn.execute(sql, _as_uuid(tenant_id), _as_uuid(document_id), state)
        return await self.get_document(tenant_id, document_id)


def _public_matter(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["id"] = str(data["id"])
    data["tenant_id"] = str(data["tenant_id"])
    parties = data.get("parties")
    if isinstance(parties, str):
        data["parties"] = json.loads(parties)
    return data
