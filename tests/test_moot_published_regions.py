"""Exercise the real public route and reader with each box's local DB transport."""
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest

from agents.zenas import app as room

MATCH = UUID('b88613ae-7f0a-49e7-bdb5-28620f283041')
PLATFORM = UUID('00000000-0000-0000-0000-000000000001')


class LocalPublishedPool:
    def __init__(self, region):
        self.region = region
        self.reads = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, query, *args):
        self.reads.append((query, args))
        assert 'FROM agent_zenas.moot_matches m' in query
        assert 'JOIN agent_zenas.moot_problems p' in query
        assert 'm.region = $2' in query
        assert 'm.published = true' in query
        assert args == (PLATFORM, self.region, MATCH)
        return [{
            'id': MATCH, 'problem_id': 'local-civil-appeal', 'region': self.region,
            'title': 'Local civil appeal', 'body': {'region': self.region},
            'mode': 'student_student', 'published': True, 'fairness': {}, 'runs': [],
        }]


@pytest.mark.asyncio
@pytest.mark.parametrize('box', ['us-east', 'za', 'eu-west', ''])
@pytest.mark.parametrize('requested', ['us-east', 'za', 'eu-west', ''])
async def test_only_known_matching_region_reads_local_published_tables(monkeypatch, box, requested):
    monkeypatch.setenv('TANK_REGION', box)
    local = LocalPublishedPool(box)
    monkeypatch.setattr(room.app.state, 'pool', local, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room.app), base_url='http://room') as client:
        response = await client.get('/moot/published', params={'home_region': requested, 'match_id': str(MATCH)})
    if requested in {'us-east', 'za'} and requested == box:
        assert response.status_code == 200
        assert response.json()['matches'][0]['region'] == box
        assert response.json()['matches'][0]['matchId'] == str(MATCH)
        assert len(local.reads) == 1
    else:
        assert response.status_code == 403
        assert response.json() == {'detail': 'region mismatch'}
        assert local.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize('box', ['us-east', 'za'])
async def test_unpublished_hearing_never_reaches_public_response(monkeypatch, box):
    monkeypatch.setenv('TANK_REGION', box)

    class UnpublishedPool(LocalPublishedPool):
        async def fetch(self, query, *args):
            rows = await super().fetch(query, *args)
            # Even an unexpectedly returned private row is filtered by the
            # existing public projection, in addition to the SQL predicate.
            rows[0]['published'] = False
            return rows

    local = UnpublishedPool(box)
    monkeypatch.setattr(room.app.state, 'pool', local, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room.app), base_url='http://room') as client:
        response = await client.get('/moot/published', params={'home_region': box, 'match_id': str(MATCH)})
    assert response.status_code == 200
    assert response.json() == {'matches': []}
    assert 'Local civil appeal' not in response.text
