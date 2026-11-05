"""Files for one agent, always under /data/<tenant_id>/."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


class UnsafePath(ValueError):
    """The tenant id or file name would leave that tenant's directory."""


def tenant_file(root: Path, tenant_id: str, name: str) -> Path:
    if not isinstance(tenant_id, str) or _TENANT.fullmatch(tenant_id) is None:
        raise UnsafePath("tenant_id is not a safe directory name")
    if not isinstance(name, str) or not name or "\x00" in name or ":" in name:
        raise UnsafePath("file name is empty or not relative")
    if name.startswith(("/", "\\")):
        raise UnsafePath("file name must stay under the tenant directory")
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise UnsafePath("file name must stay under the tenant directory")
    root_dir = root.resolve()
    tenant_dir = (root_dir / tenant_id).resolve()
    dest = root_dir.joinpath(tenant_id, *parts).resolve()
    if dest != tenant_dir and tenant_dir not in dest.parents:
        raise UnsafePath("file name must stay under the tenant directory")
    return dest


def _root(root: Path | None) -> Path:
    return root if root is not None else Path(os.environ.get("DATA_DIR", "/data"))


def save_file(tenant_id: str, name: str, data: bytes, *, root: Path | None = None) -> Path:
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("save_file expects bytes")
    path = tenant_file(_root(root), tenant_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return path


def read_file(tenant_id: str, name: str, *, root: Path | None = None) -> bytes:
    return tenant_file(_root(root), tenant_id, name).read_bytes()
