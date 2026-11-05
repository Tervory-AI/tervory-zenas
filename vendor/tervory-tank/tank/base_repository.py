"""PostgreSQL operations for Base's reference and tenant work. No worker changes."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from typing import Any
import uuid
from urllib.parse import urlsplit

from base_evidence import row_fields
from base_metrics import obj
from base_reference_status import ReferenceFailure, SAFE_DETAILS
from vendor_meter import PLATFORM

SEED_DATE = date(2026, 9, 1)
REFERENCE_URL = "https://www.atf.gov/firearms/listing-federal-firearms-licensees/complete"
ATF_ROW = """EXISTS (SELECT 1 FROM jsonb_object_keys(
    CASE WHEN jsonb_typeof(raw)='object' THEN raw ELSE '{}'::jsonb END) AS fields(name)
    WHERE regexp_replace(lower(btrim(name)), '[[:space:]-]+', '_', 'g')
        IN ('lic_regn','licence_number','license_number'))"""


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


class BaseStore:
    def __init__(self, runtime: Any) -> None:
        self.rt = runtime

    @staticmethod
    def reference_source() -> str:
        url = os.environ.get("BASE_REFERENCE_FILE_URL", "").strip()
        try:
            if not url or urlsplit(url).hostname in {"atf.gov", "www.atf.gov"}:
                return "atf"
        except ValueError:
            pass
        return hashlib.sha256(url.encode()).hexdigest()

    async def reference_state(self) -> dict[str, Any] | None:
        async with self.rt.pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tank.base_reference_checks WHERE owner_id=$1", PLATFORM)
        if row is None:
            return None
        sources = obj(row["source_checks"])
        # Existing rows describe ATF. No live-row repair or reset is needed.
        if not sources:
            return dict(row) if self.reference_source() == "atf" else None
        return sources.get(self.reference_source())

    @staticmethod
    def recent_reference_check(check: dict[str, Any]) -> bool:
        checked = check["checked_at"]
        if isinstance(checked, str):
            checked = datetime.fromisoformat(checked)
        return checked > datetime.now(timezone.utc) - timedelta(hours=24)

    async def engine(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.rt._http.post(self.rt.BASE_AGENT_URL.rstrip("/") + "/reference/ffl", json=payload,
            headers={"X-Tank-Key": os.environ.get("TANK_KEY_BASE", "")}, timeout=180, follow_redirects=False)
        if response.status_code != 200:
            reason = f"The Base reference service returned HTTP {response.status_code}."
            try:
                detail = response.json().get("detail", "")
            except (AttributeError, ValueError):
                detail = ""
            if isinstance(detail, str) and detail.startswith("file-blocked:"):
                # Reuse existing service failure wording for a non-ATF source.
                await self.reference_check("blocked", reason)
                raise ReferenceFailure(reason, blocked=True)
            if isinstance(detail, str) and detail.startswith("blocked:"):
                status = detail.removeprefix("blocked: HTTP ")
                reason += (f" ATF did not allow the read (HTTP {status})." if status in {"401", "402", "403", "429", "451"}
                           else " ATF did not allow the read.") + " Automatic checks have stopped."
                # Preserve the stop even when the founder invokes refresh
                # directly, outside the background check's exception handler.
                operation = "loaded" if "rows" in payload or payload.get("initial") else "updated"
                await self.reference_check("blocked", f"The reference could not be {operation}: {reason}")
                raise ReferenceFailure(reason, blocked=True)
            if isinstance(detail, str) and detail in SAFE_DETAILS:
                reason += " " + SAFE_DETAILS[detail]
            raise ReferenceFailure(reason)
        try:
            answer = response.json()
        except ValueError:
            raise ReferenceFailure("The Base reference service returned unreadable data (HTTP 200).") from None
        if not isinstance(answer, dict):
            raise ReferenceFailure("The Base reference service returned an unexpected response (HTTP 200).")
        return answer

    async def latest_file(self) -> dict[str, Any] | None:
        async with self.rt.pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tank.base_reference_files WHERE owner_id=$1 ORDER BY published_on DESC LIMIT 1", PLATFORM)
        return dict(row) if row else None

    async def adopt(self) -> None:
        if await self.latest_file():
            return
        async with self.rt.pool().acquire() as conn:
            rows = await conn.fetch(f"""SELECT id,raw,created_at,updated_at FROM base.records WHERE tenant_id=$1 AND {ATF_ROW}
                ORDER BY updated_at DESC,created_at DESC,id DESC""", PLATFORM)
        if not rows:
            await self.download_initial()
            return
        projected = {}
        for offset in range(0, len(rows), 2000):
            chunk = rows[offset:offset + 2000]
            converted = (await self.engine({"rows": [obj(row["raw"]) for row in chunk]})).get("rows")
            if not isinstance(converted, list) or len(converted) != len(chunk):
                raise ValueError("The reference projection did not preserve every licence.")
            for row, item in zip(chunk, converted):
                # Ordered newest first, globally across chunks. Keep pointers to
                # the original rows; never delete, rewrite or copy their data.
                projected.setdefault(item["licence_key"], {**item, "legacy_record_id": str(row["id"])})
        await self.install({"published_on": SEED_DATE.isoformat(), "source_url": REFERENCE_URL,
                            "checked_at": max(row["updated_at"] for row in rows).isoformat(),
                            "sha256": hashlib.sha256(encoded([(str(row["id"]), obj(row["raw"])) for row in rows]).encode()).hexdigest(),
                            "rows": list(projected.values()), "duplicate_count": len(rows) - len(projected)}, adopted=True)
        await self.reference_check("adopted")

    async def download_initial(self) -> None:
        check = await self.reference_state()
        if check and check["status"] == "blocked":
            return  # Durable for this source, including after restart.
        if self.reference_source() != "atf":
            if check and self.recent_reference_check(check):
                return
            # Record the new source's attempt before I/O, bounding failed or
            # interrupted empty-reference retries to the existing daily cadence.
            await self.reference_check("unavailable")
        file = await self.engine({"initial": True})
        await self.install(file, initial=True)
        await self.reference_check("updated")

    async def install(self, file: dict[str, Any], *, adopted: bool = False, initial: bool = False) -> dict[str, Any]:
        published = date.fromisoformat(file["published_on"])
        rows = file["rows"]
        if not rows or len({row["licence_key"] for row in rows}) != len(rows):
            raise ValueError("Reference licence identities must be unique and non-empty.")
        file_id = uuid.uuid4()
        async with self.rt.pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(4810511)")
                previous = await conn.fetchrow("SELECT * FROM tank.base_reference_files WHERE owner_id=$1 ORDER BY published_on DESC LIMIT 1", PLATFORM)
                if initial:
                    if previous:
                        return dict(previous)
                    if await conn.fetchval(f"SELECT 1 FROM base.records WHERE tenant_id=$1 AND {ATF_ROW} LIMIT 1", PLATFORM):
                        raise ReferenceFailure("Saved ATF licence rows became available during the download. They will be used on the next check.")
                if previous and previous["published_on"] >= published:
                    if previous["published_on"] == published and previous["sha256"] == file["sha256"]:
                        return dict(previous)
                    raise ValueError("A reference file cannot replace an existing dated snapshot.")
                await conn.execute("""INSERT INTO tank.base_reference_files
                    (id,owner_id,published_on,source_url,sha256,checked_at,adopted,row_count,duplicate_count)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)""", file_id, PLATFORM, published, file["source_url"], file["sha256"],
                    datetime.fromisoformat(file["checked_at"]), adopted, len(rows), file.get("duplicate_count", 0))
                await conn.executemany("""INSERT INTO tank.base_reference_entries
                    (file_id,licence_key,licence_number,state,licence_type,name,address,legacy_record_id,raw)
                    VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb)""",
                    [(file_id, row["licence_key"], row["licence_number"], row["state"], row["licence_type"], row["name"],
                      encoded(row["address"]), uuid.UUID(row["legacy_record_id"]) if adopted else None,
                      None if adopted else encoded(row["raw"])) for row in rows])
        return {"id": file_id, "owner_id": PLATFORM, "published_on": published, "row_count": len(rows),
                "source_url": file["source_url"], "checked_at": file["checked_at"], "sha256": file["sha256"], "adopted": adopted,
                "duplicate_count": file.get("duplicate_count", 0)}

    async def reference(self, file_id: uuid.UUID | None = None, *, inputs: bool = False,
                        states: list[str] | None = None, types: list[str] | None = None) -> list[dict[str, Any]]:
        if file_id is None:
            file = await self.latest_file()
            if not file:
                return []
            file_id = file["id"]
        async with self.rt.pool().acquire() as conn:
            rows = await conn.fetch("""SELECT e.file_id,e.licence_key,e.licence_number,e.state,e.licence_type,e.name,e.address,
                CASE WHEN $3 THEN coalesce(e.raw,b.raw) ELSE NULL END AS input,
                f.source_url,f.checked_at,f.published_on
                FROM tank.base_reference_entries e JOIN tank.base_reference_files f ON f.id=e.file_id AND f.owner_id=$1
                LEFT JOIN base.records b ON b.id=e.legacy_record_id AND b.tenant_id=$1
                WHERE e.file_id=$2 AND (cardinality($4::text[])=0 OR e.state=ANY($4))
                AND (cardinality($5::text[])=0 OR e.licence_type=ANY($5))
                ORDER BY e.state,e.licence_type,e.licence_key""", PLATFORM, file_id, inputs, states or [], types or [])
        return [{**dict(row), "raw": obj(row["input"]) if inputs else None, "address": obj(row["address"])} for row in rows]

    async def reference_history(self) -> list[dict[str, Any]]:
        async with self.rt.pool().acquire() as conn:
            return [dict(row) for row in await conn.fetch("SELECT * FROM tank.base_reference_files WHERE owner_id=$1 ORDER BY published_on DESC", PLATFORM)]

    async def reference_check(self, status: str, message: str | None = None) -> None:
        async with self.rt.pool().acquire() as conn:
            await conn.execute("""INSERT INTO tank.base_reference_checks(owner_id,checked_at,status,message,source_checks)
                VALUES($1,now(),$2,$3,jsonb_build_object($4::text,
                    jsonb_build_object('status',$2::text,'message',$3::text,'checked_at',now())))
                ON CONFLICT(owner_id) DO UPDATE SET checked_at=now(),status=$2,message=$3,
                    source_checks=(CASE WHEN tank.base_reference_checks.source_checks='{}'::jsonb THEN
                        jsonb_build_object('atf',jsonb_build_object('status',tank.base_reference_checks.status,
                            'message',tank.base_reference_checks.message,'checked_at',tank.base_reference_checks.checked_at))
                        ELSE tank.base_reference_checks.source_checks END) || EXCLUDED.source_checks
                WHERE coalesce(tank.base_reference_checks.source_checks->$4->>'status',
                    CASE WHEN tank.base_reference_checks.source_checks='{}'::jsonb AND $4='atf'
                         THEN tank.base_reference_checks.status END,'')<>'blocked' OR EXCLUDED.status='blocked'""",
                PLATFORM, status, message, self.reference_source())

    async def due_refresh(self) -> bool:
        check = await self.reference_state()
        return not (check and (check["status"] == "blocked" or
                    (check["status"] != "adopted" and self.recent_reference_check(check))))

    async def refresh(self) -> dict[str, Any]:
        check = await self.reference_state()
        if check and check["status"] == "blocked":
            raise ValueError("ATF blocked this site's public read; automatic reads have stopped.")
        current = await self.latest_file()
        if current is None:
            await self.adopt()
            current = await self.latest_file()
            if current and not current["adopted"]:
                return {"changed": True, "file": current}
        if current is None:
            raise ValueError("The existing platform reference has not been loaded on this server.")
        answer = await self.engine({"since": str(current["published_on"])})
        if answer.get("changed"):
            for file in answer.get("files") or [answer]:
                await self.install(file)
        await self.reference_check("updated" if answer.get("changed") else "current")
        return {"changed": bool(answer.get("changed")), "file": await self.latest_file()}

    async def tenants(self) -> list[uuid.UUID]:
        async with self.rt.pool().acquire() as conn:
            # Service discovery returns identities only. Every data read below
            # is then scoped to one tenant, including training and ledger joins.
            rows = await conn.fetch("SELECT DISTINCT tenant_id FROM agent_base.records UNION SELECT DISTINCT tenant_id FROM tank.base_campaigns")
        return [row["tenant_id"] for row in rows]

    async def collect(self, tenant: uuid.UUID) -> int:
        import training
        count = 0
        async with self.rt.pool().acquire() as conn:
            rows = await conn.fetch("""SELECT r.* FROM agent_base.records r
                LEFT JOIN tank.base_record_results p ON p.tenant_id=$1 AND p.record_id=r.id
                WHERE r.tenant_id=$1 AND (r.status IN ('done','failed') OR EXISTS(
                    SELECT 1 FROM tank.base_ledger_links b WHERE b.tenant_id=$1 AND b.record_id=r.id))
                  AND (p.record_id IS NULL OR r.updated_at>p.checked_at)
                ORDER BY r.updated_at,r.id LIMIT 1000""", tenant)
            for saved in rows:
                row = {**dict(saved), **{key: obj(saved[key]) for key in ("raw", "profile", "passes")}}
                fields = row_fields(row)
                reference = row["raw"].get("_base_reference") or {}
                state = str(reference.get("state") or row["raw"].get("state") or row["profile"].get("state", {}).get("value") or "unspecified").upper()
                kind = str(reference.get("licence_type") or "unspecified")
                name = str(row["profile"].get("name", {}).get("value") or row["raw"].get("name") or reference.get("name") or "Unnamed business")
                revision = hashlib.sha256(encoded({"fields": fields, "status": row["status"], "updated": row["updated_at"]}).encode()).hexdigest()
                async with conn.transaction():
                    for field, answer in fields.items():
                        if row["status"] not in {"done", "failed"}:
                            continue
                        await training.save_pair(conn, tenant=tenant, agent="base", task="base." + field,
                            value={"record_id": str(row["id"]), "name": name, "field": field}, output=answer,
                            approved_by="automatic:base.verify" if answer["verified"] else "automatic:observed_failure",
                            source="base_verified", ref=f"{row['id']}:{revision}:{field}", gold_id=None, holdout=False,
                            client_matter=True, public=False, region=self.rt.box_region(), batch_id=str(row["task_id"]) if row["task_id"] else None)
                    await conn.execute("""INSERT INTO tank.base_record_results
                        (tenant_id,record_id,task_id,batch,licence_key,state,licence_type,name,status,fields,source_revision,attempted_at,checked_at)
                        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13)
                        ON CONFLICT(tenant_id,record_id) DO UPDATE SET task_id=$3,batch=$4,licence_key=$5,state=$6,
                        licence_type=$7,name=$8,status=$9,fields=$10::jsonb,source_revision=$11,attempted_at=$12,checked_at=$13
                        WHERE tank.base_record_results.checked_at <= EXCLUDED.checked_at""",
                        tenant, row["id"], row["task_id"], row["batch"], reference.get("licence_key"), state, kind,
                        name, row["status"], encoded(fields), revision, row["created_at"], row["updated_at"])
                count += 1
        return count

    async def results(self, tenant: uuid.UUID) -> list[dict[str, Any]]:
        async with self.rt.pool().acquire() as conn:
            rows = await conn.fetch("SELECT * FROM tank.base_record_results WHERE tenant_id=$1 ORDER BY checked_at,record_id", tenant)
        return [{**dict(row), "fields": obj(row["fields"])} for row in rows]

    async def ledger(self, tenant: uuid.UUID) -> list[dict[str, Any]]:
        async with self.rt.pool().acquire() as conn:
            rows = await conn.fetch("""SELECT l.*,b.record_id FROM tank.ledger l
                LEFT JOIN tank.base_ledger_links b ON b.tenant_id=$1 AND b.ledger_id=l.id
                WHERE l.tenant_id=$1 AND l.agent='base' ORDER BY l.ts,l.id""", tenant)
        return [dict(row) for row in rows]

    async def campaigns(self, tenant: uuid.UUID) -> list[dict[str, Any]]:
        async with self.rt.pool().acquire() as conn:
            rows = await conn.fetch("""SELECT c.*,s.state,s.ordinal,s.batch,s.task_id,s.status,s.records_count,s.started_at,s.finished_at,s.error
                FROM tank.base_campaigns c JOIN tank.base_campaign_states s ON s.tenant_id=$1 AND s.campaign_id=c.id
                WHERE c.tenant_id=$1 ORDER BY c.created_at DESC,s.ordinal""", tenant)
        return [dict(row) for row in rows]

    async def create_campaign(self, tenant: uuid.UUID, request_id: uuid.UUID, name: str, created_by: str,
                              file_id: uuid.UUID | None, selection: dict[str, Any], groups: dict[str, list[dict[str, Any]]]) -> uuid.UUID:
        identity = uuid.uuid4()
        async with self.rt.pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"base-start:{tenant}:{request_id}")
                old = await conn.fetchval("SELECT id FROM tank.base_campaigns WHERE tenant_id=$1 AND request_id=$2", tenant, request_id)
                if old:
                    return old
                await conn.execute("""INSERT INTO tank.base_campaigns(tenant_id,id,request_id,name,file_id,selection,created_by)
                    VALUES($1,$2,$3,$4,$5,$6::jsonb,$7)""", tenant, identity, request_id, name, file_id, encoded(selection), created_by)
                for ordinal, state in enumerate(sorted(groups, key=lambda name: (len(groups[name]), name))):
                    batch = f"{name[:50]} · {state} · {identity}"
                    await conn.execute("""INSERT INTO tank.base_campaign_states
                        (tenant_id,campaign_id,state,ordinal,batch,records_count,csv_records)
                        VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)""", tenant, identity, state, ordinal, batch, len(groups[state]),
                        None if file_id else encoded(groups[state]))
        return identity

    async def apollo(self, tenant: uuid.UUID) -> list[dict[str, Any]]:
        async with self.rt.pool().acquire() as conn:
            rows = await conn.fetch("""SELECT DISTINCT ON(r.licence_key) r.* FROM tank.base_apollo_records r
                JOIN tank.base_apollo_imports i ON i.tenant_id=$1 AND i.id=r.import_id
                WHERE r.tenant_id=$1 ORDER BY r.licence_key,i.imported_at DESC,i.id DESC""", tenant)
        return [{**dict(row), "fields": obj(row["fields"])} for row in rows]

    async def save_apollo(self, tenant: uuid.UUID, digest: str, rows: list[dict[str, Any]]) -> uuid.UUID:
        identity = uuid.uuid4()
        async with self.rt.pool().acquire() as conn:
            async with conn.transaction():
                value = await conn.fetchval("""INSERT INTO tank.base_apollo_imports(tenant_id,id,sha256) VALUES($1,$2,$3)
                    ON CONFLICT(tenant_id,sha256) DO UPDATE SET sha256=EXCLUDED.sha256 RETURNING id""", tenant, identity, digest)
                await conn.executemany("""INSERT INTO tank.base_apollo_records(tenant_id,import_id,licence_key,fields,base_snapshot)
                    VALUES($1,$2,$3,$4::jsonb,$5::jsonb) ON CONFLICT DO NOTHING""",
                    [(tenant, value, row["licence_key"], encoded(row["fields"]), encoded(row["base_snapshot"])) for row in rows])
        return value

    async def dispatch(self, tenant: uuid.UUID) -> None:
        """Advance state submissions; the existing room remains the sole worker."""
        async with self.rt.pool().acquire() as conn:
            async with conn.transaction():
                locked = await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended($1,0))", f"base-dispatch:{tenant}")
                if not locked:
                    return
                plans = await conn.fetch("""SELECT s.*,c.file_id,c.selection,c.created_by,t.status AS task_status
                    FROM tank.base_campaign_states s JOIN tank.base_campaigns c ON c.tenant_id=$1 AND c.id=s.campaign_id
                    LEFT JOIN agent_base.tasks t ON t.tenant_id=$1 AND t.id=s.task_id
                    WHERE s.tenant_id=$1 ORDER BY c.created_at,s.ordinal""", tenant)
                stopped: set[uuid.UUID] = set()
                for saved in plans:
                    row = dict(saved)
                    campaign = row["campaign_id"]
                    if campaign in stopped or row["status"] in {"done", "failed"}:
                        continue
                    if row["task_id"]:
                        status = row["task_status"]
                        if status in {"done", "failed"}:
                            await conn.execute("""UPDATE tank.base_campaign_states SET status=$4,finished_at=now()
                                WHERE tenant_id=$1 AND campaign_id=$2 AND state=$3""", tenant, campaign, row["state"], status)
                        else:
                            stopped.add(campaign)
                        continue
                    existing = await conn.fetchrow("""SELECT id,status FROM agent_base.tasks
                        WHERE tenant_id=$1 AND input->>'batch'=$2 ORDER BY created_at LIMIT 1""", tenant, row["batch"])
                    if existing:
                        await conn.execute("""UPDATE tank.base_campaign_states SET task_id=$4,status='running',error=NULL,started_at=coalesce(started_at,now())
                            WHERE tenant_id=$1 AND campaign_id=$2 AND state=$3""", tenant, campaign, row["state"], existing["id"])
                        stopped.add(campaign)
                        continue
                    if row["status"] != "waiting":
                        # An uncertain POST is reconciled by its unique batch
                        # label. It is never blindly submitted a second time.
                        stopped.add(campaign)
                        continue
                    records = await self.state_inputs(row)
                    await conn.execute("""UPDATE tank.base_campaign_states SET status='submitting',started_at=now()
                        WHERE tenant_id=$1 AND campaign_id=$2 AND state=$3""", tenant, campaign, row["state"])
                    stopped.add(campaign)
                    # Commit the intent before the network call. The unique
                    # label and recovery read make restarts reviewable.
                    pending = row
                    break
                else:
                    return
        try:
            response = await self.rt._http.post(self.rt.BASE_AGENT_URL.rstrip("/") + "/v1/base/run", json={
                "tenant_id": str(tenant), "created_by": pending["created_by"], "batch": pending["batch"],
                "records": records, "engine": "ffl" if pending["file_id"] else None, "preview": False, "split_by_state": False,
            }, timeout=180, follow_redirects=False)
            if response.status_code not in {200, 202}:
                raise ValueError("Base did not confirm the batch.")
            task = uuid.UUID(response.json()["task"]["id"])
        except Exception:
            async with self.rt.pool().acquire() as conn:
                await conn.execute("""UPDATE tank.base_campaign_states SET status='submission_unknown',error='Submission not confirmed; checking the existing batch before any retry.'
                    WHERE tenant_id=$1 AND campaign_id=$2 AND state=$3""", tenant, pending["campaign_id"], pending["state"])
            return
        async with self.rt.pool().acquire() as conn:
            await conn.execute("""UPDATE tank.base_campaign_states SET task_id=$4,status='running'
                WHERE tenant_id=$1 AND campaign_id=$2 AND state=$3""", tenant, pending["campaign_id"], pending["state"], task)

    async def state_inputs(self, pending: dict[str, Any]) -> list[dict[str, Any]]:
        if not pending["file_id"]:
            return obj(pending["csv_records"])
        choice = obj(pending["selection"])
        reference = await self.reference(pending["file_id"], inputs=True, states=[pending["state"]], types=choice.get("types"))
        records = []
        for entry in reference:
            if entry["licence_type"] == "03" and not choice.get("include_collectors"):
                continue
            meta = {key: entry[key] for key in ("licence_key", "licence_number", "state", "licence_type", "name", "source_url")}
            meta.update(published_on=str(entry["published_on"]), checked_at=str(entry["checked_at"]))
            records.append({**entry["raw"], "_base_reference": meta})
        return records
