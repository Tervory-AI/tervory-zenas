"""Gate 1: official court text is split and queued without a model."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.zenas.core import judgment_training
from agents.zenas.core.judgment_split import outcome_label, split_judgment
from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.adapters.za_archive import ZA_ARCHIVE_SOURCES
from agents.zenas.pool.catalogue import _za_case_sources


HEADED = """FACTS
The placeholder supplier delivered machinery.
ISSUES
Whether the placeholder agreement was binding.
REASONING
The signed writing established agreement.
ORDER
The application is granted with costs.
"""


def test_headed_judgment_uses_only_court_sections_and_order_label():
    assert split_judgment("Placeholder civil matter", HEADED) == {
        "status": "split",
        "input": {
            "facts": "The placeholder supplier delivered machinery.",
            "issues": "Whether the placeholder agreement was binding.",
        },
        "output": {
            "reasoning": "The signed writing established agreement.",
            "order": "The application is granted with costs.",
            "outcome": "granted",
            "costs": "with costs",
        },
    }


def test_unheaded_and_criminal_judgments_are_excluded_without_guessing():
    assert split_judgment(
        "Placeholder civil matter", "A narrative without headings."
    ) == {"status": "excluded", "reason": "headings"}
    assert split_judgment("Placeholder criminal case", HEADED) == {
        "status": "excluded",
        "reason": "criminal",
    }
    assert split_judgment(
        "Placeholder labour dispute about criminal charges", HEADED
    ) == {"status": "excluded", "reason": "criminal"}


@pytest.mark.parametrize(
    ("order", "outcome", "costs"),
    [
        ("The appeal is upheld with costs.", "upheld", "with costs"),
        (
            "The application is dismissed. No order as to costs.",
            "dismissed",
            "without costs",
        ),
        ("The decision is set aside.", "set aside", "without costs"),
        ("The application is granted.", "granted", "without costs"),
        ("Leave is refused with costs.", "refused", "with costs"),
        ("The dispute is settled by agreement.", "settled", "without costs"),
    ],
)
def test_order_to_fixed_outcome_mapping(order, outcome, costs):
    assert outcome_label(order) == {"outcome": outcome, "costs": costs}


@pytest.mark.parametrize(
    "order", ["Placeholder order follows.", "The appeal is upheld and set aside."]
)
def test_unmapped_or_ambiguous_order_is_excluded(order):
    judgment = HEADED.replace("The application is granted with costs.", order)
    assert split_judgment("Placeholder civil matter", judgment) == {
        "status": "excluded",
        "reason": "order",
    }


def test_every_training_source_is_an_official_registered_full_text_adapter():
    sources = _za_case_sources()
    assert {source.source_id for source in sources} == set(ZA_ARCHIVE_SOURCES)
    assert all(
        source.store_full_text and source.adapter == source.source_id
        for source in sources
    )
    assert all(get_adapter(source).source_id == source.source_id for source in sources)
    assert "saflii" not in ZA_ARCHIVE_SOURCES
    assert not any("laws-africa" in source for source in ZA_ARCHIVE_SOURCES)


@pytest.mark.asyncio
async def test_nightly_split_path_calls_only_tank_court_text_writer(monkeypatch):
    row = {
        "source_id": "concourt",
        "document_id": "placeholder-document",
        "version_id": "placeholder-version",
        "title": "Placeholder civil matter",
        "text": HEADED,
    }
    conn = SimpleNamespace(execute=AsyncMock(), fetch=AsyncMock(return_value=[row]), fetchval=AsyncMock(return_value=True))
    requests = []

    class Response:
        status_code = 200

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response()

    monkeypatch.setenv("TANK_KEY", "placeholder-tank-key")
    monkeypatch.setattr(
        judgment_training.httpx, "AsyncClient", lambda **_kwargs: Client()
    )
    assert await judgment_training.run_scheduled(conn, region="za") == {
        "split": 1,
        "excluded": 0,
    }
    assert len(requests) == 1
    assert requests[0][0].endswith("/v1/training/zenas/court-text")
    assert requests[0][1]["json"]["output"]["outcome"] == "granted"
    assert "model" not in requests[0][0]
