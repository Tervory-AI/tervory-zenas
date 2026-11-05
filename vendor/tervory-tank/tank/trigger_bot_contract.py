"""Typed B1 tool/public payloads; no runtime imports or external side effects."""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from trigger_bot_grounding import exact_origin


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SourceAdd(Args):
    kind: Literal['website', 'rig', 'document']
    title: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=2000)
    attachment_name: str | None = Field(default=None, max_length=200)
    document_draft_id: UUID | None = None
    expected_digest: str = ''

    @model_validator(mode='after')
    def source(self):
        if self.kind == 'website':
            parsed = urlsplit(self.url or '')
            if not exact_origin(f'{parsed.scheme}://{parsed.netloc}'):
                raise ValueError('https_source_required')
        elif self.url is not None:
            raise ValueError('unexpected_url')
        if self.kind == 'document' and not self.attachment_name:
            raise ValueError('attachment_required')
        return self


class SourceRemove(Args):
    source_id: UUID


class Contact(Args):
    name: str = Field(min_length=1, max_length=100)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=50)

    @model_validator(mode='after')
    def validate_contact(self):
        if self.email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', self.email):
            raise ValueError('email')
        if self.phone and not re.fullmatch(r'[+()\d .-]{3,50}', self.phone):
            raise ValueError('phone')
        if not (self.email or self.phone):
            raise ValueError('contact_required')
        return self


class Ask(Args):
    text: str = Field(min_length=1, max_length=2000)
    contact: Contact | None = None
    quote_product_id: str | None = Field(default=None, max_length=64, pattern=r'^[A-Za-z0-9._-]+$')
    quote_title: str | None = Field(default=None, max_length=200)
    quote_quantity: int | None = Field(default=None, ge=1, le=100000)
    notice_version: str | None = Field(default=None, max_length=64)


class PublicMessage(Ask):
    conversation_id: UUID | None = None
    visitor_id: UUID | None = None

    @model_validator(mode='after')
    def existing(self):
        if self.conversation_id and not self.visitor_id:
            raise ValueError('visitor_required')
        return self


class FAQ(Args):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=8000)


class Feedback(Args):
    message_id: UUID
    status: Literal['good', 'bad']
    correct_answer: str | None = Field(default=None, max_length=8000)


class ConfirmDraft(Args):
    draft_id: UUID
    expected_digest: str = ''
    brief_markdown: str = Field(default='', max_length=16000)


TOOLS = {'source.add': SourceAdd, 'source.remove': SourceRemove, 'sources': Args,
         'ask': Ask, 'faq.add': FAQ, 'feedback': Feedback, 'review': Args, 'stats': Args,
         'lead.confirm': ConfirmDraft, 'quote.confirm': ConfirmDraft}


# B2 store controls retain the existing durable Command confirmation corridor.
from trigger_console_contract import ConversationAction, Settings
TOOLS.update({'settings': Settings, 'handover': ConversationAction, 'reply': ConversationAction})
