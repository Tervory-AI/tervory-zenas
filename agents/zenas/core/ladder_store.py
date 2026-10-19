"""Rows for the legal ladder. Tests use the memory store.

Every read is limited to one region. A tenant-tied row is not returned to
another tenant. Nothing here copies a row into another region.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from agents.zenas.core.engine import embed_text, normalize_box, normalize_home, question_key


def _id() -> str:
    return str(uuid.uuid4())


def _as_uuid(value: str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _region(value: str) -> str:
    return normalize_box(value)


def _row_is_visible(row: dict[str, Any], *, region: str, tenant_id: str, sharing: str) -> bool:
    """Owner rows stay private. Public record is shared. Masked client work is shared in-region.

    A held-out case is never reused, on this box or any other.
    """
    if row.get("holdout"):
        return False
    if row.get("region") != region:
        return False
    source = str(row.get("source") or "")
    owner = str(row.get("tenant_id") or "")
    kind = str(row.get("material_type") or "")
    if owner and owner == str(tenant_id):
        return True
    if source == "case" and kind == "public_record":
        return True
    return sharing == "region" and source == "released" and kind == "client_work"


def _card_servable(row: dict[str, Any], *, allow_active: bool) -> bool:
    """A lawyer-approved card is always reusable. An active card is reusable only while review is off."""
    status = str(row.get("status") or "")
    if status == "stale":
        return False
    if status == "lawyer_approved":
        return True
    return bool(allow_active and row.get("active") and status == "draft")


async def _review_on(ladder: Any, *, region: str) -> bool:
    from agents.zenas.core.review_switch import review_required

    return await review_required(ladder, region=region)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _public_card(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["id"] = str(data["id"])
    if data.get("tenant_id") is not None:
        data["tenant_id"] = str(data["tenant_id"])
    for key in ("elements", "exceptions", "defences", "remedies", "sources", "decision_table"):
        data[key] = _json(data.get(key) or ([] if key != "decision_table" else []))
    data["cost_usd"] = float(data.get("cost_usd") or 0)
    data["edit_count"] = int(data.get("edit_count") or 0)
    if data.get("as_at") is not None:
        data["as_at"] = str(data["as_at"])[:10]
    return data


class LadderMemory:
    def __init__(self) -> None:
        self.rule_cards: list[dict[str, Any]] = []
        self.worked: list[dict[str, Any]] = []
        self.pairs: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.notes: list[dict[str, Any]] = []
        self.tenants: list[dict[str, Any]] = []
        self.selftests: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []
        self.review_mode: str | None = None

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        for row in self.tenants:
            if row["tenant_id"] == str(tenant_id):
                return dict(row)
        return None

    async def register_tenant(self, tenant_id: str, home_region: str) -> dict[str, Any]:
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

    async def add_rule_card(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["region"] = _region(str(saved["region"]))
        if saved.get("tenant_id") is not None:
            saved["tenant_id"] = str(saved["tenant_id"])
        saved["question_key"] = question_key(str(saved.get("question") or ""))
        saved["edit_count"] = int(saved.get("edit_count") or 0)
        saved["cost_usd"] = float(saved.get("cost_usd") or 0)
        saved["active"] = bool(saved.get("active"))
        self.rule_cards.append(saved)
        return _public_card(saved)

    async def get_rule_card(self, card_id: str, *, region: str, tenant_id: str) -> dict[str, Any] | None:
        box = _region(region)
        for row in self.rule_cards:
            if row["id"] != str(card_id) or row["region"] != box:
                continue
            owner = row.get("tenant_id")
            if owner is not None and str(owner) != str(tenant_id):
                continue
            return _public_card(row)
        return None

    async def find_approved(
        self,
        *,
        region: str,
        jurisdiction: str,
        key: str,
        tenant_id: str,
    ) -> dict[str, Any] | None:
        box = _region(region)
        allow_active = not await _review_on(self, region=box)
        for row in self.rule_cards:
            if row["region"] != box or not _card_servable(row, allow_active=allow_active):
                continue
            if row.get("jurisdiction") != jurisdiction or row.get("question_key") != key:
                continue
            owner = row.get("tenant_id")
            if owner is not None and str(owner) != str(tenant_id):
                continue
            return _public_card(row)
        return None

    async def cards_using(self, *, region: str, doc_id: str) -> list[dict[str, Any]]:
        box = _region(region)
        found = []
        for row in self.rule_cards:
            if row["region"] != box:
                continue
            sources = row.get("sources") or []
            if any(str(item.get("doc_id") or "") == doc_id for item in sources):
                found.append(_public_card(row))
        return found

    async def set_status(self, card_id: str, *, region: str, status: str, **fields: Any) -> dict[str, Any] | None:
        box = _region(region)
        for row in self.rule_cards:
            if row["id"] == str(card_id) and row["region"] == box:
                row["status"] = status
                row.update(fields)
                return _public_card(row)
        return None

    async def list_cards(self, *, region: str) -> list[dict[str, Any]]:
        box = _region(region)
        return [_public_card(row) for row in self.rule_cards if row["region"] == box]

    async def add_worked(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["region"] = _region(str(saved["region"]))
        if saved.get("tenant_id") is not None:
            saved["tenant_id"] = str(saved["tenant_id"])
        pattern = saved.get("fact_pattern") or {}
        if not saved.get("embedding"):
            saved["embedding"] = embed_text(str(pattern.get("narrative") or json.dumps(pattern.get("features") or {})))
        saved["holdout"] = bool(saved.get("holdout"))
        self.worked.append(saved)
        return saved

    async def worked_for_doc(self, *, region: str, pool_doc_id: str) -> dict[str, Any] | None:
        box = _region(region)
        for row in self.worked:
            if row["region"] == box and row.get("source") == "case" and row.get("pool_doc_id") == pool_doc_id:
                return row
        return None

    async def visible_worked(self, *, region: str, tenant_id: str, sharing: str) -> list[dict[str, Any]]:
        box = _region(region)
        return [row for row in self.worked if _row_is_visible(row, region=box, tenant_id=tenant_id, sharing=sharing)]

    async def count_worked(self, *, region: str, tenant_id: str) -> int:
        box = _region(region)
        return sum(1 for row in self.worked if _row_is_visible(row, region=box, tenant_id=tenant_id, sharing="tenant"))

    async def holdout_cases(self, *, region: str, jurisdiction: str) -> list[dict[str, Any]]:
        box = _region(region)
        found = []
        for row in self.worked:
            if row.get("region") != box or row.get("jurisdiction") != jurisdiction:
                continue
            if row.get("source") == "case" and row.get("holdout"):
                found.append(dict(row))
        return found

    async def case_tally(self, *, region: str, jurisdiction: str) -> dict[str, int]:
        box = _region(region)
        rows = [
            row
            for row in self.worked
            if row.get("region") == box and row.get("source") == "case" and row.get("jurisdiction") == jurisdiction
        ]
        return {"converted": len(rows), "held_out": sum(1 for row in rows if row.get("holdout"))}

    async def record_convert_failure(self, *, region: str, pool_doc_id: str, reason: str) -> None:
        box = _region(region)
        for row in self.failures:
            if row["region"] == box and row["pool_doc_id"] == pool_doc_id:
                row["reason"] = reason
                return
        self.failures.append({"region": box, "pool_doc_id": pool_doc_id, "reason": reason})

    async def clear_convert_failure(self, *, region: str, pool_doc_id: str) -> None:
        box = _region(region)
        self.failures = [
            row for row in self.failures if not (row["region"] == box and row["pool_doc_id"] == pool_doc_id)
        ]

    async def convert_failure_count(self, *, region: str) -> int:
        box = _region(region)
        return sum(1 for row in self.failures if row["region"] == box)

    async def set_review(self, *, region: str, review: str) -> None:
        self.review_mode = "on" if review == "on" else "off"

    async def get_review(self, *, region: str) -> str | None:
        del region
        return self.review_mode

    async def add_selftest(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["region"] = _region(str(saved["region"]))
        self.selftests.append(saved)
        return saved

    async def list_selftest(self, *, region: str) -> list[dict[str, Any]]:
        box = _region(region)
        return [dict(row) for row in self.selftests if row.get("region") == box]

    async def add_pair(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["region"] = _region(str(saved["region"]))
        if saved.get("tenant_id") is not None:
            saved["tenant_id"] = str(saved["tenant_id"])
        self.pairs.append(saved)
        return saved

    async def count_pairs(self, *, region: str, tenant_id: str) -> int:
        box = _region(region)
        total = 0
        for row in self.pairs:
            if row["region"] != box:
                continue
            owner = row.get("tenant_id")
            if owner is None or str(owner) == str(tenant_id):
                total += 1
        return total

    async def list_pairs(self, *, region: str, tenant_id: str) -> list[dict[str, Any]]:
        box = _region(region)
        found = []
        for row in self.pairs:
            if row["region"] != box:
                continue
            owner = row.get("tenant_id")
            if owner is None or str(owner) == str(tenant_id):
                found.append(row)
        return found

    async def add_run(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["id"] = str(saved.get("id") or _id())
        saved["region"] = _region(str(saved["region"]))
        saved["tenant_id"] = str(saved["tenant_id"])
        saved["cost_usd"] = float(saved.get("cost_usd") or 0)
        self.runs.append(saved)
        return saved

    async def list_runs(self, *, region: str, tenant_id: str) -> list[dict[str, Any]]:
        box = _region(region)
        return [row for row in self.runs if row["region"] == box and row["tenant_id"] == str(tenant_id)]

    async def add_note(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = dict(row)
        saved["region"] = _region(str(saved["region"]))
        saved["tenant_id"] = str(saved["tenant_id"])
        self.notes.append(saved)
        return saved


INSERT_CARD = """
INSERT INTO agent_zenas.rule_cards
    (id, region, tenant_id, jurisdiction, question, question_key, elements, exceptions,
     defences, remedies, sources, as_at, status, approved_by, admission_no, model_used,
     cost_usd, decision_table, edit_count, active)
