"""Offline Apollo upload comparison. No vendor, model, or enrichment requests."""
import csv
from collections import Counter, defaultdict
import io
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from base_lists import Store, receipt, verified
from base_metrics import normal


class ApolloArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    list_id: UUID
    attachment_id: UUID


def compare(records: list[dict], text: str) -> dict:
    """Exact licence-key or unique website match; ambiguous identities never match."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise HTTPException(422, "Invalid Apollo CSV")
    aliases = {"company_website": "website", "company_phone": "phone", "emails": "email"}
    columns = [aliases.get(key.strip().lower().replace(" ", "_"), key.strip().lower().replace(" ", "_"))
               for key in reader.fieldnames]
    if len(set(columns)) != len(columns) or not set(columns).intersection({"licence_key", "website", "phone", "email"}):
        raise HTTPException(422, "Invalid Apollo CSV")
    apollo = []
    for raw in reader:
        if None in raw or any(value is None for value in raw.values()):
            raise HTTPException(422, "Invalid Apollo CSV")
        row = {key: value.strip() for key, value in zip(columns, raw.values())}
        apollo.append(row)
        if len(apollo) > 10000:
            raise HTTPException(422, "Apollo CSV exceeds 10000 rows")
    if not apollo:
        raise HTTPException(422, "The Apollo CSV has no rows.")
    names = ("website", "phone", "email")

    def fields(record):
        return {name: record.get("fields", {}).get(name, {}).get("value")
                if verified(record.get("fields", {}).get(name)) else None for name in names}

    base_fields = [fields(row) for row in records]
    pairs, used = [], set()
    # Build candidate sets first so duplicates on either side cannot match by order.
    by_key, by_domain = defaultdict(list), defaultdict(list)
    for i, record in enumerate(records):
        if record.get("licence_key"):
            by_key[record["licence_key"]].append(i)
        for domain in normal(base_fields[i]["website"], "website"):
            by_domain[domain].append(i)
    candidates = []
    for other in apollo:
        key = other.get("licence_key")
        domains = normal(other.get("website"), "website")
        indices = by_key.get(key, []) if key else sorted({i for domain in domains for i in by_domain.get(domain, [])})
        candidates.append(indices)
    occurrences = Counter(i for indices in candidates for i in indices)
    for j, indices in enumerate(candidates):
        if len(indices) == 1 and occurrences[indices[0]] == 1:
            i = indices[0]
            pairs.append((i, j))
            used.add(i)
    agreement = {name: {outcome: 0 for outcome in ("matches", "base_only", "apollo_only", "disagreements", "neither")}
                 for name in names}
    rows = []
    for i, j in pairs:
        outcomes = {}
        for name in names:
            left, right = normal(base_fields[i][name], name), normal(apollo[j].get(name), name)
            outcome = "neither" if not left and not right else "apollo_only" if not left else "base_only" if not right else "matches" if left == right else "disagreements"
            agreement[name][outcome] += 1
            outcomes[name] = outcome
        rows.append({"record_id": records[i]["record_id"], "name": records[i].get("name"),
                     "status": "matched", "summary": json.dumps(outcomes, sort_keys=True)})
    rows.extend({"record_id": record["record_id"], "name": record.get("name"), "status": "Base-only"}
                for i, record in enumerate(records) if i not in used)
    matched_apollo = {j for _, j in pairs}
    rows.extend({"name": row.get("company") or row.get("company_name") or row.get("website"), "status": "Apollo-only"}
                for j, row in enumerate(apollo) if j not in matched_apollo)
    counts = {"matched": len(pairs), "base_only": len(records) - len(pairs), "apollo_only": len(apollo) - len(pairs)}
    return {**counts, "field_agreement": agreement, "total": len(rows),
            "summary": json.dumps({**counts, "field_agreement": agreement}, sort_keys=True), "records": rows[:20]}


def build_router():
    from command import Actor, db, identity, require_tool
    router = APIRouter(prefix="/v1/command/base/compare", tags=["base"])

    @router.get("/apollo")
    async def apollo(args: Annotated[ApolloArgs, Depends()], actor: Annotated[Actor, Depends(identity)]):
        require_tool(actor, "base.compare.apollo")
        saved = await Store(db()).show(actor.tenant_id, args.list_id)
        async with db().acquire() as conn:
            upload = await conn.fetchrow("SELECT content FROM tank.command_attachments "
                "WHERE attachment_id=$1 AND tenant_id=$2 AND user_id=$3",
                args.attachment_id, actor.tenant_id, actor.user_id)
        if upload is None:
            raise HTTPException(404, "Not found")
        content = bytes(upload["content"])
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(413, "Attachment exceeds 5 MiB")
        try:
            result = compare(saved["records"], content.decode("utf-8-sig"))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise HTTPException(422, "Invalid Apollo CSV") from exc
        return {**result, "cost_usd": 0, "ledger_id": await receipt(actor.tenant_id, "base.compare.apollo")}

    return router
