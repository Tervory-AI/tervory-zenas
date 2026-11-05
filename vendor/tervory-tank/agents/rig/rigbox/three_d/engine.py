"""RIG-5 orchestration. Trust the catalogue's fits, never the browser's assembly."""
from __future__ import annotations

import copy
import hashlib
import json
import struct
import time
from urllib.parse import urlsplit
from uuid import UUID

from rigbox.logic import fitting_parts, reprice, rule_matches
from rigbox.service import RigError
from .contracts import Assembly, Job, Report, Review
from .forge import photo_sources


def fail(message, status=422):
    raise RigError("three_d", message, status)


class Engine:
    def __init__(self, store, catalogue, forge, assets, *, sample=False):
        self.store, self.catalogue, self.forge, self.assets = store, catalogue, forge, assets
        self.sample = sample

    async def capabilities(self, tenant):
        row = await self.store.entitlement(tenant)
        enabled = bool(row and row["enabled"])
        return {"rig_3d": enabled, "sample": self.sample, "photo_sources": photo_sources(),
                "valid_until": int(time.time()) + 300 if enabled else 0,
                "billing": {"name": "3D product views", "enabled": enabled,
                            "price": None if not row or row["price_amount"] is None else
                            {"amount": float(row["price_amount"]), "currency": row["currency"]}}}

    async def require(self, tenant):
        if not (await self.capabilities(tenant))["rig_3d"]:
            fail("3D is not included for this shop.", 403)

    async def submit(self, tenant, job: Job):
        await self.require(tenant)
        if UUID(str(tenant)) != job.tenant_id:
            fail("This request belongs to another shop.", 403)
        await self.catalogue.product(tenant, job.product_id)
        previous = await self.store.job(tenant, job.id)
        if previous and Job.model_validate(previous["request"]) != job:
            fail("Use a new job identifier for a different input.", 409)
        if previous and previous["status"] not in {"queued", "running"}:
            return self.job_view(previous)
        # Job id is the idempotency key on both sides. An uncertain submission
        # may be retried with exactly the same request, never with a new id.
        result = await self.forge.submit(job)
        if result.id != job.id or result.tenant_id != job.tenant_id:
            fail("The model service returned the wrong job.", 502)
        await self.store.create(job)
        if result.status == "failed":
            await self.store.finish(tenant, job.id, "failed", reason="The model conversion failed.")
        return self.job_view(await self.store.job(tenant, job.id))

    @staticmethod
    def job_view(row):
        return {"id": str(row["id"]), "product_id": row["product_id"], "status": row["status"],
                "report": row.get("report"), "reason": row.get("reason")}

    async def poll(self, tenant, job_id):
        await self.require(tenant)
        row = await self.store.job(tenant, job_id)
        if not row:
            fail("No such model job.", 404)
        if row["status"] not in {"queued", "running"}:
            return self.job_view(row)
        job = Job.model_validate(row["request"])
        result = await self.forge.poll(job)
        if result.id != job.id or result.tenant_id != job.tenant_id:
            fail("The model service returned the wrong job.", 502)
        if result.status == "failed":
            await self.store.finish(tenant, job.id, "failed", reason="The model conversion failed.")
        elif result.status == "draft":
            if not result.report:
                fail("The model service returned no check report.", 502)
            reason = result.report.rejection(job)
            if reason is None:
                data = await self.forge.artifact(job)
                reason = check_artifact(data, result.report)
            if reason:
                await self.store.finish(tenant, job.id, "failed", result.report.model_dump(mode="json"), reason)
            else:
                await self.assets.put(tenant, job.id, data)
                await self.store.finish(tenant, job.id, "draft", result.report.model_dump(mode="json"))
        return self.job_view(await self.store.job(tenant, job.id))

    async def approve(self, tenant, job_id, review: Review, reviewer):
        await self.require(tenant)
        row = await self.store.job(tenant, job_id)
        if not row or row["status"] != "draft":
            fail("Only a checked draft can be approved.", 409)
        report = Report.model_validate(row["report"])
        reason = report.rejection(Job.model_validate(row["request"]))
        reason = reason or check_artifact(await self.assets.get(tenant, job_id), report)
        if reason:
            fail(reason, 409)
        if not await self.store.approve(tenant, job_id, review.model_dump(), reviewer):
            fail("This model has already been reviewed.", 409)
        return {"approved": True, "id": str(job_id)}

    async def mount_points(self, tenant, part, centre, centre_anchor, part_anchor):
        await self.require(tenant)
        c, p = await self.store.approved(tenant, centre), await self.store.approved(tenant, part)
        if not c or not p or centre_anchor not in c["report"]["anchors"] or part_anchor not in p["report"]["anchors"]:
            fail("Choose attachment points from approved models.")
        bundle = await self.catalogue._bundle(tenant, centre)
        product = next((p for p in bundle["products"] if (p.get("sku") or p.get("id")) == centre), {})
        if not await self.store.set_mounts(tenant, part, centre, product.get("family", ""), centre_anchor, part_anchor):
            fail("This part has no approved fit to that product.", 409)
        return {"saved": True}

    async def assembly(self, tenant, request: Assembly):
        await self.require(tenant)
        # Rebuild from current fits (do not trust the answers cache after revocation).
        from rigbox.logic import setups as build_setups
        bundle = await self.catalogue._bundle(tenant, request.product_id)
        products = [p for p in bundle["products"] if p.get("active", True)]
        centre = next((p for p in products if (p.get("sku") or p.get("id")) == request.product_id), None)
        if not centre:
            fail("No such product.", 404)
        setups = build_setups(bundle)["setups"]
        setup = next((s for s in setups if s["tier"] == request.tier), None)
        if setup is None:
            fail("Choose a setup returned by this shop.")
        setup = copy.deepcopy(setup)
        permitted = {p["sku"]: p for p, _ in fitting_parts(centre, products, bundle["fits"])}
        roles = {p["role"]: p for p in setup["parts"][1:]}
        if not set(request.choices) <= roles.keys():
            fail("That setup has no such part.")
        for role, sku in request.choices.items():
            part = permitted.get(sku)
            if not part or part["category"] != roles[role]["product"]["category"]:
                fail("That part has no approved fit.")
            roles[role]["product"] = {"id": sku}
        setup = reprice(setup, products)
        model = await self.store.approved(tenant, request.product_id)
        base = {"sample": self.sample, "setup": setup, "total": setup["total"],
                "product_id": request.product_id, "photo": photo_url(centre.get("image_url") or ""),
                "valid_until": int(time.time()) + 300, "options": {}}
        if not model:
            return {**base, "available": False, "models": []}
        models = [self.public_model(model, "centre", None)]
        for part in setup["parts"][1:]:
            sku, role = part["product"]["id"], part["role"]
            asset = await self.store.approved(tenant, sku)
            mount = await self._mount(tenant, sku, centre, model, asset)
            # A missing/unreviewed part model uses photos for the whole setup;
            # never silently display a misleading incomplete assembly.
            if not mount:
                return {**base, "available": False, "models": []}
            models.append(self.public_model(asset, role, mount))
            options = []
            for other, row in permitted.items():
                if row["category"] != part["product"]["category"]:
                    continue
                other_asset = await self.store.approved(tenant, other)
                if await self._mount(tenant, other, centre, model, other_asset):
                    options.append({"id": other, "name": row["name"]})
            base["options"][role] = options
        return {**base, "available": True, "models": models}

    async def _mount(self, tenant, sku, centre, model, asset):
        if not asset:
            return None
        rows = await self.store.mounts(tenant, sku, centre["sku"], centre.get("family", ""))
        for row in rows:
            rule = row["rule"] if not isinstance(row["rule"], str) else json.loads(row["rule"])
            if not rule_matches(rule, centre.get("attributes", {})):
                continue
            mapping = row["mount_points"]
            parent, child = mapping.get("centre"), mapping.get("part")
            if parent in model["report"]["anchors"] and child in asset["report"]["anchors"]:
                return {"parent": parent, "child": child,
                        "parent_anchor": model["report"]["anchors"][parent],
                        "part_anchor": asset["report"]["anchors"][child]}
        return None

    @staticmethod
    def public_model(row, role, mount):
        # No raw report, storage path, receipt, source file or price input.
        return {"id": str(row["id"]), "product_id": row["product_id"], "role": role,
                "url": f"/v1/rig/3d/assets/{row['id']}", "mount": mount,
                "sha256": row["report"]["sha256"], "size_bytes": row["report"]["size_bytes"]}

    async def asset(self, tenant, job_id, *, preview=False):
        await self.require(tenant)
        row = await self.store.job(tenant, job_id)
        if not row or row["status"] not in ({"draft", "approved"} if preview else {"approved"}):
            fail("No approved model is available.", 404)
        data = await self.assets.get(tenant, job_id)
        if check_artifact(data, Report.model_validate(row["report"])):
            fail("The saved model failed its integrity check.", 503)
        return data


