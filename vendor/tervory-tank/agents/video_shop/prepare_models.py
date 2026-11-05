"""Build/test preparation only. No downloads in the serving room."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def verify(root: Path) -> dict:
    manifest = json.loads(Path(__file__).with_name('models.json').read_text())
    for name, asset in manifest.items():
        path = root / name
        if not path.is_file():
            raise ValueError('local_model_missing: ' + name)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != asset['sha256']:
            raise ValueError('local_model_checksum: ' + name)
    return manifest


def prepare(root: Path) -> None:
    manifest = json.loads(Path(__file__).with_name('models.json').read_text())
    for name, asset in manifest.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() == asset['sha256']:
                    continue
        partial = path.with_suffix('.partial')
        try:
            # URLs are checked-in, immutable revisions; curl verifies TLS, bounds
            # transfer time and refuses HTTP downgrade, including on CDN redirects.
            subprocess.run(['curl', '--fail', '--location', '--proto', '=https',
                            '--proto-redir', '=https', '--max-time', '900', '--retry', '3',
                            '--output', str(partial), asset['url']], check=True)
            with partial.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != asset['sha256']:
                    raise ValueError('local_model_checksum: ' + name)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    verify(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    prepare(parser.parse_args().directory)
