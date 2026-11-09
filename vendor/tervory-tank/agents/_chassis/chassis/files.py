"""Files for one agent, always under /data/<tenant_id>/."""

from __future__ import annotations

import os
from contextlib import ExitStack
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
    if tenant_dir != root_dir / tenant_id:
        raise UnsafePath("tenant directory must not be a symlink")
    dest = root_dir.joinpath(tenant_id, *parts).resolve()
    if dest != tenant_dir and tenant_dir not in dest.parents:
        raise UnsafePath("file name must stay under the tenant directory")
    return dest


def _root(root: Path | None) -> Path:
    return root if root is not None else Path(os.environ.get("DATA_DIR", "/data"))


def _open(root: Path, tenant_id: str, name: str, *, write: bool):
    tenant_file(root, tenant_id, name)
    parts = (tenant_id, *PurePosixPath(name.replace("\\", "/")).parts)
    with ExitStack() as stack:
        try:
            if write:
                root.mkdir(parents=True, exist_ok=True)
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, directory)
            for part in parts[:-1]:
                if write:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                stack.callback(os.close, directory)
            flags = os.O_WRONLY | os.O_CREAT if write else os.O_RDONLY
            handle = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            try:
                import stat
                info = os.fstat(handle)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise UnsafePath("file must be a private regular file")
                if write:
                    os.ftruncate(handle, 0)
                return os.fdopen(handle, "wb" if write else "rb")
            except BaseException:
                os.close(handle)
                raise
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            raise UnsafePath("file must stay under the tenant directory") from exc


def save_file(tenant_id: str, name: str, data: bytes, *, root: Path | None = None) -> Path:
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("save_file expects bytes")
    target_root = _root(root)
    with _open(target_root, tenant_id, name, write=True) as file:
        file.write(bytes(data))
    return tenant_file(target_root, tenant_id, name)


def read_file(tenant_id: str, name: str, *, root: Path | None = None) -> bytes:
    with _open(_root(root), tenant_id, name, write=False) as file:
        return file.read()
