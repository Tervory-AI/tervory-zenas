"""Ownership records for Moot Mux media. Memory for tests; Postgres for Tank."""

from __future__ import annotations

import json
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4


class ReservationError(Exception):
    pass


@dataclass
class UploadReservation:
    request_id: str
    tenant_id: str
    user_id: str
    hearing_id: str
    idempotency_key: str
    input_hash: str
    created_at: float
    upload_id: str | None = None


def repeated(reservation: UploadReservation, input_hash: str) -> UploadReservation:
    if reservation.input_hash != input_hash:
        raise ReservationError("idempotency_conflict")
    if not reservation.upload_id:
        # A crash or timeout can leave a provider write uncertain. Never retry it.
        raise ReservationError("upload_pending_or_uncertain")
    return reservation


@dataclass
class MediaRecord:
    tenant_id: str
    user_id: str
    hearing_id: str
    upload_id: str
    asset_id: str | None = None
    playback_ids: list[str] = field(default_factory=list)
    passthrough: str = ""
    idempotency_key: str | None = None


class AsyncOwnershipStore(Protocol):
    async def reserve_upload(
        self,
        *,
        tenant_id: str,
        user_id: str,
        hearing_id: str,
        idempotency_key: str,
        input_hash: str,
        rate: int,
        daily: int,
    ) -> UploadReservation: ...

    async def complete_upload(
        self, reservation: UploadReservation, record: MediaRecord
    ) -> None: ...

    async def save(self, record: MediaRecord) -> None: ...

    async def by_upload(
        self, upload_id: str, *, tenant_id: str
    ) -> MediaRecord | None: ...

    async def by_asset(
        self, asset_id: str, *, tenant_id: str
    ) -> MediaRecord | None: ...

    async def by_playback(
        self, playback_id: str, *, tenant_id: str
    ) -> MediaRecord | None: ...

    async def by_idempotency(
        self, tenant_id: str, user_id: str, hearing_id: str, idempotency_key: str
    ) -> MediaRecord | None: ...

    async def bind_asset(
        self, upload_id: str, asset_id: str, playback_ids: list[str], *, tenant_id: str
    ) -> MediaRecord | None: ...

    async def count_uploads_since(
        self, tenant_id: str, user_id: str, since_epoch: float
    ) -> int: ...


