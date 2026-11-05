"""Tenant voice format adapted from Brand Voice Spec; see ATTRIBUTION.md."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).parent
BOOKS = frozenset(p.name for p in (ROOT / "sources/stylebooks").iterdir() if p.is_dir())


class Voice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand: str = Field(default="", max_length=200)
    status: Literal["live"]
    revision: int = Field(ge=1)
    registers: dict[str, str] = Field(default_factory=dict)
    tone: list[str] = Field(default_factory=list)
    guidelines: list[dict[str, Any]] = Field(default_factory=list)
    doAndDont: list[dict[str, Any]] = Field(default_factory=list)
    surfaceDoDont: list[dict[str, Any]] = Field(default_factory=list)
    promptKit: list[dict[str, Any]] = Field(default_factory=list)
    bannedTerms: list[str] = Field(default_factory=list, max_length=200)
    preferredTerms: list[str] = Field(default_factory=list, max_length=200)
    vocabulary: dict[str, Any] = Field(default_factory=dict)
    signaturePhrases: list[str] = Field(default_factory=list)
    changelog: list[dict[str, Any]] = Field(default_factory=list)
    voiceQueue: list[dict[str, Any]] = Field(default_factory=list)
    # Explicit extensions: preferred does not mean required.
    requiredTerms: list[str] = Field(default_factory=list, max_length=200)
    stylebook: str | None = None
    maxSentenceWords: int = Field(default=30, ge=5, le=100)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json()) > 40_000:
            raise ValueError("Voice must fit in 40,000 characters")
        if self.stylebook is not None and self.stylebook not in BOOKS:
            raise ValueError("Unknown stylebook")
        terms = self.bannedTerms + self.requiredTerms + self.preferredTerms
        if any(not term.strip() or len(term) > 200 for term in terms):
            raise ValueError("Voice terms must be nonempty and at most 200 characters")
        def normalized(terms):
            return {" ".join(term.split()).casefold() for term in terms}
        if normalized(self.bannedTerms) & normalized(self.requiredTerms):
            raise ValueError("A term cannot be both banned and required")
        if any(not isinstance(g.get("rule"), str) for g in self.guidelines):
            raise ValueError("Every guideline must contain a rule")
        return self


class VoiceStore:
    def __init__(self, pool):
        self.pool = pool

    async def get(self, tenant: UUID) -> Voice | None:
        raw = await self.pool.fetchval(
            "SELECT voice FROM agent_trigger.voices WHERE tenant_id=$1", tenant)
        return Voice.model_validate(json.loads(raw) if isinstance(raw, str) else raw) if raw else None

    async def put(self, tenant: UUID, voice: Voice) -> None:
        # Reject stale revisions; a later writer cannot overwrite a newer voice.
        changed = await self.pool.fetchval(
            "INSERT INTO agent_trigger.voices(tenant_id,voice,revision) VALUES($1,$2::jsonb,$3) "
            "ON CONFLICT(tenant_id) DO UPDATE SET voice=EXCLUDED.voice,revision=EXCLUDED.revision,updated_at=now() "
            "WHERE agent_trigger.voices.revision < EXCLUDED.revision RETURNING revision",
            tenant, voice.model_dump_json(), voice.revision)
        if changed is None:
            raise ValueError("Voice revision must increase")
