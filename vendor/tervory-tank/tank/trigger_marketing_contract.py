"""M1 command payloads and code-only scheduling validation; no runtime imports."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

Channel = Literal['gbp', 'facebook', 'instagram', 'linkedin', 'x']
FORMATS = {'gbp': 'Google Business Profile post', 'facebook': 'Facebook post',
           'instagram': 'Instagram caption', 'linkedin': 'LinkedIn post', 'x': 'X post'}


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Create(Args):
    channel: Channel
    write_id: UUID | None = None
    text: str | None = Field(default=None, min_length=1, max_length=20000)

    @model_validator(mode='after')
    def source(self):
        if (self.write_id is None) == (self.text is None):
            raise ValueError('source')
        return self


class Post(Args):
    post_id: UUID
    connection_generation: UUID | None = None
    location: str | None = Field(default=None, max_length=200)
    expected_revision: int = Field(default=0, ge=0)
    brief_markdown: str = Field(default='', max_length=25000)


class Edit(Post):
    text: str = Field(min_length=1, max_length=20000)


class Schedule(Post):
    planned_at: datetime  # Explicit UTC offset disambiguates a repeated DST hour.
    time_zone: str = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def zone(self):
        try:
            zone = ZoneInfo(self.time_zone)
        except ZoneInfoNotFoundError:
            raise ValueError('time_zone') from None
        if self.planned_at.utcoffset() is None:
            raise ValueError('offset_required')
        local = self.planned_at.astimezone(zone)
        if local.replace(tzinfo=None) != self.planned_at.replace(tzinfo=None) or local.utcoffset() != self.planned_at.utcoffset():
            raise ValueError('zone_offset_mismatch')
        return self


class Posts(Args):
    state: Literal['draft', 'approved', 'scheduled', 'published', 'failed', 'cancelled'] | None = None
    channel: Channel | None = None
    start: date | None = None
    end: date | None = None


class Calendar(Args):
    start: date
    period: Literal['week', 'month']


class Location(Args):
    location: str = Field(pattern=r'^accounts/[0-9]+/locations/[0-9]+$', max_length=200)
    generation: UUID | None = None
    brief_markdown: str = Field(default='', max_length=25000)


class Page(Args):
    page_id: str = Field(pattern=r'^[0-9]{1,50}$')
    generation: UUID | None = None
    brief_markdown: str = Field(default='', max_length=25000)


class Review(Args):
    review_id: str = Field(pattern=r'^[A-Za-z0-9_-]+$', max_length=200)


class Reply(Args):
    reply_id: UUID
    expected_revision: int = Field(default=0, ge=0)
    brief_markdown: str = Field(default='', max_length=25000)


class ReplyEdit(Reply):
    text: str = Field(min_length=1, max_length=20000)


class Disconnect(Args):
    generation: UUID | None = None
    brief_markdown: str = Field(default='', max_length=25000)


TOOLS = {'post.create': Create, 'post.edit': Edit, 'post.approve': Post, 'post.schedule': Schedule,
         'post.cancel': Post, 'post.retry': Post, 'posts': Posts, 'calendar': Calendar,
         'gbp.connect': Args, 'gbp.locations': Args, 'gbp.location.select': Location,
         'gbp.disconnect': Disconnect, 'gbp.reviews': Args, 'gbp.reply.draft': Review,
         'gbp.reply.edit': ReplyEdit, 'gbp.reply.send': Reply,
         'meta.connect': Args, 'meta.pages': Args, 'meta.page.select': Page, 'meta.disconnect': Disconnect}


def schedulable(report: dict, channel: str) -> bool:
    length = report.get('length', {})
    if (report.get('complete') is not True or report.get('format') != FORMATS[channel]
            or 'limit' not in length or not isinstance(length.get('count'), int)):
        return False
    limit = length['limit']
    # Founder 3 Nov: None is intentional. Google's rejection is authoritative;
    # never invent 1500 or retry a terminal too-long error with the same text.
    return limit is None or (isinstance(limit, int) and length['count'] <= limit)


def utc_now():
    return datetime.now(UTC)
