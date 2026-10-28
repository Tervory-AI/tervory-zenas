"""Encrypt Docassemble's previous daily snapshot with the existing age/R2 worker.

The official image creates MM-DD snapshots. Yesterday's snapshot is no longer
being written by today's cron job. No live database or plaintext network copy.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

from backup import recipient_value, tool_environment
from r2_archive import PLATFORM_TENANT, Storage, StorageError, archive_file


def snapshot(root: Path, now: datetime) -> Path:
    day = (now - timedelta(days=1)).date()
    source = root / day.strftime("%m-%d")
    if source.is_symlink() or not source.is_dir():
        raise StorageError("Docassemble daily snapshot is unavailable")
    # Reject stale same-calendar-day directories from a previous year.
    made = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).date()
    if made != day:
        raise StorageError("Docassemble daily snapshot is unavailable")
    for relative in ("config/config.yml", "postgres/docassemble"):
        item = source / relative
        if item.is_symlink() or not item.is_file() or not item.stat().st_size:
            raise StorageError("Docassemble daily snapshot is incomplete")
    if not (source / "files").is_dir():
        raise StorageError("Docassemble daily snapshot is incomplete")
    return source


def configuration():
    region = os.environ.get("TANK_REGION")
    if region not in {"us-east", "za"}:
        raise StorageError("Unknown storage region")
    recipient = recipient_value()
    if not shutil.which("age") or not shutil.which("tar"):
        raise StorageError("Backup requires tar and age on this cloud worker")
    return region, recipient


async def encrypted_snapshot(source: Path, target: Path, recipient: str):
    read_fd, write_fd = os.pipe()
    children = []
    try:
        with target.open("wb") as output:
            encrypt = await asyncio.create_subprocess_exec(
                "age", "--encrypt", "--recipient", recipient, stdin=read_fd, stdout=output,
                stderr=asyncio.subprocess.DEVNULL, env=tool_environment())
            children.append(encrypt)
            os.close(read_fd)
            read_fd = -1
            archive = await asyncio.create_subprocess_exec(
                "tar", "-C", str(source), "-czf", "-", ".", stdout=write_fd,
                stderr=asyncio.subprocess.DEVNULL, env=tool_environment())
            children.append(archive)
            os.close(write_fd)
            write_fd = -1
            codes = await asyncio.wait_for(asyncio.gather(*(child.wait() for child in children)), 3600)
            if any(codes):
                raise StorageError("Archive or encryption failed; no backup was uploaded")
        if not target.stat().st_size:
            raise StorageError("Encrypted backup is empty")
    finally:
        for fd in (read_fd, write_fd):
            if fd >= 0:
                os.close(fd)
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()


async def backup_once(root=Path("/backup"), now=None):
    region, recipient = configuration()
    store = Storage("zenas")
    # Same encrypted platform-backup destination as the existing ZA worker.
    # `public` is the storage gate's platform flag, not a bucket/object ACL.
    public = region == "za"
    try:
        store._prefix(PLATFORM_TENANT, "backups", public=public)
        source = snapshot(root, now or datetime.now(timezone.utc))
        with tempfile.TemporaryDirectory(prefix="docassemble-encrypted-") as directory:
            target = Path(directory) / "docassemble.tar.age"
            await encrypted_snapshot(source, target, recipient)
            return await archive_file(store, target, "backups", tenant_id=PLATFORM_TENANT,
                                      public=public, label="docassemble.tar.age")
    finally:
        await store.close()


async def main(nightly):
    configuration()
    # Validate the bucket/credential owner before waiting for the first schedule.
    store = Storage("zenas")
    await store.close()
    while True:
        if nightly:
            now = datetime.now(timezone.utc)
            when = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if when <= now:
                when += timedelta(days=1)
            await asyncio.sleep((when - now).total_seconds())
        result = await backup_once()
        print(json.dumps({"kind": "docassemble", "bucket": result["bucket"],
                          "key": result["key"], "size_bytes": result["size_bytes"]}), flush=True)
        if not nightly:
            return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--nightly", action="store_true")
    try:
        asyncio.run(main(parser.parse_args().nightly))
    except Exception:
        raise SystemExit("Encrypted backup failed; no complete backup was confirmed. Check worker configuration.") from None
