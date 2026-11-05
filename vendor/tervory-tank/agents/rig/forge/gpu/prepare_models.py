"""Verify a legally provisioned, offline checkpoint bundle before baking the image."""
import hashlib
import json
from pathlib import Path


def prepare(root, manifest):
    evidence = []
    for model in manifest:
        for item in model["files"]:
            path = root / model["destination"] / item["path"]
            sha, git_sha = hashlib.sha256(), hashlib.sha1()
            git_sha.update(f"blob {path.stat().st_size}\0".encode())
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    sha.update(chunk)
                    git_sha.update(chunk)
            if ("sha256" in item and sha.hexdigest() != item["sha256"]
                    or "git_blob_sha1" in item and git_sha.hexdigest() != item["git_blob_sha1"]):
                raise ValueError("Checkpoint checksum mismatch")
            evidence.append({"path": str(path.relative_to(root)), "sha256": sha.hexdigest(),
                             "revision": model["revision"]})
    (root / "verified-sha256.json").write_text(json.dumps(evidence, indent=2))
    pipeline = root / "trellis2" / "pipeline.json"
    body = json.loads(pipeline.read_text())
    args = body["args"]
    args["models"]["sparse_structure_decoder"] = str(root / "trellis1" / "ckpts/ss_dec_conv3d_16l8_fp16")
    args["image_cond_model"]["args"]["model_name"] = str(root / "dino")
    args["rembg_model"] = {"name": "PreparedBackground", "args": {}}
    pipeline.write_text(json.dumps(body, indent=2))


if __name__ == "__main__":
    prepare(Path("/opt/models"), json.loads(Path(__file__).with_name("models.lock.json").read_text()))
