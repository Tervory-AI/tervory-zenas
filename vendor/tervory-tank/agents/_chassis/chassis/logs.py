"""One JSON line per event. Keys and file bytes never go here."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def log_line(
    agent: str,
    message: str,
    *,
    tenant_id: str | None = None,
    job_id: str | None = None,
    level: str = "info",
    error: str | None = None,
) -> None:
    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "agent": agent,
        "msg": message,
    }
    if tenant_id:
        row["tenant_id"] = str(tenant_id)
    if job_id:
        row["job_id"] = str(job_id)
    if error:
        row["error"] = str(error)
    sys.stdout.write(json.dumps(row, default=str) + "\n")
    sys.stdout.flush()
