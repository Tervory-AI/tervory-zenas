"""Bounded, temporary browser evidence. This process never uploads or reads R2 keys."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

HTML_CHAR_LIMIT = 600_000
SCREENSHOT_HEIGHT = 8_000
SCREENSHOT_BYTES = 8 * 1024 * 1024
PDF_BYTES = 10_000_000
RETENTION_SECONDS = 24 * 60 * 60
RETENTION_BYTES = 256 * 1024 * 1024


def prune(root: Path) -> None:
    """Room-wide oldest-first cap; never touch cookies or another file type."""
    files = []
    now = time.time()
    for path in root.glob('tenants/*/evidence/*'):
        if any(parent.is_symlink() for parent in (path, path.parent, path.parent.parent)) or path.suffix not in {'.html', '.jpg', '.pdf', '.tmp'}:
            continue
        try:
            info = path.stat()
            if now - info.st_mtime > RETENTION_SECONDS:
                path.unlink(missing_ok=True)
            else:
                files.append((info.st_mtime, info.st_size, path))
        except OSError:
            continue
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= RETENTION_BYTES:
            break
        try:
            path.unlink(missing_ok=True)
            total -= size
        except OSError:
            continue


def write_complete(path: Path, data: bytes) -> None:
    """Only completed files get an uploadable suffix, even on a full disk."""
    temporary = path.with_suffix(path.suffix + '.tmp')
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # Any orphan temporary file remains covered by retention.


async def stage(root: Path, directory: Path, task: str, *, html: str = '', pdf: bytes | None = None, page=None) -> dict:
    """Evidence failures are explicit, but do not discard a successful page read."""
    identity = hashlib.sha256(os.urandom(32) + task.encode()).hexdigest()
    result = {'id': identity, 'status': 'pending_upload', 'files': []}
    try:
        prune(root)
        destination = directory / 'evidence'
        destination.mkdir(exist_ok=True)
        if pdf is not None:
            if len(pdf) > PDF_BYTES:
                raise ValueError('PDF evidence exceeds limit')
            payload = pdf
            suffix = '.pdf'
        else:
            payload = html[:HTML_CHAR_LIMIT].encode('utf-8')
            suffix = '.html'
        write_complete(destination / (identity + suffix), payload)
        result['files'].append(suffix)
        if page is not None and pdf is None:
            try:
                bounds = await page.evaluate('''() => ({width: Math.max(1, Math.min(1600, document.documentElement.clientWidth)),
                    height: Math.max(1, Math.min(8000, document.documentElement.scrollHeight))})''')
                image = await page.screenshot(type='jpeg', quality=70,
                    clip={'x': 0, 'y': 0, 'width': bounds['width'], 'height': min(SCREENSHOT_HEIGHT, bounds['height'])},
                    timeout=10000)
                if len(image) > SCREENSHOT_BYTES:
                    raise ValueError('Screenshot evidence exceeds limit')
                write_complete(destination / (identity + '.jpg'), image)
                result['files'].append('.jpg')
            except Exception:
                result['screenshot_error'] = 'Screenshot evidence could not be saved.'
        prune(root)
    except Exception:
        result['status'] = 'failed'
        result['error'] = 'Page evidence could not be saved.'
    return result
