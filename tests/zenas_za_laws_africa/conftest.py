import pytest

from agents.zenas.pool.adapters import laws_africa_api as api


@pytest.fixture
def clock_and_token(monkeypatch):
    waits = []

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(api.asyncio, "sleep", sleep)
    # Variable-name marker only; never an API credential.
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", "LAWS_AFRICA_API_TOKEN")
    monkeypatch.setattr(api, "laws_africa_token", lambda: "LAWS_AFRICA_API_TOKEN")
    return waits
