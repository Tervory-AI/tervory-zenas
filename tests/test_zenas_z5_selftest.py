"""Z5: fake gateways and library responses, no external network or model."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from agents.zenas.core import selftest
from agents.zenas.core.answer_failures import exception_failure, safe_failure
from agents.zenas.core.ladder_store import LadderMemory
from agents.zenas.core.service import answer_question
from agents.zenas.pool.adapters import laws_africa_kb as kb

QUOTE = "The placeholder supply agreement binds both parties to its written terms."
SECOND = "The placeholder buyer must perform every obligation under the written agreement."
CITE = "[2026] ZACAC 1"
PASSAGE = {"doc_id": "placeholder-doc", "version_id": "placeholder-version", "citation": CITE,
           "title": "Placeholder Buyer v Placeholder Seller", "text": "[1] " + QUOTE + " [2] " + SECOND,
           "status": "effective"}


def library():
    return [{"name": f"Placeholder Buyer {i} v Placeholder Seller {i}",
             "citation": f"[2026] ZACAC {i + 1}", "text": PASSAGE["text"]} for i in range(10)]


class Gateway:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        prompt = messages[0]["content"]
        passage = json.JSONDecoder().raw_decode(prompt.split("Passages:\n", 1)[1])[0][0]
        citation = passage["citation"]
        if "Fictional Party" in prompt:
            citation = "[2099] ZASCA 9000"
        return {"content": json.dumps({"elements": [{"id": "placeholder-holding", "test": QUOTE,
                "quote": QUOTE, "citation": citation}], "decision_table": [
                {"element_id": "placeholder-holding", "when": {"asked": "yes"}, "finding": "met"}]}),
                "cost_usd": 0, "model": "placeholder-model"}


async def answer(model, *, passages=None, pool=None, question=None):
    ladder = LadderMemory()
    await ladder.register_tenant("placeholder-tenant", "za")
    return await answer_question(ladder, ladder, model, tenant_id="placeholder-tenant", home_region="za",
        box_region="za", jurisdiction="za", provider="za-local", features={"asked": "yes"},
        question=question or f"What did {CITE} hold about the placeholder supply agreement?",
        passages=[PASSAGE] if passages is None else passages, pool=pool)


@pytest.mark.parametrize("error,status", [
    (RuntimeError("placeholder gateway failure"), None),
    (RuntimeError("tank answered 503"), 503),
    (RuntimeError("tank answered 409"), 409),
    (RuntimeError("tank answered 402"), 402),
    (httpx.ReadError("placeholder private response body"), None),
    (httpx.ReadTimeout("placeholder private prompt"), None),
])
def test_gateway_failures_return_unavailable_with_safe_metadata(error, status):
    model = Gateway(error)
    result = asyncio.run(answer(model))
    assert result["ok"] is False and result["reason"] == "unavailable"
    assert result["delivered"] is False and model.calls == 1
    failure = result["failure"]
    assert failure["exception"] == type(error).__name__
    assert failure["file"] == "tests/test_zenas_z5_selftest.py" and failure["line"] > 0
    assert failure.get("http_status") == status
    assert "private" not in json.dumps(failure) and "prompt" not in json.dumps(failure)


def test_structured_paid_refusal_keeps_only_known_code():
    class Refused:
        async def complete(self, *args, **kwargs):
            return {"ok": False, "code": "paid_work_paused", "http_status": 409,
                    "reason": "placeholder private body", "headers": {"placeholder": "private"}}
    result = asyncio.run(answer(Refused()))
    assert result["reason"] == "unavailable"
    assert result["failure"] == {"http_status": 409, "refusal_code": "paid_work_paused"}
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("error", [httpx.ReadError("placeholder body"), TimeoutError("placeholder prompt")])
def test_kb_failure_retains_library_and_marks_sources(monkeypatch, error):
    async def retrieve(self, *args, **kwargs):
        raise error
    monkeypatch.setattr(kb.KnowledgeBaseClient, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(kb.KnowledgeBaseClient, "retrieve", retrieve)

    class Pool:
        async def find_by_citation(self, citation, **kwargs):
            assert citation == CITE
            return [SimpleNamespace(chunk_id="placeholder-chunk", document_id=PASSAGE["doc_id"],
                version_id=PASSAGE["version_id"], citation=CITE, heading="", path="[1]",
                text=PASSAGE["text"], jurisdiction="za")]
        async def search_text(self, *args, **kwargs):
            return []
    result = asyncio.run(answer(Gateway(), passages=[], pool=Pool(),
        question=f"What did [Party A] 270.CAC .Apr25. Judgment {CITE} hold in this placeholder commercial dispute?"))
    assert result["ok"], result
    source = result["memo"]["sources"][0]
    assert source["retrieval_fallback"] == "library"
    assert source["retrieval_failure"]["exception"] == type(error).__name__
    assert f"“{QUOTE}” ({CITE} [1])" in result["memo"]["text"]


def test_selection_is_civil_unique_and_quotable():
    docs = library()
    docs[:0] = [dict(docs[0]), {**docs[0], "name": "Placeholder conviction", "citation": "placeholder-criminal",
                "text": "The placeholder criminal conviction was affirmed after the sentencing hearing."},
                {"name": "Placeholder header", "citation": "placeholder-header",
                 "text": "Budlender SC (Tribunal Member) : A."},
                {"name": "Placeholder spacing", "citation": "placeholder-spacing",
                 "text": "The placeholder court m ade an order against the supplier."}]
    rows = selftest.made_up_questions(docs)
    real = [row for row in rows if row["kind"] == "real"]
    assert len(rows) == 50 and len(real) == 10
    assert len({row["citation"] for row in real}) == 10
    assert all(selftest._sentence_ok(row["quote"]) for row in real)
    assert not any("placeholder-criminal" == row["citation"] for row in real)


def test_insufficient_distinct_cases_do_not_fake_fifty():
    result = asyncio.run(selftest.run_made_up_test(LadderMemory(), model=Gateway(), library=library()[:1]))
    assert result["path"] == "not run" and not result["skills_may_be_approved"]


def test_grading_accepts_any_body_sentence_without_requiring_a_model_quote():
    result = {"memo": {"text": f"{CITE}: “{SECOND}”"}}
    assert selftest.supported_quotes(result, CITE, PASSAGE["text"]) == [SECOND]
    item = {"kind": "real", "citation": CITE}
    assert not selftest._miss_reason(item, result["memo"]["text"], ok_result=True, raised="", quote=QUOTE,
                                    supported=selftest.supported_quotes(result, CITE, PASSAGE["text"]))
    assert selftest.supported_quotes({"memo": {"text": f"{CITE}: The buyer has contractual duties."}},
                                    CITE, PASSAGE["text"]) == []
    assert not selftest._sentence_ok("The appeal is dismissed with costs.")


def test_full_selftest_uses_fifty_distinct_questions_and_records_failed_calls():
    good = asyncio.run(selftest.run_made_up_test(LadderMemory(), model=Gateway(), library=library()))
    assert good["passed"] == good["asked"] == 50, good["misses"]
    failed = asyncio.run(selftest.run_made_up_test(LadderMemory(), model=Gateway(RuntimeError("tank answered 409")),
                                                 library=library()))
    assert not failed["skills_may_be_approved"]
    assert all(miss["failure"]["http_status"] == 409 for miss in failed["misses"])
    public = selftest._public_misses(failed["misses"])
    assert all(miss["failure"]["http_status"] == 409 for miss in public)


def test_diagnostics_reject_arbitrary_fields_and_messages():
    assert safe_failure({"http_status": 409, "refusal_code": "placeholder-private",
                         "body": "placeholder-body", "file": "../../placeholder.py", "line": 1}) == {"http_status": 409}
    try:
        raise RuntimeError("placeholder secret prompt header token body")
    except RuntimeError as exc:
        failure = exception_failure(exc)
    assert set(failure) == {"exception", "file", "line"}
