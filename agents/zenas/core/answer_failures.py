"""Bounded failure metadata. Never retain exception messages or HTTP bodies."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_CODES = frozenset({"paid_work_paused", "paid_work_pause", "cost_cap", "cost_cap_exceeded"})


def safe_failure(value: Any) -> dict[str, Any]:
    """Revalidate metadata when reading old selftest rows as well as new errors."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    name = value.get("exception")
    if isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", name):
        out["exception"] = name
    path = value.get("file")
    line = value.get("line")
    if isinstance(path, str) and (_ROOT / path).is_file() and not Path(path).is_absolute() and ".." not in Path(path).parts:
        if path.endswith(".py") and isinstance(line, int) and not isinstance(line, bool) and line > 0:
            out.update(file=path, line=line)
    status = value.get("http_status")
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        out["http_status"] = status
    if value.get("refusal_code") in _CODES:
        out["refusal_code"] = value["refusal_code"]
    return out


def exception_failure(exc: Exception) -> dict[str, Any]:
    data: dict[str, Any] = {"exception": type(exc).__name__}
    tb = exc.__traceback__
    while tb is not None:
        path = Path(tb.tb_frame.f_code.co_filename).resolve()
        if path.is_relative_to(_ROOT):
            data.update(file=str(path.relative_to(_ROOT)), line=tb.tb_lineno)
        tb = tb.tb_next
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    # TankClient intentionally discards response bodies and raises this fixed message.
    if status is None and isinstance(exc, RuntimeError):
        match = re.fullmatch(r"tank answered ([1-5][0-9]{2})", str(exc))
        if match:
            status = int(match[1])
    data["http_status"] = status
    data["refusal_code"] = getattr(exc, "code", None)
    return safe_failure(data)