VALUES
    ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb, $11::jsonb,
     $12::date, $13, $14, $15, $16, $17, $18::jsonb, $19, $20)
""".strip()

CARD_FOR_CALLER = """
SELECT * FROM agent_zenas.rule_cards
WHERE region = $1 AND id = $2 AND (tenant_id IS NULL OR tenant_id = $3)
""".strip()


class PgLadder:
    """asyncpg pool. Statements are tenant- and region-scoped."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.review_mode: str | None = None

    async def add_rule_card(self, row: dict[str, Any]) -> dict[str, Any]:
        card_id = str(row.get("id") or _id())
        owner = row.get("tenant_id")
        key = question_key(str(row.get("question") or ""))
        async with self.pool.acquire() as conn:
            await conn.execute(
                INSERT_CARD,
                _as_uuid(card_id),
                _region(str(row["region"])),
                _as_uuid(owner) if owner else None,
                row["jurisdiction"],
                row["question"],
                key,
                json.dumps(row.get("elements") or []),
                json.dumps(row.get("exceptions") or []),
                json.dumps(row.get("defences") or []),
                json.dumps(row.get("remedies") or []),
                json.dumps(row.get("sources") or []),
                row.get("as_at"),
                row.get("status") or "draft",
                row.get("approved_by"),
                row.get("admission_no"),
                row.get("model_used"),
                float(row.get("cost_usd") or 0),
                json.dumps(row.get("decision_table") or []),
                int(row.get("edit_count") or 0),
                bool(row.get("active")),
            )
        saved = dict(row)
        saved["id"] = card_id
        saved["question_key"] = key
        if owner is not None:
            saved["tenant_id"] = str(owner)
        return _public_card(saved)

    async def get_rule_card(self, card_id: str, *, region: str, tenant_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(CARD_FOR_CALLER, _region(region), _as_uuid(card_id), _as_uuid(tenant_id))
        if row is None:
            return None
        return _public_card(dict(row))

    async def find_approved(
        self,
        *,
        region: str,
        jurisdiction: str,
        key: str,
        tenant_id: str,
    ) -> dict[str, Any] | None:
        box = _region(region)
        allow_active = not await _review_on(self, region=box)
        sql = """
        SELECT * FROM agent_zenas.rule_cards
        WHERE region = $1 AND jurisdiction = $2 AND question_key = $3
          AND status <> 'stale'
          AND (
            status = 'lawyer_approved'
            OR ($5::boolean AND active IS TRUE AND status = 'draft')
          )
          AND (tenant_id IS NULL OR tenant_id = $4)
        ORDER BY tenant_id NULLS FIRST
        LIMIT 1
        """.strip()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, box, jurisdiction, key, _as_uuid(tenant_id), allow_active)
        if row is None:
            return None
        return _public_card(dict(row))

    async def cards_using(self, *, region: str, doc_id: str) -> list[dict[str, Any]]:
        sql = """
        SELECT * FROM agent_zenas.rule_cards
        WHERE region = $1 AND sources @> $2::jsonb
        """.strip()
        needle = json.dumps([{"doc_id": doc_id}])
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, _region(region), needle)
        return [_public_card(dict(row)) for row in rows]

    async def set_status(self, card_id: str, *, region: str, status: str, **fields: Any) -> dict[str, Any] | None:
        sets = ["status = $3"]
        args: list[Any] = [_region(region), _as_uuid(card_id), status]
        for name in ("approved_by", "admission_no", "edit_count"):
            if name in fields:
                args.append(fields[name])
                sets.append(f"{name} = ${len(args)}")
        sql = f"UPDATE agent_zenas.rule_cards SET {', '.join(sets)} WHERE region = $1 AND id = $2"
        async with self.pool.acquire() as conn:
            await conn.execute(sql, *args)
            row = await conn.fetchrow(
                "SELECT * FROM agent_zenas.rule_cards WHERE region = $1 AND id = $2",
                _region(region),
                _as_uuid(card_id),
            )
        if row is None:
            return None
        return _public_card(dict(row))

    async def list_cards(self, *, region: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM agent_zenas.rule_cards WHERE region = $1", _region(region))
        return [_public_card(dict(row)) for row in rows]

    async def add_worked(self, row: dict[str, Any]) -> dict[str, Any]:
        worked_id = str(row.get("id") or _id())
        owner = row.get("tenant_id")
        pattern = row.get("fact_pattern") or {}
        embedding = row.get("embedding") or embed_text(
            str(pattern.get("narrative") or json.dumps(pattern.get("features") or {}))
        )
        sql = """
        INSERT INTO agent_zenas.worked_matters
            (id, region, tenant_id, source, jurisdiction, area, rule_card_ids, fact_pattern,
             element_findings, outcome, lawyer_edits, as_at, pool_doc_id, pool_version_id,
             embedding, released_matter_id, material_type, holdout)
        VALUES
            ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10, $11, $12::date,
             $13, $14, $15::jsonb, $16, $17, $18)
        """.strip()
        async with self.pool.acquire() as conn:
            await conn.execute(
                sql,
                _as_uuid(worked_id),
                _region(str(row["region"])),
                _as_uuid(owner) if owner else None,
                row["source"],
                row["jurisdiction"],
                row.get("area") or "",
                json.dumps(row.get("rule_card_ids") or []),
                json.dumps(pattern),
                json.dumps(row.get("element_findings") or []),
                row.get("outcome") or "",
                row.get("lawyer_edits") or "",
                row.get("as_at"),
                row.get("pool_doc_id"),
                row.get("pool_version_id"),
                json.dumps(embedding),
                _as_uuid(row["released_matter_id"]) if row.get("released_matter_id") else None,
                row.get("material_type"),
                bool(row.get("holdout")),
            )
        saved = dict(row)
        saved["id"] = worked_id
        saved["embedding"] = embedding
        if owner is not None:
            saved["tenant_id"] = str(owner)
        return saved

    async def worked_for_doc(self, *, region: str, pool_doc_id: str) -> dict[str, Any] | None:
        sql = """
        SELECT * FROM agent_zenas.worked_matters
        WHERE region = $1 AND source = 'case' AND pool_doc_id = $2
        """.strip()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, _region(region), pool_doc_id)
        if row is None:
            return None
        return _row_worked(dict(row))

    async def visible_worked(self, *, region: str, tenant_id: str, sharing: str) -> list[dict[str, Any]]:
        if sharing == "region":
            sql = """
            SELECT * FROM agent_zenas.worked_matters
            WHERE region = $1
              AND COALESCE(holdout, false) = false
              AND (
                tenant_id = $2
                OR (source = 'case' AND material_type = 'public_record')
                OR (source = 'released' AND material_type = 'client_work')
              )
            """.strip()
        else:
            sql = """
            SELECT * FROM agent_zenas.worked_matters
            WHERE region = $1
              AND COALESCE(holdout, false) = false
              AND (
                tenant_id = $2
                OR (source = 'case' AND material_type = 'public_record')
              )
            """.strip()
        args: tuple[Any, ...] = (_region(region), _as_uuid(tenant_id))
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row_worked(dict(row)) for row in rows]

    async def count_worked(self, *, region: str, tenant_id: str) -> int:
        sql = """
        SELECT count(*) FROM agent_zenas.worked_matters
        WHERE region = $1
          AND COALESCE(holdout, false) = false
          AND (tenant_id = $2 OR (source = 'case' AND material_type = 'public_record'))
        """.strip()
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(sql, _region(region), _as_uuid(tenant_id))
        return int(value or 0)

    async def holdout_cases(self, *, region: str, jurisdiction: str) -> list[dict[str, Any]]:
        sql = """
        SELECT * FROM agent_zenas.worked_matters
        WHERE region = $1 AND jurisdiction = $2 AND source = 'case' AND holdout IS TRUE
        """.strip()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, _region(region), jurisdiction)
        return [_row_worked(dict(row)) for row in rows]

    async def case_tally(self, *, region: str, jurisdiction: str) -> dict[str, int]:
        sql = """
        SELECT
          count(*) FILTER (WHERE source = 'case' AND jurisdiction = $2) AS converted,
          count(*) FILTER (WHERE source = 'case' AND jurisdiction = $2 AND holdout IS TRUE) AS held_out
        FROM agent_zenas.worked_matters
        WHERE region = $1
        """.strip()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, _region(region), jurisdiction)
        return {"converted": int(row["converted"] or 0), "held_out": int(row["held_out"] or 0)}

    async def record_convert_failure(self, *, region: str, pool_doc_id: str, reason: str) -> None:
        sql = """
        INSERT INTO agent_zenas.convert_failures (region, pool_doc_id, reason)
        VALUES ($1, $2, $3)
        ON CONFLICT (region, pool_doc_id) DO UPDATE
          SET reason = EXCLUDED.reason, created_at = now()
        """.strip()
        async with self.pool.acquire() as conn:
            await conn.execute(sql, _region(region), pool_doc_id, reason)

    async def clear_convert_failure(self, *, region: str, pool_doc_id: str) -> None:
        sql = "DELETE FROM agent_zenas.convert_failures WHERE region = $1 AND pool_doc_id = $2"
        async with self.pool.acquire() as conn:
            await conn.execute(sql, _region(region), pool_doc_id)

    async def convert_failure_count(self, *, region: str) -> int:
        sql = "SELECT count(*) FROM agent_zenas.convert_failures WHERE region = $1"
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(sql, _region(region))
        return int(value or 0)

    async def set_review(self, *, region: str, review: str) -> None:
        mode = "on" if review == "on" else "off"
        sql = """
        INSERT INTO agent_zenas.review_settings (region, review)
        VALUES ($1, $2)
        ON CONFLICT (region) DO UPDATE SET review = EXCLUDED.review
        """.strip()
        async with self.pool.acquire() as conn:
            await conn.execute(sql, _region(region), mode)
        self.review_mode = mode

    async def get_review(self, *, region: str) -> str | None:
        sql = "SELECT review FROM agent_zenas.review_settings WHERE region = $1"
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(sql, _region(region))
        if value in ("on", "off"):
            self.review_mode = str(value)
            return str(value)
        return None

    async def add_selftest(self, row: dict[str, Any]) -> dict[str, Any]:
        run_id = str(row.get("id") or _id())
        sql = """
        INSERT INTO agent_zenas.selftest_runs
            (id, region, kind, area, jurisdiction, cases, outcome_agreement,
             element_agreement, citations_valid, cost_per_case, detail)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
        """.strip()
        async with self.pool.acquire() as conn:
            await conn.execute(
                sql,
                _as_uuid(run_id),
                _region(str(row["region"])),
                row["kind"],
                row.get("area") or "",
                row.get("jurisdiction") or "",
                int(row.get("cases") or 0),
                float(row.get("outcome_agreement") or 0),
                float(row.get("element_agreement") or 0),
                float(row.get("citations_valid") or 0),
                float(row.get("cost_per_case") or 0),
                json.dumps(row.get("detail") or {}),
            )
        saved = dict(row)
        saved["id"] = run_id
        return saved

    async def list_selftest(self, *, region: str) -> list[dict[str, Any]]:
        sql = """
        SELECT id, region, kind, area, jurisdiction, cases, outcome_agreement,
               element_agreement, citations_valid, cost_per_case, detail, created_at
        FROM agent_zenas.selftest_runs
        WHERE region = $1
        ORDER BY created_at DESC
        """.strip()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, _region(region))
        found = []
        for row in rows:
            item = dict(row)
            item["id"] = str(item["id"])
            item["detail"] = _json(item.get("detail") or {})
            for key in ("outcome_agreement", "element_agreement", "citations_valid", "cost_per_case"):
                item[key] = float(item.get(key) or 0)
            found.append(item)
        return found

    async def add_pair(self, row: dict[str, Any]) -> dict[str, Any]:
        pair_id = str(row.get("id") or _id())
        owner = row.get("tenant_id")
        sql = """
        INSERT INTO agent_zenas.training_pairs
            (id, region, tenant_id, source, worked_id, request, findings, outcome, memo, lawyer_edits, as_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11::date)
        """.strip()
        async with self.pool.acquire() as conn:
            await conn.execute(
                sql,
                _as_uuid(pair_id),
                _region(str(row["region"])),
                _as_uuid(owner) if owner else None,
                row["source"],
                _as_uuid(row["worked_id"]) if row.get("worked_id") else None,
                row.get("request") or "",
                json.dumps(row.get("findings") or []),
                row.get("outcome") or "",
                row.get("memo") or "",
                row.get("lawyer_edits") or "",
                row.get("as_at"),
            )
        saved = dict(row)
        saved["id"] = pair_id
        if owner is not None:
            saved["tenant_id"] = str(owner)
        return saved

    async def count_pairs(self, *, region: str, tenant_id: str) -> int:
        sql = """
        SELECT count(*) FROM agent_zenas.training_pairs
        WHERE region = $1 AND (tenant_id IS NULL OR tenant_id = $2)
        """.strip()
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(sql, _region(region), _as_uuid(tenant_id))
        return int(value or 0)

    async def list_pairs(self, *, region: str, tenant_id: str) -> list[dict[str, Any]]:
        sql = """
        SELECT * FROM agent_zenas.training_pairs
        WHERE region = $1 AND (tenant_id IS NULL OR tenant_id = $2)
        """.strip()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, _region(region), _as_uuid(tenant_id))
        return [dict(row) for row in rows]

    async def add_run(self, row: dict[str, Any]) -> dict[str, Any]:
        run_id = str(row.get("id") or _id())
        sql = """
        INSERT INTO agent_zenas.ladder_runs
            (id, region, tenant_id, kind, question_key, cost_usd, reused, coded, modeled, elements)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """.strip()
        async with self.pool.acquire() as conn:
            await conn.execute(
                sql,
                _as_uuid(run_id),
                _region(str(row["region"])),
                _as_uuid(row["tenant_id"]),
                row["kind"],
                row.get("question_key") or "",
                float(row.get("cost_usd") or 0),
                int(row.get("reused") or 0),
                int(row.get("coded") or 0),
                int(row.get("modeled") or 0),
                int(row.get("elements") or 0),
            )
        saved = dict(row)
        saved["id"] = run_id
        saved["tenant_id"] = str(row["tenant_id"])
        return saved

    async def list_runs(self, *, region: str, tenant_id: str) -> list[dict[str, Any]]:
        sql = """
        SELECT * FROM agent_zenas.ladder_runs
        WHERE region = $1 AND tenant_id = $2
        ORDER BY created_at
        """.strip()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, _region(region), _as_uuid(tenant_id))
        out = []
        for row in rows:
            data = dict(row)
            data["cost_usd"] = float(data.get("cost_usd") or 0)
            out.append(data)
        return out

    async def add_note(self, row: dict[str, Any]) -> dict[str, Any]:
        note_id = str(row.get("id") or _id())
        sql = """
        INSERT INTO agent_zenas.memory_notes (id, region, tenant_id, kind, body)
        VALUES ($1, $2, $3, $4, $5)
        """.strip()
        async with self.pool.acquire() as conn:
            await conn.execute(
                sql,
                _as_uuid(note_id),
                _region(str(row["region"])),
                _as_uuid(row["tenant_id"]),
                row.get("kind") or "",
                row.get("body") or "",
            )
        saved = dict(row)
        saved["id"] = note_id
        return saved

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        sql = "SELECT tenant_id, home_region FROM agent_zenas.tenants WHERE tenant_id = $1"
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, _as_uuid(tenant_id))
        if row is None:
            return None
        return {"tenant_id": str(row["tenant_id"]), "home_region": str(row["home_region"])}

    async def register_tenant(self, tenant_id: str, home_region: str) -> dict[str, Any]:
        home = normalize_home(home_region)
        if home not in ("us-east", "za"):
            return {"ok": False, "reason": "home region must be us-east or za"}
        existing = await self.get_tenant(tenant_id)
        if existing is not None:
            if existing["home_region"] != home:
                return {"ok": False, "reason": "home region is already set", "home_region": existing["home_region"]}
            return {"ok": True, "tenant_id": str(tenant_id), "home_region": home, "created": False}
        sql = """
        INSERT INTO agent_zenas.tenants (tenant_id, home_region)
        VALUES ($1, $2)
        ON CONFLICT (tenant_id) DO NOTHING
        """.strip()
        async with self.pool.acquire() as conn:
            await conn.execute(sql, _as_uuid(tenant_id), home)
        return {"ok": True, "tenant_id": str(tenant_id), "home_region": home, "created": True}


def _row_worked(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["id"] = str(data["id"])
    if data.get("tenant_id") is not None:
        data["tenant_id"] = str(data["tenant_id"])
    for key in ("rule_card_ids", "fact_pattern", "element_findings", "embedding"):
        data[key] = _json(data.get(key) or ([] if key != "fact_pattern" else {}))
    return data