class MemoryOwnershipStore:
    def __init__(self) -> None:
        self._by_upload: dict[str, MediaRecord] = {}
        self._by_asset: dict[str, MediaRecord] = {}
        self._by_playback: dict[str, MediaRecord] = {}
        self._by_idem: dict[tuple[str, str, str, str], MediaRecord] = {}
        self._created_at: dict[str, float] = {}
        self._reservations: dict[tuple[str, str, str, str], UploadReservation] = {}
        self._reservation_lock = asyncio.Lock()

    async def reserve_upload(
        self,
        *,
        tenant_id: str,
        user_id: str,
        hearing_id: str,
        idempotency_key: str,
        input_hash: str,
        rate: int,
        daily: int,
    ) -> UploadReservation:
        async with self._reservation_lock:
            key = (tenant_id, user_id, hearing_id, idempotency_key)
            if key in self._reservations:
                return repeated(self._reservations[key], input_hash)
            if key in self._by_idem:
                raise ReservationError("upload_state_unavailable")
            now = time.time()
            owned = [
                r.created_at
                for r in self._reservations.values()
                if r.tenant_id == tenant_id and r.user_id == user_id
            ]
            completed = {
                r.upload_id for r in self._reservations.values() if r.upload_id
            }
            owned += [
                self._created_at[i]
                for i, r in self._by_upload.items()
                if r.tenant_id == tenant_id
                and r.user_id == user_id
                and i not in completed
            ]
            if sum(t >= now - 60 for t in owned) >= rate:
                raise ReservationError("rate_limited")
            if sum(t >= now - 86400 for t in owned) >= daily:
                raise ReservationError("upload_budget_exhausted")
            result = UploadReservation(str(uuid4()), *key, input_hash, now)
            self._reservations[key] = result
            return result

    async def complete_upload(
        self, reservation: UploadReservation, record: MediaRecord
    ) -> None:
        async with self._reservation_lock:
            if (
                record.tenant_id,
                record.user_id,
                record.hearing_id,
                record.idempotency_key,
            ) != (
                reservation.tenant_id,
                reservation.user_id,
                reservation.hearing_id,
                reservation.idempotency_key,
            ):
                raise ReservationError("upload_owner_mismatch")
            previous = self._by_upload.get(record.upload_id)
            if previous is not None and (
                previous.tenant_id,
                previous.user_id,
                previous.hearing_id,
                previous.idempotency_key,
            ) != (
                record.tenant_id,
                record.user_id,
                record.hearing_id,
                record.idempotency_key,
            ):
                raise ReservationError("upload_owner_mismatch")
            await self.save(record)
            reservation.upload_id = record.upload_id

    async def save(self, record: MediaRecord) -> None:
        import time

        self._by_upload[record.upload_id] = record
        self._created_at.setdefault(record.upload_id, time.time())
        if record.asset_id:
            self._by_asset[record.asset_id] = record
        for pid in record.playback_ids:
            self._by_playback[pid] = record
        if record.idempotency_key:
            self._by_idem[
                (
                    record.tenant_id,
                    record.user_id,
                    record.hearing_id,
                    record.idempotency_key,
                )
            ] = record

    async def by_upload(self, upload_id: str, *, tenant_id: str) -> MediaRecord | None:
        row = self._by_upload.get(upload_id)
        return row if row is not None and row.tenant_id == tenant_id else None

    async def by_asset(self, asset_id: str, *, tenant_id: str) -> MediaRecord | None:
        row = self._by_asset.get(asset_id)
        return row if row is not None and row.tenant_id == tenant_id else None

    async def by_playback(
        self, playback_id: str, *, tenant_id: str
    ) -> MediaRecord | None:
        row = self._by_playback.get(playback_id)
        return row if row is not None and row.tenant_id == tenant_id else None

    async def by_idempotency(
        self, tenant_id: str, user_id: str, hearing_id: str, idempotency_key: str
    ) -> MediaRecord | None:
        return self._by_idem.get((tenant_id, user_id, hearing_id, idempotency_key))

    async def bind_asset(
        self, upload_id: str, asset_id: str, playback_ids: list[str], *, tenant_id: str
    ) -> MediaRecord | None:
        row = self._by_upload.get(upload_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        row.asset_id = asset_id
        row.playback_ids = list(playback_ids)
        self._by_asset[asset_id] = row
        for pid in playback_ids:
            self._by_playback[pid] = row
        return row

    async def count_uploads_since(
        self, tenant_id: str, user_id: str, since_epoch: float
    ) -> int:
        total = 0
        for upload_id, row in self._by_upload.items():
            if (
                row.tenant_id == tenant_id
                and row.user_id == user_id
                and self._created_at.get(upload_id, 0) >= since_epoch
            ):
                total += 1
        return total


def _row_to_record(row: Any) -> MediaRecord | None:
    if row is None:
        return None
    playback = row["playback_ids"]
    if isinstance(playback, str):
        playback = json.loads(playback)
    return MediaRecord(
        tenant_id=str(row["tenant_id"]),
        user_id=row["user_id"],
        hearing_id=str(row["hearing_id"]),
        upload_id=row["upload_id"],
        asset_id=row["asset_id"],
        playback_ids=list(playback or []),
        passthrough=row["passthrough"] or "",
        idempotency_key=row["idempotency_key"]
        if "idempotency_key" in row.keys()
        else None,
    )


class PostgresOwnershipStore:
    def __init__(self, pool_factory):
        self._pool = pool_factory

    async def reserve_upload(
        self,
        *,
        tenant_id: str,
        user_id: str,
        hearing_id: str,
        idempotency_key: str,
        input_hash: str,
        rate: int,
        daily: int,
    ) -> UploadReservation:
        async with self._pool().acquire() as conn, conn.transaction():
            # This shared transaction lock covers the quota and request claim
            # across all Tank workers, before any provider request is made.
            scope = json.dumps(["moot-upload", tenant_id, user_id])
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", scope
            )
            row = await conn.fetchrow(
                """SELECT * FROM tank.moot_upload_requests
                WHERE tenant_id=$1::uuid AND user_id=$2 AND hearing_id=$3::uuid
                  AND idempotency_key=$4""",
                tenant_id,
                user_id,
                hearing_id,
                idempotency_key,
            )
            if row:
                return repeated(
                    UploadReservation(
                        str(row["request_id"]),
                        tenant_id,
                        user_id,
                        hearing_id,
                        idempotency_key,
                        row["input_hash"],
                        row["created_at"].timestamp(),
                        row["upload_id"],
                    ),
                    input_hash,
                )
            if await conn.fetchval(
                """SELECT 1 FROM tank.moot_media WHERE tenant_id=$1::uuid
                AND user_id=$2 AND hearing_id=$3::uuid AND idempotency_key=$4""",
                tenant_id,
                user_id,
                hearing_id,
                idempotency_key,
            ):
                raise ReservationError("upload_state_unavailable")
            counts = await conn.fetchrow(
                """WITH attempts AS (
                SELECT created_at FROM tank.moot_upload_requests WHERE tenant_id=$1::uuid AND user_id=$2
                UNION ALL
                SELECT m.created_at FROM tank.moot_media m WHERE m.tenant_id=$1::uuid AND m.user_id=$2
                  AND NOT EXISTS (SELECT 1 FROM tank.moot_upload_requests r
                    WHERE r.tenant_id=$1::uuid AND r.user_id=$2 AND r.upload_id=m.upload_id)
                ) SELECT count(*) FILTER (WHERE created_at>=now()-interval '1 minute') AS recent,
                    count(*) FILTER (WHERE created_at>=now()-interval '1 day') AS daily FROM attempts""",
                tenant_id,
                user_id,
            )
            if counts["recent"] >= rate:
                raise ReservationError("rate_limited")
            if counts["daily"] >= daily:
                raise ReservationError("upload_budget_exhausted")
            request_id = str(uuid4())
            created = await conn.fetchval(
                """INSERT INTO tank.moot_upload_requests
                (request_id,tenant_id,user_id,hearing_id,idempotency_key,input_hash)
                VALUES($1::uuid,$2::uuid,$3,$4::uuid,$5,$6) RETURNING created_at""",
                request_id,
                tenant_id,
                user_id,
                hearing_id,
                idempotency_key,
                input_hash,
            )
            return UploadReservation(
                request_id,
                tenant_id,
                user_id,
                hearing_id,
                idempotency_key,
                input_hash,
                created.timestamp(),
            )

    async def complete_upload(
        self, reservation: UploadReservation, record: MediaRecord
    ) -> None:
        if (
            record.tenant_id,
            record.user_id,
            record.hearing_id,
            record.idempotency_key,
        ) != (
            reservation.tenant_id,
            reservation.user_id,
            reservation.hearing_id,
            reservation.idempotency_key,
        ):
            raise ReservationError("upload_owner_mismatch")
        async with self._pool().acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """SELECT upload_id FROM tank.moot_upload_requests
                WHERE request_id=$1::uuid AND tenant_id=$2::uuid AND user_id=$3
                AND hearing_id=$4::uuid AND idempotency_key=$5 FOR UPDATE""",
                reservation.request_id,
                reservation.tenant_id,
                reservation.user_id,
                reservation.hearing_id,
                reservation.idempotency_key,
            )
            if row is None or row["upload_id"] not in (None, record.upload_id):
                raise ReservationError("upload_state_unavailable")
            if row["upload_id"] == record.upload_id:
                return
            await conn.execute(
                """INSERT INTO tank.moot_media
                (upload_id,tenant_id,user_id,hearing_id,passthrough,idempotency_key)
                VALUES($1,$2::uuid,$3,$4::uuid,$5,$6)""",
                record.upload_id,
                record.tenant_id,
                record.user_id,
                record.hearing_id,
                record.passthrough,
                record.idempotency_key,
            )
            await conn.execute(
                """UPDATE tank.moot_upload_requests SET upload_id=$4
                WHERE request_id=$1::uuid AND tenant_id=$2::uuid AND user_id=$3""",
                reservation.request_id,
                reservation.tenant_id,
                reservation.user_id,
                record.upload_id,
            )

    async def save(self, record: MediaRecord) -> None:
        async with self._pool().acquire() as conn:
            await conn.execute(
                """INSERT INTO tank.moot_media
                    (upload_id, tenant_id, user_id, hearing_id, asset_id, playback_ids, passthrough, idempotency_key)
                   VALUES ($1,$2::uuid,$3,$4::uuid,$5,$6::jsonb,$7,$8)
                   ON CONFLICT (upload_id) DO UPDATE SET
                     asset_id=COALESCE(EXCLUDED.asset_id, tank.moot_media.asset_id),
                     playback_ids=EXCLUDED.playback_ids,
                     updated_at=now()""",
                record.upload_id,
                record.tenant_id,
                record.user_id,
                record.hearing_id,
                record.asset_id,
                json.dumps(record.playback_ids),
                record.passthrough,
                record.idempotency_key,
            )

    async def by_upload(self, upload_id: str, *, tenant_id: str) -> MediaRecord | None:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tank.moot_media WHERE upload_id=$1 AND tenant_id=$2::uuid",
                upload_id,
                tenant_id,
            )
        return _row_to_record(row)

    async def by_asset(self, asset_id: str, *, tenant_id: str) -> MediaRecord | None:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tank.moot_media WHERE asset_id=$1 AND tenant_id=$2::uuid",
                asset_id,
                tenant_id,
            )
        return _row_to_record(row)

    async def by_playback(
        self, playback_id: str, *, tenant_id: str
    ) -> MediaRecord | None:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tank.moot_media WHERE playback_ids ? $1 AND tenant_id=$2::uuid",
                playback_id,
                tenant_id,
            )
        return _row_to_record(row)

    async def by_idempotency(
        self, tenant_id: str, user_id: str, hearing_id: str, idempotency_key: str
    ) -> MediaRecord | None:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM tank.moot_media
                   WHERE tenant_id=$1::uuid AND user_id=$2 AND hearing_id=$3::uuid
                     AND idempotency_key=$4""",
                tenant_id,
                user_id,
                hearing_id,
                idempotency_key,
            )
        return _row_to_record(row)

    async def bind_asset(
        self, upload_id: str, asset_id: str, playback_ids: list[str], *, tenant_id: str
    ) -> MediaRecord | None:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE tank.moot_media SET asset_id=$2, playback_ids=$3::jsonb, updated_at=now()
                   WHERE upload_id=$1 AND tenant_id=$4::uuid RETURNING *""",
                upload_id,
                asset_id,
                json.dumps(playback_ids),
                tenant_id,
            )
        return _row_to_record(row)

    async def count_uploads_since(
        self, tenant_id: str, user_id: str, since_epoch: float
    ) -> int:
        async with self._pool().acquire() as conn:
            return int(
                await conn.fetchval(
                    """SELECT count(*) FROM tank.moot_media
                   WHERE tenant_id=$1::uuid AND user_id=$2
                     AND created_at >= to_timestamp($3)""",
                    tenant_id,
                    user_id,
                    since_epoch,
                )
                or 0
            )
