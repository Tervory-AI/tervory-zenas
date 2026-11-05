"""Validation and room-key authentication for private Rig brain tasks."""
import hmac
import os
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    task_id: uuid.UUID
    home_region: str
    question: str = Field(min_length=1, max_length=8000)


def authorize(key: str, region: str) -> None:
    expected = os.environ.get("TANK_KEY", "")
    if not expected or not key or not hmac.compare_digest(expected.encode(), key.encode()):
        raise HTTPException(401, "Rig room key required.")
    if region != "us-east" or region != os.environ.get("TANK_REGION", "us-east"):
        raise HTTPException(403, "This pilot stays in its home region.")
