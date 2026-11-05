"""Unpack a tarball, zip, or git bundle that the caller handed over.

Radix does not clone. Remote URLs are refused.
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_SOURCE = 32 * 1024 * 1024
MAX_MEMBERS = 5000
MAX_UNPACKED = 64 * 1024 * 1024

_B64 = re.compile(r"[A-Za-z0-9+/]+=*")


def _bounded(raw: bytes) -> bytes:
    if len(raw) > MAX_SOURCE:
        raise ValueError("source is too large")
    return raw


def _kind(raw: bytes) -> str:
    if raw.startswith(b"\x1f\x8b"):
        return "tar"
    if len(raw) > 262 and raw[257:262] == b"ustar":
        return "tar"
    if raw.startswith(b"PK\x03\x04"):
        return "zip"
    if raw.startswith(b"# v2 git bundle") or raw.startswith(b"# v3 git bundle"):
        return "bundle"
    return ""


def _maybe_b64(text: str) -> bytes | None:
    forced = text.startswith("b64:")
    compact = "".join((text[4:] if forced else text).split())
    if not forced and len(compact) < 80:
        return None
    if _B64.fullmatch(compact) is None:
        return None
    try:
        decoded = base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error):
        return None
    if forced or _kind(decoded):
        return decoded
    return None


def load_bytes(source: str, read_file) -> bytes:
    text = source.strip()
    if not text:
        raise ValueError("source is required")
    if text.startswith(("http://", "https://", "git@", "ssh://", "git://")):
        raise ValueError("source must be handed over, not fetched")
    if text.startswith("file:"):
        return _bounded(read_file(text[5:]))
    decoded = _maybe_b64(text)
    if decoded is not None and _kind(decoded):
        return _bounded(decoded)
    return _bounded(read_file(text))


def _unsafe(name: str) -> bool:
    if not name or name.startswith(("/", "\\")):
        return True
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return (not parts) or any(part in ("", ".", "..") for part in parts)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["http_proxy"] = "http://127.0.0.1:9"
    env["https_proxy"] = "http://127.0.0.1:9"
    env["HTTP_PROXY"] = "http://127.0.0.1:9"
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    return env


def _unpack_tar(raw: bytes, dest: Path) -> None:
    try:
        handle = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
    except tarfile.TarError as exc:
        raise ValueError("source is not a readable archive") from exc
    with handle:
        members = handle.getmembers()
        if len(members) > MAX_MEMBERS:
            raise ValueError("archive has too many files")
        total = sum(item.size for item in members if item.isfile())
        if total > MAX_UNPACKED:
            raise ValueError("archive is too large")
        for item in members:
            if _unsafe(item.name):
                raise ValueError("archive path is not safe")
        handle.extractall(dest, members=members, filter="data")


def _unpack_zip(raw: bytes, dest: Path) -> None:
    try:
        handle = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("source is not a readable archive") from exc
    with handle:
        infos = handle.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ValueError("archive has too many files")
        total = sum(item.file_size for item in infos if not item.is_dir())
        if total > MAX_UNPACKED:
            raise ValueError("archive is too large")
        for item in infos:
            if item.is_dir():
                continue
            if _unsafe(item.filename):
                raise ValueError("archive path is not safe")
            handle.extract(item, dest)


def _unpack_bundle(raw: bytes, dest: Path, commit: str | None = None) -> Path:
    bundle = dest / "source.bundle"
    bundle.write_bytes(raw)
    work = dest / "repo"
    git = shutil.which("git")
    if git is None:
        raise ValueError("git is not available for a bundle")
    try:
        completed = subprocess.run(
            [git, "-c", "protocol.file.allow=always", "clone", "--quiet", str(bundle), str(work)],
            capture_output=True,
            timeout=60,
            env=_git_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("git bundle could not be read") from exc
    if completed.returncode != 0:
        raise ValueError("git bundle could not be read")
    if commit is not None:
        actual=subprocess.run([git,'rev-parse','HEAD'],cwd=work,env=_git_env(),capture_output=True,text=True,timeout=10,check=True).stdout.strip()
        if actual!=commit:
            raise ValueError('bundle commit mismatch')
    git_dir = work / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)
    bundle.unlink(missing_ok=True)
    return work


def unpack(raw: bytes, dest: Path, *, commit: str | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    kind = _kind(raw)
    if commit is not None and (kind!='bundle' or re.fullmatch(r'[0-9a-f]{40}',commit) is None):
        raise ValueError('bundle commit required')
    if kind == "tar":
        _unpack_tar(raw, dest)
        return dest
    if kind == "zip":
        _unpack_zip(raw, dest)
        return dest
    if kind == "bundle":
        return _unpack_bundle(raw, dest, commit)
    raise ValueError("source is not a tar archive, zip archive, or git bundle")
