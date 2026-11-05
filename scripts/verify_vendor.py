"""Verify every copied dependency against the immutable upstream Git tree."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor/tervory-tank'
SOURCE = (VENDOR / 'SOURCE').read_text().strip()
assert len(SOURCE) == 40 and all(c in '0123456789abcdef' for c in SOURCE)
candidates = [Path(os.environ.get('TANK_SOURCE_REPO', '/nonexistent')),
              ROOT.parent / 'tervory-tank', ROOT / '.cache/tank-source.git']
repository = next((p for p in candidates if p.exists() and subprocess.run(
    ['git', '-C', str(p), 'cat-file', '-e', SOURCE + '^{commit}'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0), None)
if repository is None:
    repository = ROOT / '.cache/tank-source.git'
    repository.parent.mkdir(exist_ok=True)
    if not repository.exists():
        subprocess.run(['git', 'init', '--bare', str(repository)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['git', '-C', str(repository), 'fetch', '--depth=1',
                    'https://github.com/Tervory-AI/tervory-tank.git', SOURCE], check=True)
tree = subprocess.check_output(['git', '-C', str(repository), 'ls-tree', '-rz', SOURCE])
entries = {}
for record in tree.split(b'\0'):
    if record:
        metadata, path = record.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        entries[path.decode()] = (mode, kind, oid)
expected = set((ROOT / 'VENDORED-PATHS.txt').read_text().splitlines())
actual = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob('*') if p.is_file() or p.is_symlink()} - {'SOURCE'}
assert actual == expected, f'Vendor file inventory drift: {sorted(actual ^ expected)}'
for name in sorted(expected):
    mode, kind, oid = entries[name]
    path = VENDOR / name
    assert kind == 'blob' and mode in {'100644', '100755'}, name
    assert not path.is_symlink(), f'Unexpected vendor symlink: {name}'
    content = path.read_bytes()
    actual_oid = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
    assert actual_oid == oid, f'Vendor content differs from SOURCE: {name}'
    assert bool(path.stat().st_mode & 0o111) == (mode == '100755'), f'Vendor mode drift: {name}'
print(f'Vendor verified: {len(expected)} files match {SOURCE}', flush=True)
