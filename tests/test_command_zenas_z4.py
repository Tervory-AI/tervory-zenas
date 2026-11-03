"""Watch cards expose room refusals and keep infrastructure failures generic."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

import command
from agents.zenas.core.scope_rules import REFUSED
from test_command import TENANT, runtime as runtime

GENERIC = "The action was refused or failed. It has not been retried."
WATCH_TOOLS = [
    ("zenas.watch.add", {"source": "federal-register", "topic": "criminal sentencing"}),
    ("zenas.watch.list", {}),
    ("zenas.watch.enable", {"watch_id": str(UUID(int=1))}),
    ("zenas.watch.disable", {"watch_id": str(UUID(int=1))}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,arguments", WATCH_TOOLS)
@pytest.mark.parametrize("status,detail,expected", [
    (403, REFUSED, REFUSED),
    (404, "Not in scope.", "Not in scope."),
    (422, "Not in scope.", "Not in scope."),
    (409, "TANK_REGION must be us-east or za", GENERIC),
    (401, "room key required", GENERIC),
    (503, "Zenas room is unreachable", GENERIC),
    (403, {"placeholder": "not a string"}, GENERIC),
    (404, None, GENERIC),
    (422, ["placeholder"], GENERIC),
])
async def test_watch_card_exposes_only_string_room_refusals(runtime, monkeypatch, tool, arguments, status, detail, expected):
    actor = command.Actor(TENANT, "founder", True)
    card_id = "placeholder-card"
    card = {"card_id": card_id, "summary": tool, "tool": tool, "status": "executing"}
    state = {"cards": {card_id: card}}
    row = {"tenant_id": TENANT, "user_id": actor.user_id, "state": state}
    conn = SimpleNamespace()

    @asynccontextmanager
    async def editing(thread_id, current):
        assert current == actor
        yield conn, row, state

    @asynccontextmanager
    async def acquire():
        yield conn

    monkeypatch.setattr(command, "authorized_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(command, "editing", editing)
    monkeypatch.setattr(command, "db", lambda: SimpleNamespace(acquire=acquire))
    monkeypatch.setattr(command, "owned", AsyncMock(return_value=row))
    monkeypatch.setattr(command, "step", AsyncMock())
    events = AsyncMock()
    monkeypatch.setattr(command, "event", events)
    monkeypatch.setattr(command, "refresh_action_receipts", AsyncMock())
    dispatch = AsyncMock(return_value=(status, {"detail": detail}))
    monkeypatch.setattr(command, "dispatch_action", dispatch)

    result = await command.execute_card(uuid4(), actor, card_id,
                                        command.PlannedAction(tool=tool, arguments=arguments))
    dispatch.assert_awaited_once()
    assert result["status"] == "failed"
    assert result["error"] == expected
    assert "result" not in result
    emitted_card = next(call.args[3] for call in events.await_args_list if call.args[2] == "card")
    assert emitted_card["error"] == expected
    if expected == GENERIC and isinstance(detail, str):
        assert detail not in command.dump(result)
        assert detail not in command.dump(emitted_card)
