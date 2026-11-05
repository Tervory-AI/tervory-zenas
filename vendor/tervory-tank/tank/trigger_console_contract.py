"""Typed, confirmation-bound operations for the approved Trigger console."""
from __future__ import annotations

from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    name: str = Field(min_length=1, max_length=200)
    opening_hours: str = Field(default='', max_length=2000)
    time_zone: str = Field(min_length=1, max_length=100)
    greeting: str = Field(default='', max_length=2000)
    handover_rules: str = Field(default='', max_length=2000)
    no_source_message: str = Field(default='', max_length=2000)
    handover_message: str = Field(default='', max_length=2000)
    privacy_notice: str = Field(default='', max_length=4000)
    notice_version: str = Field(default='', max_length=64)
    accent: str = Field(default='#001840', pattern=r'^#[0-9a-fA-F]{6}$')
    ink: str = Field(default='#1a1a1a', pattern=r'^#[0-9a-fA-F]{6}$')
    paper: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    line: str = Field(default='#7a7a85', pattern=r'^#[0-9a-fA-F]{6}$')
    expected_digest: str = ''
    brief_markdown: str = Field(default='', max_length=25000)

    @model_validator(mode='after')
    def timezone(self):
        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('time_zone') from None
        return self


class ConversationAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: UUID
    text: str = Field(default='', max_length=2000)
    person: str = Field(default='', max_length=200)
    expected_digest: str = ''
    brief_markdown: str = Field(default='', max_length=25000)
