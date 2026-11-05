"""Install exact, verified upstream artifacts. Never disable TLS or hash checks."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path


def main(destination: Path) -> None:
    lock = json.loads(Path(__file__).with_name("tools.lock.json").read_text())
    destination.mkdir(parents=True, exist_ok=True)
    cache = destination / "archives"
    cache.mkdir(exist_ok=True)
    bin_dir = destination / "bin"
    bin_dir.mkdir(exist_ok=True)
    for asset in lock["assets"]:
        archive = cache / asset["url"].rsplit("/", 1)[1]
        if not archive.exists():
            temporary = archive.with_suffix(archive.suffix + ".partial")
            with urllib.request.urlopen(asset["url"], timeout=120) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            temporary.replace(archive)
        data = archive.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise RuntimeError("Artifact SHA256 mismatch: " + asset["name"])
        if "upstream_integrity" in asset:
            published = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
            if published != asset["upstream_integrity"]:
                raise RuntimeError("Published npm integrity mismatch: " + asset["name"])
            continue  # npm ci installs these same bytes using package-lock.json.
        target = destination / asset["name"]
        target.mkdir(exist_ok=True)
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zipped:
                for member in zipped.infolist():
                    if not (target / member.filename).resolve().is_relative_to(target.resolve()):
                        raise RuntimeError("Archive path escaped destination")
                zipped.extractall(target)
        else:
            with tarfile.open(archive) as packed:
                packed.extractall(target, filter="data")
        names = {"blender": ["blender"], "gltfpack": ["gltfpack"], "ktx": ["toktx", "ktx"]}[asset["name"]]
        for name in names:
            matches = [p for p in target.rglob(name) if p.is_file()]
            if len(matches) != 1:
                raise RuntimeError("Expected one executable: " + name)
            matches[0].chmod(matches[0].stat().st_mode | 0o111)
            link = bin_dir / name
            if link.is_symlink():
                link.unlink()
            os.symlink(matches[0].resolve(), link)
        print("Verified " + asset["name"] + " " + asset["version"])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
