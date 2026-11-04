import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from agents.zenas.core.store import MemoryStore
from agents.zenas.core.street_law import Actor, Refused, StreetLaw
from agents.zenas.core.matters import CURRENT_WORDING_VERSION

TENANT = '11111111-1111-4111-8111-111111111111'


class Matters(MemoryStore):
    async def set_document_state(self, tenant_id, document_id, state):
        document = await super().set_document_state(tenant_id, document_id, state)
        if document is not None and state == 'released':
            document['reviewed'] = True
        return document

    async def lock_document(self, tenant_id, document_id):
        return await self.get_document(tenant_id, document_id) is not None


class Sessions:
    def __init__(self, matters):
        self.rows, self.lock, self.matters = {}, asyncio.Lock(), matters

    async def create(self, tenant_id, row):
        self.rows[tenant_id, row['id']] = deepcopy(row)

    @asynccontextmanager
    async def edit(self, tenant_id, session_id):
        async with self.lock:
            if (tenant_id, session_id) not in self.rows:
                raise Refused('not found')
            value = deepcopy(self.rows[tenant_id, session_id])
            before = deepcopy(self.matters.__dict__)
            try:
                yield value, self.matters
                self.rows[tenant_id, session_id] = value
            except BaseException:
                self.matters.__dict__.update(before)
                raise


@pytest_asyncio.fixture
async def clinic():
    matters = Matters()
    await matters.register_tenant(TENANT, 'za')
    actor = Actor(TENANT, 'placeholder-founder', True)
    await matters.add_consent({'tenant_id': TENANT, 'user_id': actor.user_id,
                              'wording_version': CURRENT_WORDING_VERSION})
    chunk = SimpleNamespace(document_id='placeholder-law', version_id='placeholder-version', jurisdiction='za',
        citation='Placeholder Rental Act section 1', text='The rental deposit must be returned with the interest owing to the tenant.')
    law = SimpleNamespace(search_text=AsyncMock(return_value=[chunk]), source_metadata=AsyncMock(return_value={
        ('placeholder-law', 'placeholder-version'): {'url': 'https://www.gov.za/documents/placeholder-act',
          'version_date': '2026-10-01', 'attribution': 'Official court or legislative public record'}}))
    sessions = Sessions(matters)
    service = StreetLaw(sessions, matters, law, box_region='za')
    session = await service.create(actor, 'za', 'Placeholder Street Law day')
    return SimpleNamespace(service=service, sessions=sessions, matters=matters, actor=actor,
                           session=session['id'], law=law, chunk=chunk)
