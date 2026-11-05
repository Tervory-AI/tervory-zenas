"""Shared wire job; room rows retain their existing columns and JSON input."""
from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    region: Literal["us-east", "za"]
    agent: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    skill: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,79}$")
    input: dict[str, Any] = Field(default_factory=dict)
    priority: Literal["interactive", "standard", "batch"] = "standard"
    engine: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{0,79}$")

    @classmethod
    def from_room(cls, row: Any, *, agent: str, region: str, skill_column: str = "kind") -> Job:
        payload = row["input"]
        payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
        metadata = payload.pop("_job_contract", {})
        if not isinstance(metadata, dict) or metadata.get("region", region) != region:
            raise ValueError("Invalid room job region or metadata")
        # Tenant and agent authority always comes from the room row/context.
        return cls(tenant_id=row["tenant_id"], agent=agent, region=region,
                   skill=row[skill_column], input=payload,
                   priority=metadata.get("priority", "standard"),
                   engine=metadata.get("engine", payload.get("engine")))

    def to_room(self, *, skill_column: str = "kind") -> dict[str, Any]:
        return {"tenant_id": self.tenant_id, skill_column: self.skill,
                "input": {**self.input, "_job_contract": {
                    "region": self.region, "priority": self.priority, "engine": self.engine}}}
