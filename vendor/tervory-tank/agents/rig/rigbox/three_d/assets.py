"""Tenant-private drafts. Only approved bytes are served by the public route.

The CDN must forward X-Rig-Key and respect private/no-store. Never publish a
draft bucket or a signed URL. The browser caches approved bytes in its own
short-lived, tenant-partitioned kiosk cache instead.
"""
import hashlib
import hmac
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from chassis.storage import StorageError
from rigbox.rooms import RoomStorage as Storage, require_owner
import httpx
from rigbox.deadline import bounded


PHOTO_MAX_BYTES = 20_000_000
JOB_INPUT_MAX_BYTES = 1_000_000_000
CHUNK_BYTES = 64 * 1024


class PhotoInputError(StorageError):
    """A safe, actionable input error; never contains provider data."""


class ForgeFiles(Storage):
    """Stream validated photos to this job's private prefix using Rig's R2 pair."""
    def prefix(self, job):
        require_owner(job.tenant_id)
        if self.region != "us-east":
            raise StorageError("This conversion is available on the US box only")
        return f"forge/{job.tenant_id}/{job.id}/"

    def _signed_digest(self, path, digest):
        # SigV4 for a pre-hashed file. Storage._sign accepts only whole byte
        # buffers; keep that shared API unchanged and never read a photo whole.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        headers = {"host": self.host, "x-amz-content-sha256": digest, "x-amz-date": stamp}
        names = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canonical = "\n".join(("PUT", path, "", canonical_headers, names, digest))
        scope = f"{stamp[:8]}/auto/s3/aws4_request"
        message = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        key = ("AWS4" + self._secret).encode()
        for part in (stamp[:8], "auto", "s3", "aws4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={self._access}/{scope}, SignedHeaders={names}, Signature={signature}"
        return headers

    @bounded(180, lambda: StorageError("Input storage is unavailable"))
    async def _read_photo(self, key, suffix, remaining, target=None):
        path = "/" + self.bucket + "/" + key
        size, signature, digest = 0, bytearray(), hashlib.sha256()
        async with self._http.stream("GET", f"https://{self.host}{path}",
                                     headers=self._sign("GET", path, "", b"")) as response:
            if response.status_code != 200:
                raise StorageError("An uploaded photo could not be read")
            if response.headers.get("content-encoding", "identity") != "identity":
                raise PhotoInputError("Upload an uncompressed JPEG or PNG file")
            length = response.headers.get("content-length")
            if length is not None:
                if not length.isdecimal():
                    raise PhotoInputError("The photo size could not be checked")
                if int(length) > PHOTO_MAX_BYTES or int(length) > remaining:
                    raise PhotoInputError("Photos must be at most 20 MB each and 1 GB per job")
            async for chunk in response.aiter_raw(CHUNK_BYTES):
                size += len(chunk)
                if size > PHOTO_MAX_BYTES or size > remaining:
                    raise PhotoInputError("Photos must be at most 20 MB each and 1 GB per job")
                signature.extend(chunk[:max(0, 8 - len(signature))])
                digest.update(chunk)
                if target is not None:
                    target.write(chunk)
            if length is not None and size != int(length):
                raise PhotoInputError("The photo transfer was incomplete")
        valid = (suffix in {"jpg", "jpeg"} and signature.startswith(b"\xff\xd8\xff")) or (
            suffix == "png" and signature == b"\x89PNG\r\n\x1a\n")
        if not valid:
            raise PhotoInputError("Each file must contain a JPEG or PNG matching its extension")
        return size, digest.hexdigest()

    async def stage(self, job):
        from .contracts import Job
        job = Job.model_validate(job.model_dump())
        prefix = self.prefix(job)
        try:
            # Validate all inputs before any storage write or paid submission.
            # Disk scratch is bounded by the job cap and removed on every exit.
            with TemporaryDirectory(prefix="rig-photos-") as directory:
                photos, total = [], 0
                for index, source in enumerate(job.inputs):
                    name = source.rsplit("/", 1)[1]
                    local = Path(directory) / str(index)
                    key = self._prefix(str(job.tenant_id), "uploads", public=False) + name
                    with local.open("wb") as output:
                        size, digest = await self._read_photo(key, name.rsplit(".", 1)[-1].lower(),
                                                             JOB_INPUT_MAX_BYTES - total, output)
                    total += size
                    photos.append((name, local, size, digest))
                for name, local, size, digest in photos:
                    key = prefix + "inputs/" + name
                    path = "/" + self.bucket + "/" + key
                    headers = self._signed_digest(path, digest)
                    headers.update({"If-None-Match": "*", "Content-Length": str(size)})
                    async def chunks():
                        with local.open("rb") as source:
                            while chunk := source.read(CHUNK_BYTES):
                                yield chunk
                    async with asyncio.timeout(180), self._http.stream("PUT", f"https://{self.host}{path}", headers=headers,
                                                 content=chunks()) as response:
                        if response.status_code == 412:
                            existing = await self._read_photo(key, name.rsplit(".", 1)[-1].lower(), PHOTO_MAX_BYTES)
                            if existing != (size, digest):
                                raise StorageError("This job already has different input files")
                        elif response.status_code not in {200, 204}:
                            raise StorageError("Input files could not be prepared")
        except (httpx.HTTPError, OSError):
            raise StorageError("Input storage is unavailable") from None

    async def draft(self, job):
        data = await self._request("GET", self.prefix(job) + "drafts/model.glb")
        if len(data) > job.max_bytes:
            raise StorageError("The product file exceeds its size limit")
        return data


def asset_name(job_id):
    return f"rig-3d/{UUID(str(job_id))}.glb"


class R2Assets:
    def __init__(self, storage: Storage):
        self.storage = storage

    async def put(self, tenant, job_id, data):
        return await self.storage.put(str(tenant), "work", asset_name(job_id), data)

    async def get(self, tenant, job_id):
        return await self.storage.get(str(tenant), "work", asset_name(job_id))


class LocalAssets:
    """Explicit cloud test-page storage, never an automatic R2 fallback."""
    def __init__(self, root: Path):
        self.root = root

    def path(self, tenant, job_id):
        return self.root / str(UUID(str(tenant))) / asset_name(job_id)

    async def put(self, tenant, job_id, data):
        path = self.path(tenant, job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Immutable job outputs: a retry may confirm, never replace, the bytes.
        try:
            with path.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("The saved model differs from this job") from None

    async def get(self, tenant, job_id):
        return self.path(tenant, job_id).read_bytes()
