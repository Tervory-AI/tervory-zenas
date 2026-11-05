"""Build-only installer for verified upstream archives, including git submodules."""
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def install(row, destination, strip_root=True):
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "source.tar.gz"
        digest, size = hashlib.sha256(), 0
        with urllib.request.urlopen(row["url"], timeout=120) as source, archive.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if digest.hexdigest() != row["sha256"] or ("size" in row and size != row["size"]):
            raise ValueError("Upstream source checksum mismatch")
        unpacked = Path(directory) / "unpacked"
        with tarfile.open(archive) as source:
            source.extractall(unpacked, filter="data")
        roots = list(unpacked.iterdir())
        origin = roots[0] if strip_root and len(roots) == 1 and roots[0].is_dir() else unpacked
        shutil.copytree(origin, destination, dirs_exist_ok=True)


if __name__ == "__main__":
    root = Path("/opt")
    manifest = json.loads(Path(sys.argv[1]).read_text())
    if isinstance(manifest, dict):
        install(manifest, root / "meshroom")
    else:
        for row in manifest:
            install(row, root / row["destination"])
