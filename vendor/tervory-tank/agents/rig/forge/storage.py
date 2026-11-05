"""Rig-only R2 access. Inputs/outputs are tenant/job keys, never caller-supplied URLs."""
from __future__ import annotations

import os
import re
from pathlib import Path

import boto3
from botocore.config import Config


class Storage:
    def __init__(self, tenant: str, job: str, client=None):
        self.prefix = f"forge/{tenant}/{job}/"
        if client is None:
            account = os.environ.get("R2_ACCOUNT_ID", "")
            key, secret = os.environ.get("R2_ACCESS_KEY_ID_RIG", ""), os.environ.get("R2_SECRET_ACCESS_KEY_RIG", "")
            if not re.fullmatch(r"[a-f0-9]{32}", account) or not key or not secret:
                raise ValueError("Rig R2 storage is not configured.")
            client = boto3.client("s3", endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
                aws_access_key_id=key, aws_secret_access_key=secret, region_name="auto",
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                              connect_timeout=10, read_timeout=120, retries={"max_attempts": 2},
                              proxies={"https": "http://rig-forge-egress:8080"}))
        self.client = client

    def key(self, relative: str) -> str:
        if not re.fullmatch(r"(?:inputs|gpu|drafts)/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", relative):
            raise ValueError("Invalid job object name.")
        return self.prefix + relative

    def transfers(self, manifest: dict) -> dict:
        """Mint method/object-limited capabilities, never send a bucket key to HF.

        GET and PUT both last the job timeout plus five minutes (at most 65
        minutes), so a job that waits in the provider queue can still read its
        photos instead of failing after paying for start-up. Do not persist/log this.
        """
        timeout = manifest["timeout_seconds"]
        if not 60 <= timeout <= 3600:
            raise ValueError("Invalid transfer timeout.")
        def signed(operation, name, expires, **params):
            return self.client.generate_presigned_url(operation,
                Params={"Bucket": "tervory-rig", "Key": self.key(name), **params},
                ExpiresIn=expires, HttpMethod="GET" if operation == "get_object" else "PUT")
        return {"manifest": manifest,
                "get": {name: signed("get_object", "inputs/" + name, timeout + 300)
                        for name in manifest["inputs"]},
                "put": {name: signed("put_object", "gpu/" + name, timeout + 300,
                                    Metadata={"approval": "draft"})
                        for name in ("result.glb", "source.zip")}}

    def download(self, name: str, destination: Path, limit: int = 256_000_000):
        response = self.client.get_object(Bucket="tervory-rig", Key=self.key(name))
        stream = response["Body"]
        try:
            if response["ContentLength"] > limit:
                raise ValueError("Input exceeds the ingestion budget.")
            total = 0
            with destination.open("xb") as output:
                while chunk := stream.read(1_048_576):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("Input exceeds the ingestion budget.")
                    output.write(chunk)
        finally:
            stream.close()

    def upload(self, name: str, source: Path):
        with source.open("rb") as body:
            self.client.put_object(Bucket="tervory-rig", Key=self.key(name), Body=body,
                ContentType="model/gltf-binary" if source.suffix == ".glb" else "application/json",
                Metadata={"approval": "draft"})
