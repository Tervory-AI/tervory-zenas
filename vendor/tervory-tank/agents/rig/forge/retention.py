"""Discard bulky intermediates; retain small idempotency records and owed receipts."""
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

KEEP = {"request.json", "state.json", "report.json", "pending-receipt.json", "pending-gpu-relay.json"}
PENDING = {"pending-receipt.json", "pending-gpu-relay.json"}


def save(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def cleanup(work: Path):
    for path in work.iterdir():
        if path.name in KEEP and not path.is_symlink():
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def sweep(root: Path, active: set, *, now=None):
    now = time.time() if now is None else now
    retention = int(os.environ.get("FORGE_RETENTION_SECONDS", "604800"))
    if not 3600 <= retention <= 2_592_000:
        raise ValueError("FORGE_RETENTION_SECONDS must be one hour to thirty days.")
    if root.is_symlink():
        raise ValueError("Invalid job storage.")
    for work in root.glob("*/*"):
        if work.is_symlink() or work.parent.is_symlink() or not work.is_dir():
            continue
        try:
            identity = (uuid.UUID(work.parent.name), uuid.UUID(work.name))
        except ValueError:
            continue
        if identity in active or not (work / "request.json").is_file():
            continue
        try:
            path = work / "state.json"
            state = json.loads(path.read_text()) if path.exists() else {}
            if state.get("status") not in {"draft", "failed"}:
                state.update(tenant_id=str(identity[0]), job_id=str(identity[1]), status="failed",
                             error="Forge restarted. Inspect existing receipts; this job is not relaunched.")
            state.setdefault("finished_at", path.stat().st_mtime if path.exists() else now)
            cleanup(work)
            if now - state["finished_at"] >= retention and not any((work / name).exists() for name in PENDING):
                # Never remove request identity: retrying an expired job must not
                # repeat expensive work. Tank/R2 hold the durable receipts/assets.
                state.pop("report", None)
                state.pop("receipts", None)
                state["diagnostics_expired"] = True
                (work / "report.json").unlink(missing_ok=True)
            save(path, state)
        except (OSError, ValueError, TypeError):
            logging.getLogger(__name__).error("Forge retention could not clean a job; retrying on the next sweep.")
