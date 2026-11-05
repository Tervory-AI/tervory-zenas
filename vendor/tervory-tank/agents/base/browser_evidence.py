"""Upload Base browser spool files using only agent-base's existing R2 pair."""
from __future__ import annotations

import hashlib
import stat
from contextlib import ExitStack
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from chassis.storage import Storage, StorageError

LIMITS = {'.html': 2_400_000, '.jpg': 8 * 1024 * 1024, '.pdf': 10_000_000}


class EvidenceRequest(BaseModel):
    tenant_id: uuid.UUID
    task_id: uuid.UUID
    evidence_id: str = Field(pattern=r'^[a-f0-9]{64}$')


class EvidenceStorage(Storage):
    async def put_evidence(self, tenant: str, date: str, digest: str, suffix: str, data: bytes) -> str:
        # Reuse the chassis's tenant/region policy before using the founder's
        # evidence-specific prefix. This never permits ZA client exports.
        self._prefix(tenant, 'work', public=False)
        if self.owner != 'base' or suffix not in LIMITS or len(data) > LIMITS[suffix]:
            raise StorageError('Invalid browser evidence object')
        if re.fullmatch(r'[a-f0-9]{64}', digest) is None or re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) is None:
            raise StorageError('Invalid browser evidence key')
        key = f'evidence/{uuid.UUID(tenant)}/{date}/{digest}{suffix}'
        await self._request('PUT', key, data=data)
        return key


async def upload(body: EvidenceRequest, *, root: Path | None = None, store_factory=EvidenceStorage) -> dict:
    root = root or Path('/browser-evidence')
    # Hold directory descriptors so browser-controlled symlink/rename races
    # cannot redirect agent-base into its credentials or another tenant.
    with ExitStack() as opened:
        def directory_fd(path, *, parent=None):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            opened.callback(os.close, fd)
            return fd
        parent = directory_fd(root)
        for part in ('tenants', str(body.tenant_id), 'evidence'):
            parent = directory_fd(part, parent=parent)
        store = store_factory('base')
        try:
            pending = []
            date = datetime.now(timezone.utc).date().isoformat()
            for suffix, limit in LIMITS.items():
                name = body.evidence_id + suffix
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                except FileNotFoundError:
                    continue
                with os.fdopen(fd, 'rb') as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                        raise StorageError('Browser evidence exceeds limit or is not a file')
                    data = source.read(limit + 1)
                if len(data) > limit:
                    raise StorageError('Browser evidence exceeds limit')
                if suffix == '.html':
                    data = data.decode('utf-8', errors='replace')[:600000].encode('utf-8')
                digest = hashlib.sha256(data).hexdigest()
                pending.append((name, suffix, data, digest, info.st_ino))
            if not pending:
                raise StorageError('Browser evidence is no longer available locally')
            keys = []
            for _, suffix, data, digest, _ in pending:
                keys.append(await store.put_evidence(str(body.tenant_id), date, digest, suffix, data))
            # Retain every file if any upload failed. Retries address the same
            # content hashes; delete only after all uploads are acknowledged.
            for name, _, _, _, inode in pending:
                try:
                    if os.stat(name, dir_fd=parent, follow_symlinks=False).st_ino == inode:
                        os.unlink(name, dir_fd=parent)
                except FileNotFoundError:
                    pass
            return {'status': 'uploaded', 'bucket': store.bucket, 'keys': keys}
        finally:
            await store.close()


def register(app) -> None:
    @app.post('/brain/evidence')
    async def evidence(body: EvidenceRequest, x_tank_key: str = Header(default='')):
        from base_brain import Task, selected
        selected(Task(tenant_id=body.tenant_id, task_id=body.task_id), x_tank_key)
        try:
            return await upload(body)
        except Exception:
            raise HTTPException(503, 'Browser evidence upload failed; local evidence is retained within retention limits.') from None
