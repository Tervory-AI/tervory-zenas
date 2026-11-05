"""HF controller: short-lived object capabilities only, never R2/HF credentials."""
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, unquote

import requests


def validate_manifest(body, tenant, job, method):
    if body.get("tenant_id") != tenant or body.get("job_id") != job or body.get("method") != method:
        raise ValueError("Manifest scope mismatch")
    names = body["inputs"]
    low, high = (50, 150) if method == "photogrammetry" else (1, 4)
    if not low <= len(names) <= high or len(set(names)) != len(names):
        raise ValueError("Invalid photo count")
    if any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,110}\.(?i:png|jpe?g|webp)", name) for name in names):
        raise ValueError("Invalid photo name")
    dimensions = body["dimensions_mm"]
    if len(dimensions) != 3 or any(not math.isfinite(x) or not 0 < x <= 100000 for x in dimensions):
        raise ValueError("Measured dimensions are required")
    if not 60 <= body["timeout_seconds"] <= 3600:
        raise ValueError("Invalid timeout")


def validate_url(url, key):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or not re.fullmatch(r"[a-f0-9]{32}\.r2\.cloudflarestorage\.com", parsed.hostname or "")
            or unquote(parsed.path) != "/tervory-rig/" + key or not parsed.query or parsed.fragment):
        raise ValueError("Invalid scoped transfer")


def download(url, target, maximum):
    # Signed capabilities must never be redirected to a different host.
    with requests.get(url, stream=True, timeout=(10, 120), allow_redirects=False) as response:
        if response.status_code != 200 or int(response.headers.get("Content-Length", 0)) > maximum:
            raise ValueError("Input transfer failed")
        size = 0
        with target.open("xb") as output:
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ValueError("Photo exceeds job budget")
                output.write(chunk)


def upload(url, path):
    with path.open("rb") as content:
        with requests.put(url, data=content, headers={"x-amz-meta-approval": "draft"},
                          timeout=(10, 120), allow_redirects=False) as response:
            if response.status_code not in {200, 204}:
                raise ValueError("Output transfer failed")


def main(method):
    if method not in {"photogrammetry", "trellis2"}:
        raise ValueError("Unsupported GPU method")
    tenant, job = (str(uuid.UUID(os.environ[name])) for name in ("FORGE_TENANT_ID", "FORGE_JOB_ID"))
    prefix = f"forge/{tenant}/{job}/"
    # Even /proc access by a compromised same-user parser can reveal only these
    # expiring capabilities for this job, never a bucket-wide signing credential.
    transfers = json.loads(os.environ.pop("FORGE_TRANSFERS"))
    body = transfers["manifest"]
    validate_manifest(body, tenant, job, method)
    if set(transfers["get"]) != set(body["inputs"]) or set(transfers["put"]) != {"result.glb", "source.zip"}:
        raise ValueError("Invalid transfer set")
    for direction, folder in (("get", "inputs/"), ("put", "gpu/")):
        for name, url in transfers[direction].items():
            validate_url(url, prefix + folder + name)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        (work / "manifest.json").write_text(json.dumps(body))
        photos = work / "photos"
        photos.mkdir()
        for name in body["inputs"]:
            download(transfers["get"][name], photos / name, 20_000_000)
        safe = {name: os.environ[name] for name in ("PATH", "LD_LIBRARY_PATH", "CUDA_HOME") if name in os.environ}
        safe.update(HOME=directory, TMPDIR=directory, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                    HF_HUB_DISABLE_TELEMETRY="1", PYTHONPATH="/opt/trellis2", OMP_NUM_THREADS="4",
                    OPENCV_IO_ENABLE_OPENEXR="1", PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
        subprocess.run([sys.executable, str(Path(__file__).with_name("reconstruct.py")), method, directory],
            env=safe, check=True, timeout=body["timeout_seconds"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        outputs = work / "outputs"
        artifacts, total = [], 0
        for path in sorted(outputs.iterdir()):
            if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", path.name):
                raise ValueError("Invalid output artifact")
            total += path.stat().st_size
            if total > 256_000_000:
                raise ValueError("GPU output exceeds ingestion budget")
            artifacts.append(path)
        if not (outputs / "result.glb").is_file():
            raise ValueError("GPU job produced no model")
        # A fixed object key contains original OBJ/materials/textures. The worker
        # never gets authority to invent output keys or list other jobs' objects.
        archive = work / "source.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for path in artifacts:
                if path.name != "result.glb":
                    bundle.write(path, arcname=path.name)
        upload(transfers["put"]["source.zip"], archive)
        upload(transfers["put"]["result.glb"], outputs / "result.glb")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception:
        # Provider errors may contain signed capabilities: never print them.
        print("Forge GPU reconstruction failed. No model was approved.", file=sys.stderr)
        sys.exit(1)
