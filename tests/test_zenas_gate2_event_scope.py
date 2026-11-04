"""Only the two explicit consumer/event pairs can access tenant streams."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from event_bus import Bus, EVENTS, Scope

TENANT = UUID('71f837b8-60a2-4364-b222-29415e5a43d3')
ALLOWED = {('rig', 'tiger.price.changed'), ('radix', 'zenas.regulation.changed')}


@pytest.mark.asyncio
@pytest.mark.parametrize('agent', sorted(set(EVENTS.values()) | {'unlisted'}))
@pytest.mark.parametrize('event', sorted(set(EVENTS) | {'unlisted.event'}))
async def test_consumer_scope_is_exact(agent, event):
    redis = SimpleNamespace(
        xgroup_create=AsyncMock(),
        xautoclaim=AsyncMock(return_value=('0-0', [])),
        xreadgroup=AsyncMock(return_value=[]),
        eval=AsyncMock(return_value=1),
    )
    bus = Bus(redis)
    scope = Scope(TENANT, agent, 'za')
    if (agent, event) in ALLOWED:
        assert await bus.receive(scope, event, 'worker') == []
        redis.xgroup_create.assert_awaited_once_with(scope.stream(event), agent, id='0-0', mkstream=True)
        assert await bus.settle(scope, event, 'worker', '1-0', 'ack') == 1
        with pytest.raises(PermissionError):
            await bus.settle(scope, event, 'worker', '1-0', 'invalid')
    else:
        with pytest.raises(PermissionError):
            await bus.receive(scope, event, 'worker')
        with pytest.raises(PermissionError):
            await bus.settle(scope, event, 'worker', '1-0', 'ack')
        redis.xgroup_create.assert_not_awaited()
        redis.eval.assert_not_awaited()
