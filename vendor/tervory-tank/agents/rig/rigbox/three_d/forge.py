"""Only the brain can reach the sealed forge. No conversion tools run here."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Protocol
from uuid import UUID

import httpx

from rigbox.service import RigError
from .contracts import Job, Report, Result


def photo_sources():
    # This switch is off unless explicitly enabled, as in the brain and forge.
    return ["scan", "ai"] if os.environ.get("RIG_FORGE_TRELLIS_ENABLED", "").lower() == "true" else ["scan"]


class Forge(Protocol):
    async def submit(self, job: Job) -> Result: ...
    async def poll(self, job: Job) -> Result: ...
    async def artifact(self, job: Job) -> bytes: ...


class BrainForge:
    """RIG-5F draft ab2400e: brain start/poll plus tenant/job-scoped R2 files."""
    def __init__(self, client: httpx.AsyncClient, key: str, files=None):
        self.client, self.key, self.files = client, key, files

    async def _call(self, method: str, job: Job):
        if not self.key:
            raise RigError("forge_unavailable", "The product service is not connected.", 503)
        try:
            response = await self.client.request(
                method, "http://rig-brain:8001/forge/jobs" + ("/" + str(job.id) if method == "GET" else ""),
                headers={"X-Tank-Key": self.key},
                params={"tenant_id": str(job.tenant_id), "home_region": "us-east"} if method == "GET" else None,
                json=wire_job(job) if method == "POST" else None, timeout=30, follow_redirects=False,
            )
            if response.status_code not in {200, 202} or len(response.content) > 100_000:
                raise ValueError("relay failed")
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise RigError("forge_unavailable", "The product service is not connected.", 503) from None

    async def submit(self, job: Job) -> Result:
        if not self.key or self.files is None:
            raise RigError("forge_unavailable", "The product service is not connected.", 503)
        if not job.anchors:
            raise RigError("anchors", "Enter the measured attachment points before preparing this product.", 422)
        if job.source not in photo_sources():
            raise RigError("source_unavailable", "Preparation from a few photos is not available yet.", 422)
        if job.cost_cap_usd <= 0:
            raise RigError("budget", "Photo preparation needs a positive cost limit.", 422)
        await self.files.stage(job)
        return await self._result(await self._call("POST", job), job)

    async def poll(self, job: Job) -> Result:
        return await self._result(await self._call("GET", job), job)

    async def _result(self, value, job):
        try:
            # Forge's transient queued response may omit scope. It comes from
            # the authenticated, exact job URL; every supplied identity must match.
            if UUID(value.get("tenant_id", str(job.tenant_id))) != job.tenant_id or UUID(value.get("job_id", str(job.id))) != job.id:
                raise ValueError("wrong job")
            status = value["status"]
            if status in {"queued", "running", "failed"}:
                return Result(id=job.id, tenant_id=job.tenant_id, status=status,
                              failure="conversion" if status == "failed" else None)
            if status != "draft" or value.get("asset_key") != f"forge/{job.tenant_id}/{job.id}/drafts/model.glb":
                raise ValueError("invalid draft")
            report = value["report"]
            checks = report["checks"]
            if report["passed"] is not True or not all(checks.get(k) is True for k in
                    ("finite", "watertight", "outward", "manifold", "dimensions", "budget", "gltf_validator")):
                return Result(id=job.id, tenant_id=job.tenant_id, status="failed", failure="validation")
            receipts, stages = [], set()
            for row in value["receipts"]:
                if "ledger_id" not in row:
                    continue  # GPU reservation/claim acknowledgements are not receipts.
                if (row.get("tenant_id") != str(job.tenant_id) or row.get("task_id") != str(job.id)
                        or row.get("outcome") != "delivered"):
                    raise ValueError("invalid receipt")
                receipts.append(UUID(row["ledger_id"]))
                stages.add(row.get("stage"))
            if not {"model_check", "validate", "upload"} <= stages:
                raise ValueError("missing receipts")
            raw = await self.files.draft(job)
            if len(raw) != report["bytes"] or len(raw) > job.max_bytes:
                raise ValueError("wrong artifact size")
            data = add_anchors(raw, job)
            parsed = Report(outward_faces=True, watertight=True, real_size=True, gltf_valid=True,
                triangles=report["triangles"], size_bytes=len(data), dimensions_mm=report["dimensions_mm"],
                compression="meshopt", sha256=hashlib.sha256(data).hexdigest(), anchors=job.anchors, receipt_ids=receipts)
            return Result(id=job.id, tenant_id=job.tenant_id, status="draft", report=parsed)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise RigError("forge_invalid", "The product service returned an invalid result.", 502) from None

    async def artifact(self, job: Job) -> bytes:
        if self.files is None:
            raise RigError("forge_unavailable", "The product files are not connected.", 503)
        return add_anchors(await self.files.draft(job), job)


def wire_job(job: Job):
    """Exact fields of RIG-5F's Job; product/anchor metadata stays in RIG-5."""
    return {"tenant_id": str(job.tenant_id), "job_id": str(job.id), "home_region": "us-east",
            "method": {"ai": "trellis2", "scan": "photogrammetry"}[job.source],
            "inputs": [name.rsplit("/", 1)[1] for name in job.inputs], "unit_mm": 1.0,
            "dimensions_mm": list(job.dimensions_mm), "max_triangles": job.max_triangles,
            "max_bytes": job.max_bytes, "timeout_seconds": job.timeout_seconds,
            "cost_cap_usd": format(job.cost_cap_usd, ".6f").rstrip("0").rstrip(".")}


def add_anchors(data: bytes, job: Job) -> bytes:
    """Add reviewed placement metadata only; preserve geometry/binary chunks."""
    import struct
    try:
        magic, version, length, size, kind = struct.unpack_from("<4sIIII", data)
        if (magic, version, length, kind) != (b"glTF", 2, len(data), 0x4E4F534A):
            raise ValueError("invalid container")
        doc = json.loads(data[20:20+size])
        nodes = doc.setdefault("nodes", [])
        roots = doc["scenes"][doc.get("scene", 0)].setdefault("nodes", [])
        names = {node.get("name") for node in nodes}
        if any(name in names for name in job.anchors):
            raise ValueError("attachment point name already exists")
        for name, anchor in job.anchors.items():
            roots.append(len(nodes))
            nodes.append({"name": name, "translation": [n / 1000 for n in anchor.position_mm], "rotation": list(anchor.quaternion)})
        encoded = json.dumps(doc, separators=(",", ":")).encode()
        encoded += b" " * (-len(encoded) % 4)
        tail = data[20+size:]
        return struct.pack("<4sIIII", b"glTF", 2, 20+len(encoded)+len(tail), len(encoded), 0x4E4F534A) + encoded + tail
    except (ValueError, KeyError, IndexError, TypeError, struct.error):
        raise RigError("anchors", "The attachment points could not be added. Check their names and the product file.", 422) from None