def check_artifact(data: bytes, report: Report):
    if len(data) != report.size_bytes or hashlib.sha256(data).hexdigest() != report.sha256:
        return "The model does not match its check report"
    try:
        magic, version, length, size, chunk = struct.unpack_from("<4sIIII", data)
        if magic != b"glTF" or version != 2 or length != len(data) or chunk != 0x4E4F534A:
            raise ValueError("GLB header")
        doc = json.loads(data[20:20 + size])
        if "EXT_meshopt_compression" not in doc.get("extensionsUsed", []):
            return "The model has no supported mesh compression"
        # Self-contained GLBs only. A loader must never fetch arbitrary textures,
        # credentials or remote buffer URLs embedded in an uploaded model.
        if any("uri" in obj for group in ("buffers", "images") for obj in doc.get(group, [])):
            return "The model contains an external resource"
        nodes = doc.get("nodes", [])
        roots = doc.get("scenes", [])[doc.get("scene", 0)].get("nodes", [])
        names = {n.get("name"): n for i, n in enumerate(nodes) if i in roots}
        if len([n for n in nodes if n.get("name") in report.anchors]) != len(report.anchors):
            return "The named anchors are ambiguous"
        for name, anchor in report.anchors.items():
            node = names.get(name)
            if not node or "matrix" in node or node.get("scale", [1, 1, 1]) != [1, 1, 1]:
                return "The named anchors do not match the GLB"
            if any(abs(a / 1000 - b) > 1e-6 for a, b in zip(anchor.position_mm, node.get("translation", [0, 0, 0]))):
                return "The named anchors do not match the GLB"
            if any(abs(a - b) > 1e-6 for a, b in zip(anchor.quaternion, node.get("rotation", [0, 0, 0, 1]))):
                return "The named anchors do not match the GLB"
    except (ValueError, KeyError, IndexError, AttributeError, TypeError, struct.error):
        return "The GLB is not valid"
    return None


def photo_url(value):
    """No credentials, signed queries or script URLs in the photo fallback."""
    try:
        parsed = urlsplit(value)
        if (parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme and parsed.scheme != "https") or value.startswith("//")):
            return ""
        return value if (parsed.scheme == "https" and parsed.hostname) or value.startswith("/") else ""
    except ValueError:
        return ""
