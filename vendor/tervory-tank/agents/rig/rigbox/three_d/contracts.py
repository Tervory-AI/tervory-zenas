"""Versioned boundary shared by the forge adapter and its explicit stand-in."""
from __future__ import annotations

import math
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_BYTES = 5_000_000
MAX_TRIANGLES = 100_000
NAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Anchor(Strict):
    position_mm: tuple[float, float, float]
    quaternion: tuple[float, float, float, float]

    @field_validator("position_mm")
    @classmethod
    def bounded(cls, value):
        if any(abs(n) > 100_000 for n in value):
            raise ValueError("Anchor is outside the supported dimensions")
        return value

    @field_validator("quaternion")
    @classmethod
    def normalised(cls, value):
        if not math.isclose(sum(n * n for n in value), 1, abs_tol=0.001):
            raise ValueError("Anchor orientation must be a unit quaternion")
        return value


class Job(Strict):
    contract: Literal["rig-forge/1"] = "rig-forge/1"
    id: UUID
    tenant_id: UUID
    product_id: str = Field(pattern=NAME)
    source: Literal["ai", "scan"]
    # Bucket-relative names, never URLs, credentials or caller-selected buckets.
    inputs: list[str] = Field(min_length=1, max_length=70)
    dimensions_mm: tuple[float, float, float]
    anchors: dict[str, Anchor] = Field(default_factory=dict, max_length=32)
    max_bytes: int = Field(default=MAX_BYTES, ge=1024, le=MAX_BYTES)
    max_triangles: int = Field(default=MAX_TRIANGLES, ge=12, le=MAX_TRIANGLES)
    cost_cap_usd: float = Field(ge=0, le=100)
    timeout_seconds: int = Field(default=900, ge=60, le=3600)

    @model_validator(mode="after")
    def valid_source(self):
        prefix = f"{self.tenant_id}/uploads/"
        suffixes = {"jpg", "jpeg", "png"}
        if self.source == "scan" and not 50 <= len(self.inputs) <= 70:
            raise ValueError("A scan needs 50 to 70 overlapping photos")
        if self.source == "ai" and len(self.inputs) > 4:
            raise ValueError("Choose up to four photos")
        if len(set(self.inputs)) != len(self.inputs):
            raise ValueError("Choose different input files")
        for name in self.inputs:
            if (not name.startswith(prefix) or len(name) > 300
                    or "/" in name[len(prefix):]
                    or any(not re.fullmatch(NAME, p) for p in name.split("/"))
                    or name.rsplit(".", 1)[-1].lower() not in suffixes):
                raise ValueError("Inputs must be this tenant's uploaded files")
        if any(not re.fullmatch(NAME, name) for name in self.anchors):
            raise ValueError("Use simple names for attachment points")
        if any(n <= 0 or n > 100_000 for n in self.dimensions_mm):
            raise ValueError("Supply the real dimensions in millimetres")
        return self


class Report(Strict):
    outward_faces: bool
    watertight: bool
    real_size: bool
    gltf_valid: bool
    triangles: int = Field(gt=0)
    size_bytes: int = Field(gt=0)
    dimensions_mm: tuple[float, float, float]
    compression: Literal["meshopt"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    anchors: dict[str, Anchor]
    receipt_ids: list[UUID] = Field(min_length=1)

    @field_validator("anchors")
    @classmethod
    def named(cls, value):
        if not value or len(value) > 32 or any(not re.fullmatch(NAME, n) for n in value):
            raise ValueError("Name between one and 32 anchors")
        return value

    def rejection(self, job: Job) -> str | None:
        for check, reason in [(self.outward_faces, "Faces point inward"),
                              (self.watertight, "The model has open holes"),
                              (self.real_size, "Real dimensions were not verified"),
                              (self.gltf_valid, "The GLB did not pass validation")]:
            if not check:
                return reason
        if self.triangles > job.max_triangles or self.size_bytes > job.max_bytes:
            return "The model exceeds its size budget"
        if any(not math.isclose(a, b, rel_tol=0.01, abs_tol=0.1)
               for a, b in zip(self.dimensions_mm, job.dimensions_mm)):
            return "The dimensions do not match the requested millimetres"
        return None


class Result(Strict):
    contract: Literal["rig-forge/1"] = "rig-forge/1"
    id: UUID
    tenant_id: UUID
    status: Literal["queued", "running", "failed", "draft"]
    report: Report | None = None
    # An opaque code from a fixed vocabulary, never an upstream error message.
    failure: Literal["conversion", "budget", "timeout", "validation", "unavailable"] | None = None


class Review(Strict):
    scale: Literal[True]
    appearance: Literal[True]
    attachment_points: Literal[True]


class Assembly(Strict):
    product_id: str = Field(pattern=NAME)
    tier: str = Field(default="complete", pattern=NAME)
    choices: dict[str, str] = Field(default_factory=dict, max_length=12)
