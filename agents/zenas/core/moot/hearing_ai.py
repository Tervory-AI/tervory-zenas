"""One explicit AI turn via existing Tank tasks, with durable revocation polling.

No worker/automatic model loop. A crash after durable 'started' is uncertain and
must NOT repeat the provider call on retry. Tank owns any actual provider receipt;
a locally cancelled HTTP await is not a promise of upstream billing cancellation.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import hearing
from .judge import flag_authorities
from agents.zenas.core.precedent import named_cases
from agents.zenas.core.engine import quote_in_source
from agents.zenas.pool.citations import extract_citations
from .reasoning import REASONING_INSTRUCTIONS, validate


# Reuse the installed pool extractor (eyecite plus regional/statutory forms).
_QUOTES = re.compile(r'"([^"\n]+)"|“([^”]+)”|‘([^’]+)’|(?<!\w)\'([^\'\n]+)\'(?!\w)|^\s*>\s*(.+)$', re.I | re.M)


def _ground_text(value: Any, authorities: list[dict[str, Any]], facts: str) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _ground_text(item, authorities, facts)
    elif isinstance(value, list):
        for item in value:
            _ground_text(item, authorities, facts)
    elif isinstance(value, str):
        normalize = lambda text: re.sub(r"[\s.]", "", text).casefold()
        known = {normalize(cite) for a in authorities for cite in extract_citations(a["name"])}
        if any(normalize(cite) not in known for cite in extract_citations(value)):
            raise hearing.HearingError("citation_check_failed")
        sources = [a.get("quote", "") for a in authorities] + [facts]
        for match in _QUOTES.finditer(value):
            quote = next(group for group in match.groups() if group is not None)
            if not any(quote_in_source(quote, source) for source in sources):
                raise hearing.HearingError("citation_check_failed")


def validate_turn(state: dict[str, Any], payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise hearing.HearingError("invalid_turn")
    problem = state["problem"]
    kind = payload.get("kind")
    fields = ("kind", "text", "target", "authority_ids", "order", "reasons", "responds_to", "issue_index", "decision")
    result = {k: payload[k] for k in fields if k in payload}
    if kind != "direction":
        result["reasoning"] = validate(problem, payload.get("reasoning"))
    question = state["pending_question"]
    if question and state["floor"] == question["target"] and payload.get("responds_to") != question["turn"]:
        raise hearing.HearingError("pending_question_not_addressed")
    _ground_text(result, problem["authorities"], problem["facts"])
    known = [a["name"] for a in problem["authorities"]]
    binding = [a["name"] for a in problem["authorities"] if a.get("weight") == "binding"]
    normalized = lambda value: " ".join(value.split()).casefold()
    aliases = {normalized(name) for name in known}
    aliases.update(normalized(name) for authority in known for name in named_cases(authority))
    if any(normalized(name) not in aliases for name in named_cases(json.dumps(result))):
        raise hearing.HearingError("citation_check_failed")
    flags = flag_authorities(json.dumps(result), library_names=known, binding_names=binding)
    if flags["made_up"] or flags["nonbinding_as_binding"]:
        raise hearing.HearingError("citation_check_failed")
    return result


class AIExecutor:
    def __init__(self, store: Any, tank: Any, *, poll_seconds: float = 0.1):
        self.store, self.tank, self.poll_seconds = store, tank, poll_seconds

    @staticmethod
    def _key(token: str, operation: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"tervory:moot:{token}:{operation}"))

    async def _watch(self, actor: hearing.Actor, hearing_id: str, token: str, revision: int):
        while True:
            current = await self.store.load(actor, hearing_id)
            pending = current["pending_ai"]
            if pending is None or pending["token"] != token or current["revision"] != revision:
                return current
            await asyncio.sleep(self.poll_seconds)

    async def _resolve(self, actor, hearing_id, token, reason, receipt=None):
        for _ in range(5):
            state = await self.store.load(actor, hearing_id)
            prior = next((a for a in state["ai_attempts"] if a["token"] == token and a.get("resolution") == reason), None)
            if prior and (receipt is None or any(r["ledger_id"] == receipt.get("ledger_id") for r in state["receipts"])):
                return state
            try:
                return await self.store.mutate(actor, hearing_id, self._key(token, "resolve:" + reason),
                    state["revision"], "ai_resolve", {"token": token, "reason": reason, "receipt": receipt},
                    lambda s, a, r, p: hearing.resolve_ai(s, a, r, p["token"], p["reason"], p["receipt"]))
            except hearing.HearingError as exc:
                if exc.code not in ("stale_revision", "idempotency_conflict"):
                    raise
        raise hearing.HearingError("stale_revision")

    async def run(self, actor: hearing.Actor, hearing_id: str, request_id: str, revision: int) -> dict[str, Any]:
        token = self._key(request_id, hearing_id)
        claimed = await self.store.commit(actor, hearing_id, request_id, revision, "ai_claim", {"token": token},
            lambda s, a, r, p: hearing.claim_ai(s, a, r, p["token"]))
        state = claimed.state
        pending = state["pending_ai"]
        if pending is None or pending["token"] != token or pending["status"] != "claimed":
            return state
        started = await self.store.commit(actor, hearing_id, self._key(token, "start"), state["revision"],
            "ai_start", {"token": token}, lambda s, a, r, p: hearing.mark_ai_started(s, a, r, p["token"]))
        if started.replayed:
            return started.state
        state = started.state
        context = hearing.ai_context(state, actor, token)
        task = "moot.judge" if state["floor"] == "judge" else "moot.counsel"
        instructions = REASONING_INSTRUCTIONS + "\nReturn a JSON turn only. Counsel kind=submission. Judge kind=question (target applicant/respondent), direction or ruling. Include text, authority_ids and reasoning. Ruling also includes order, reasons and decision (applicant/respondent/uncertain); never force a decision when evidence is insufficient, and never rule before both sides have submitted. Answer the pending question with responds_to equal to its turn number. Never include a source outside supplied authorities. reasoning schema: facts=[{id,position:sourced|contested|assumed,relevance}], rules=[{authority_id,court_weight,application}], comparisons=[{authority_id,similarities:[fact_id],differences:[{fact_id,explanation}],legal_significance}], opposing_argument:string,response:string,uncertainty:[string],conclusion:string. A judge may set issue_index to a supplied issue index."
        messages = [{"role": "system", "content": instructions}, {"role": "user", "content": json.dumps(context)}]
        call = asyncio.create_task(self.tank.route(actor.tenant_id, task, messages))
        watch = asyncio.create_task(self._watch(actor, hearing_id, token, state["revision"]))
        receipt = None
        try:
            done, _ = await asyncio.wait({call, watch}, return_when=asyncio.FIRST_COMPLETED)
            if watch in done and not call.done():
                call.cancel()
                with suppress(asyncio.CancelledError):
                    await call
                return await self._resolve(actor, hearing_id, token, "revoked_call_cancelled")
            reply = await call
            receipt = {k: reply.get(k) for k in ("ledger_id", "model", "cost_usd", "settings_hash")}
            # Validate receipt even if the answer is malformed, so its real cost can
            # be recorded separately from whether its text becomes a hearing turn.
            try:
                hearing._receipt(receipt)
            except hearing.HearingError:
                receipt = None
                raise
            payload = validate_turn(state, json.loads(reply["content"]))
            try:
                return await self.store.mutate(actor, hearing_id, self._key(token, "finish"), state["revision"],
                    "ai_turn", {"token": token, "payload": payload, "receipt": receipt},
                    lambda s, a, r, p: hearing.turn(s, a, r, p["payload"], ai_token=p["token"], receipt=p["receipt"]))
            except hearing.HearingError as exc:
                if exc.code in ("stale_revision", "ai_claim_invalid"):
                    return await self._resolve(actor, hearing_id, token, "revoked_result_discarded", receipt)
                raise
        except asyncio.CancelledError:
            call.cancel()
            # Durable started state stays uncertain. Request retry must not re-call.
            raise
        except Exception:
            # Details from providers, source text and bench context never enter errors.
            return await self._resolve(actor, hearing_id, token, "generation_failed", receipt)
        finally:
            watch.cancel()
            call.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await watch
            with suppress(asyncio.CancelledError, Exception):
                await call
