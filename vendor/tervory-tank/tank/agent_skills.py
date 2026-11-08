"""Hermes room boundary: room authentication, chat receipts and Skala exchange.

There is deliberately no second skill runner or approval endpoint here.
"""
from __future__ import annotations

from tenant_db import tenant_scope

import hashlib
import hmac
import importlib.util
import json
import math
import logging
import os
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import httpx
import controls
import yaml
from base_public_http import BaseBlockResponse, PublicPageHTTP
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("tank.agent_skills")
ROOM = "base"
# Rooms with a brain pilot. room_text() maps a request's room to one of these fixed
# strings; SQL always receives the room and its task type as bound parameters.
ROOMS = {"base": "base", "rig": "rig", "tiger": "tiger", "zenas": "zenas", "ghost": "ghost", "trigger": "trigger", "tisan": "tisan", "radix": "radix", "market_lab": "market_lab"}
SUBMISSIONS = Path("/tmp/tervory-agent-skills")
LEDGER_SQL = """
INSERT INTO tank.ledger
 (id,tenant_id,region,agent,task_type,rung,model,priority,tokens_in,tokens_out,
  cost_usd,latency_ms,outcome,task_id,error)
VALUES ($1,$2,$3,$14,$15,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
"""


def room_text(room: str) -> str:
    """The allowlisted room string for SQL parameters; anything else is refused."""
    try:
        return ROOMS[room]
    except (KeyError, TypeError):
        raise HTTPException(403, "Room is not enabled.") from None


def room_key(room: str, authorization: str = "", x_tank_key: str = "") -> None:
    """Only enabled rooms. Never accept the Workspace or another room's key."""
    room = room_text(room)
    bearer = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    if bearer and x_tank_key and not hmac.compare_digest(bearer.encode(), x_tank_key.encode()):
        raise HTTPException(401, "Conflicting room keys.")
    presented = bearer or x_tank_key
    expected = os.environ.get("TANK_KEY_" + room.upper(), "").strip()
    if not expected or not presented or not hmac.compare_digest(expected.encode(), presented.encode()):
        raise HTTPException(401, "Owning room key required.")


def local_region(tank: Any, home: str | None, *, room: str = ROOM) -> str:
    box = tank.box_region()
    if room == "zenas":
        if box not in {"us-east", "za"} or home != box:
            raise HTTPException(403)
        return box
    # The Base pilot is US-only. Never forward a ZA tenant to Boston.
    if box != "us-east" or (home is not None and home != box):
        raise HTTPException(403, "Base's pilot stays in its home region.")
    return box


@lru_cache(maxsize=16)
def zenas_scope_rules(package_path: Path):
    """Load the existing approved scope rules; never run its model fallback."""
    spec = importlib.util.spec_from_file_location("brain_zenas_scope", package_path / "rules.py")
    if spec is None or spec.loader is None:
        raise HTTPException(503)
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    return rules


class Chat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paid_run_id: uuid.UUID | None = None
    model: str = "policy"
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=512)
    tools: list[dict[str, Any]] | None = Field(default=None, max_length=64)
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    stop: str | list[str] | None = None
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    user: str | None = None
    n: Literal[1] = 1


def validate_chat(body: Chat) -> None:
    if len(json.dumps(body.model_dump(exclude={"paid_run_id"}))) > 1_000_000:
        raise HTTPException(413, "Conversation is too large.")
    for message in body.messages:
        if message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
            raise HTTPException(422, "Unsupported message role.")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            if not isinstance(content, list) or any(
                not isinstance(p, dict) or p.get("type") != "text" or not isinstance(p.get("text"), str)
                for p in content
            ):
                raise HTTPException(422, "The Base pilot accepts text only.")
        calls = message.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list) or len(calls) > 64:
                raise HTTPException(422, "Invalid tool calls.")
            for call in calls:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not isinstance(call.get("function"), dict):
                    raise HTTPException(422, "Invalid tool call.")
                function = call["function"]
                if not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                    raise HTTPException(422, "Invalid tool function.")



def amount(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Missing usage cost") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError("Invalid usage cost")
    return number


def baseline_cost(roster: list[dict], model: str, tokens_in: int, tokens_out: int) -> float | None:
    for row in roster:
        if row.get("model_name") == model:
            info = row.get("model_info") or {}
            try:
                return tokens_in * amount(info.get("input_cost_per_token")) + tokens_out * amount(info.get("output_cost_per_token"))
            except ValueError:
                return None
    return None


@lru_cache(maxsize=1)
def input_encoding():
    import tiktoken
    from tiktoken.load import load_tiktoken_bpe
    # OpenAI's MIT-licensed cl100k vocabulary is bundled and hash checked:
    # no runtime tokenizer download, provider request or credential is needed.
    ranks = load_tiktoken_bpe(str(Path(__file__).with_name("cl100k_base.tiktoken")),
        expected_hash="223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7")
    return tiktoken.Encoding(name="base-cl100k", mergeable_ranks=ranks, special_tokens={},
        pat_str=r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s")


def input_tokens(payload: dict) -> int:
    # Count the complete serialized message/tool payload, then allow 20% for
    # provider tokenizer differences and 32 framing tokens per message. This is
    # a reference BPE budget, not a claim of exact billing for every provider.
    count = len(input_encoding().encode_ordinary(json.dumps(payload, ensure_ascii=False)))
    return math.ceil(count * 1.2) + 32 * len(payload.get("messages", []))


async def complete(tank: Any, body: Chat, tenant: uuid.UUID, task: uuid.UUID | None, region: str, *, room: str = ROOM) -> dict:
    import paid_steps
    room = room_text(room)
    validate_chat(body)
    digest = paid_steps.fingerprint(body.model_dump(exclude={"paid_run_id"}))
    identity = body.paid_run_id or (uuid.uuid5(task,digest) if task else uuid.uuid4())
    run = paid_steps.Run(tenant, identity, digest)
    with paid_steps.run_scope(run):
        return await paid_steps.checkpoint(paid_steps.fingerprint(["agent.chat", digest]),
            lambda: _complete(tank, body, tenant, task, region, room=room))


async def _complete(tank: Any, body: Chat, tenant: uuid.UUID, task: uuid.UUID | None, region: str, *, room: str = ROOM) -> dict:
    room = room_text(room)
    task_type = f"agent.{room}.think"
    validate_chat(body)
    if room == "zenas":
        region = local_region(tank, region, room=room)
        version = await tank._pick_version("zenas.scope", False)
        package = tank.get_package("zenas.scope", version)
        # Scan the whole supplied conversation, including tool results/arguments.
        # This is the room's existing classifier, with no model scope fallback.
        scope = zenas_scope_rules(package.path).classify(
            json.dumps(body.model_dump(exclude_none=True,exclude={"paid_run_id"}), ensure_ascii=False), "za" if region == "za" else "us")
        if scope.get("criminal") or not scope.get("in_scope"):
            raise HTTPException(403, scope["reason"])
    policy = await tank.load_policy(task_type, tenant)
    model = policy["model"]
    if tank.graduate.router_cannot_serve(model):
        raise HTTPException(409, "The brain needs a model policy.")
    # Keep only OpenAI text and tool fields. Extra SDK/provider fields never
    # become instructions, reasoning settings or alternate routing parameters.
    messages = []
    for source in body.messages:
        message = {"role": source["role"], "content": source.get("content")}
        if isinstance(message["content"], list):
            message["content"] = [{"type": "text", "text": part["text"]} for part in message["content"]]
        if source.get("tool_call_id"):
            message["tool_call_id"] = str(source["tool_call_id"])
        if source.get("tool_calls"):
            message["tool_calls"] = [{"id": item["id"], "type": "function", "function": {
                "name": item["function"]["name"], "arguments": item["function"]["arguments"]}}
                for item in source["tool_calls"]]
        messages.append(message)
    if policy.get("system_prompt"):
        messages.insert(0, {"role": "system", "content": policy["system_prompt"]})
    text = []
    for message in messages:
        content = message.get("content")
        text.append(content if isinstance(content, str) else "\n".join(part["text"] for part in content or []))
        text.extend(call["function"]["arguments"] for call in message.get("tool_calls", []))
    refusal = tank.model_call_refusal("\n".join(text), model,
        box=region, tenant_id=str(tenant), task_type=task_type, caller=room)
    if refusal:
        raise HTTPException(403, refusal)
    # Policy chooses the route; a model name in an SDK request cannot override it.
    payload = body.model_dump(exclude_none=True, exclude={"paid_run_id", "model", "stream", "stream_options", "max_completion_tokens", "user", "reasoning_effort"})
    payload.update(model=model, messages=messages, stream=False,
                   max_tokens=min(body.max_tokens or body.max_completion_tokens or policy["max_tokens_out"], policy["max_tokens_out"]))
    # Match Hermes' minimum 64k context, reserving the requested output budget.
    upper_input = input_tokens(payload)
    if upper_input > min(int(policy.get("max_tokens_in") or 64000), 64000 - payload["max_tokens"]):
        raise HTTPException(413, "Conversation exceeds The brain's input-token budget.")
    roster = await tank._roster_models()
    if baseline_cost(roster, model, 1, 1) is None:
        raise HTTPException(503, "The brain's policy model needs roster prices before it can be called.")
    controls._task.set(task_type)
    approval = await __import__("paid_steps").model_admission(tenant, task_type, model, agent=room, roster=roster)
    ledger_id = uuid.uuid4()
    started = time.perf_counter()
    outcome, error, cost = "failed", "Model gateway unavailable.", 0.0
    cost_status = "not_billed"
    tokens_in = tokens_out = 0
    answered = model
    data: dict[str, Any] = {}
    payload.update(await __import__('engine_tracing').metadata(tank, tenant, task_type, model, policy))
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                tank.LITELLM_URL + "/v1/chat/completions", json=payload,
                headers={"Authorization": "Bearer " + tank.LITELLM_MASTER_KEY},
            )
        try:
            cost = amount(response.headers.get("x-litellm-response-cost"))
            cost_status = "reported"
        except ValueError:
            pass
        if response.status_code == 200:
            answered = response.headers.get("x-litellm-model-group") or model
            # A billed but malformed response must still retain its reported
            # spend. If usage is also missing, reserve a clearly labelled upper
            # bound instead of logging the successful upstream call as free.
            tokens_in, tokens_out = upper_input, payload["max_tokens"]
            if cost_status != "reported":
                cost = baseline_cost(roster, model, tokens_in, tokens_out)
                cost_status = "estimated_upper_bound"
            data = response.json()
            usage = data["usage"]
            observed_in, observed_out = int(usage["prompt_tokens"]), int(usage["completion_tokens"])
            if observed_in < 0 or observed_out < 0:
                raise ValueError("Malformed model usage")
            tokens_in, tokens_out = observed_in, observed_out
            if cost_status != "reported":
                estimate = baseline_cost(roster, answered, tokens_in, tokens_out)
                if estimate is None:
                    estimate = baseline_cost(roster, model, tokens_in, tokens_out)
                assert estimate is not None
                cost, cost_status = estimate, "estimated"
            if not isinstance(data["choices"][0]["message"], dict):
                raise ValueError("Malformed model response")
            outcome, error = "delivered", None
        else:
            error = "Model gateway refused the call (HTTP %d)." % response.status_code
    except (httpx.ReadTimeout, httpx.ReadError, httpx.WriteTimeout, httpx.WriteError, httpx.RemoteProtocolError):
        # The request may have reached the provider. Preserve an explicitly
        # unknown conservative reservation until a provider receipt reconciles it.
        tokens_in, tokens_out = upper_input, payload["max_tokens"]
        cost = baseline_cost(roster, model, tokens_in, tokens_out)
        cost_status, outcome = "unknown_upper_bound", "unknown"
        error = "Model gateway interrupted after send; provider charge is unknown."
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        # Never return or log upstream bodies: they can contain credentials/prompts.
        error = "Model gateway failed or returned an incomplete receipt."
    latency = int((time.perf_counter() - started) * 1000)
    # Reporting follows the answered provider, including a gateway fallback.
    # The cleared legacy policy field is not a comparison baseline.
    from bench import price_book
    book = price_book(roster)
    family = book.get(answered, {}).get("family")
    async with tank.pool().acquire() as conn:
        baseline = await conn.fetchval("""SELECT coalesce(
            (SELECT override_model FROM tank.baseline_settings WHERE task_type=$2),
            tank.standard_model_for($1::text))""", family, task_type)
    baseline_usd = (baseline_cost(roster, baseline, tokens_in, tokens_out)
                    if family and book.get(baseline, {}).get("family") == family else None)
    receipt = {
        "ledger_id": str(ledger_id), "tenant_id": str(tenant), "task_id": str(task) if task else None,
        "room": room, "task_type": task_type, "region": region, "model": answered,
        "cost_usd": cost, "cost_status": cost_status, "baseline_model": baseline, "baseline_cost_usd": baseline_usd,
        "saving_usd": None if baseline_usd is None or cost is None else baseline_usd - cost,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "latency_ms": latency, "outcome": outcome,
    }
    if approval:
        receipt["paid_step_id"] = approval["step_id"]
        receipt["paid_step_mode"] = approval["authorization_mode"]
    async with tank.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(LEDGER_SQL, ledger_id, tenant, region, policy["rung"], answered, policy["priority"],
                               tokens_in, tokens_out, cost, latency, outcome, task, error, room, task_type)
            await conn.execute(
                "INSERT INTO tank.transcripts (ledger_id,tenant_id,task_type,messages,content) "
                "VALUES ($1,$2,$5,$3::jsonb,$4)", ledger_id, tenant, json.dumps(messages),
                json.dumps(data.get("choices", [])) if data else "Model call failed.", task_type,
            )
            await conn.execute(
                "INSERT INTO tank.agent_receipts (ledger_id,tenant_id,room,receipt) VALUES ($1,$2,$4,$3::jsonb)",
                ledger_id, tenant, json.dumps(receipt), room,
            )
    await __import__("paid_steps").attach_receipt(ledger_id, approval)
    if outcome != "delivered":
        raise HTTPException(502, {"message": error, "receipt": receipt})
    return {
        "id": "chatcmpl-" + str(ledger_id), "object": "chat.completion", "created": int(time.time()),
        "model": answered, "choices": data["choices"],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out, "total_tokens": tokens_in + tokens_out},
        "receipt": receipt,
    }


def stream_completion(result: dict):
    """Buffer upstream so spend is durable before the SDK sees any answer."""
    base = {k: result[k] for k in ("id", "created", "model")}
    base["object"] = "chat.completion.chunk"
    for choice in result["choices"]:
        delta = dict(choice["message"])
        if delta.get("tool_calls"):
            delta["tool_calls"] = [{"index": i, **tool} for i, tool in enumerate(delta["tool_calls"])]
        yield "data: " + json.dumps({**base, "choices": [{"index": choice.get("index", 0), "delta": delta, "finish_reason": None}]}) + "\n\n"
        yield "data: " + json.dumps({**base, "choices": [{"index": choice.get("index", 0), "delta": {}, "finish_reason": choice.get("finish_reason", "stop")}]}) + "\n\n"
    yield "data: " + json.dumps({**base, "choices": [], "usage": result["usage"], "receipt": result["receipt"]}) + "\n\n"
    yield "data: [DONE]\n\n"


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    home_region: str
    skill_id: str = Field(pattern=r"^base\.learned\.[a-z][a-z0-9_]{0,40}$")
    version: str = Field(pattern=r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$")
    description: str = Field(min_length=1, max_length=1024)
    instructions: str = Field(min_length=1, max_length=16000)
    run_py: str = Field(min_length=1, max_length=64000)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    cases: list[dict[str, Any]] = Field(min_length=1, max_length=100)


def candidate_files(body: Candidate) -> dict[str, str]:
    import ast

    try:
        tree = ast.parse(body.run_py)
    except SyntaxError:
        raise HTTPException(422, "Candidate Python does not parse.") from None
    if not any(isinstance(node, ast.AsyncFunctionDef) and node.name == "run" for node in tree.body):
        raise HTTPException(422, "Candidate needs async def run(input, ctx).")
    if any(not {"input", "expected"} <= set(case) for case in body.cases):
        raise HTTPException(422, "Every eval case needs input and expected.")
    files = {
        "skill.yaml": yaml.safe_dump({"id": body.skill_id, "version": body.version, "agent": ROOM,
            "rung": 3, "description": body.description, "input": body.input_schema,
            "output": body.output_schema, "eval_threshold": 1.0}),
        "run.py": body.run_py,
        "SKILL.md": "---\n" + yaml.safe_dump({"name": body.skill_id.replace(".", "-").replace("_", "-"), "description": body.description}) + "---\n" + body.instructions,
        "evals/cases.jsonl": "\n".join(json.dumps(case) for case in body.cases) + "\n",
        "CHANGELOG.md": "# " + body.version + "\n\nSubmitted by Base as a candidate. Requires evals and founder approval.\n",
    }
    if len(json.dumps(files)) > 256_000:
        raise HTTPException(413, "Candidate package is too large.")
    # Reject accidental copies of credentials, without disclosing which value matched.
    raw = json.dumps(files)
    for name, value in os.environ.items():
        if any(part in name for part in ("KEY", "PASSWORD", "TOKEN", "SECRET")) and len(value) >= 8 and value in raw:
            raise HTTPException(422, "Remove credentials from the candidate.")
    return files


def materialize(tank: Any, skill_id: str, version: str, files: dict, digest: str):
    """Rebuild immutable source from Postgres; never write over a Git package."""
    if not re.fullmatch(r"base\.learned\.[a-z][a-z0-9_]{0,40}", skill_id) or not re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", version):
        raise RuntimeError("Invalid stored candidate identity")
    if hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest() != digest:
        raise RuntimeError("Candidate source digest mismatch")
    directory = SUBMISSIONS / digest / skill_id / version
    directory.mkdir(parents=True, exist_ok=True)
    allowed = {"skill.yaml", "run.py", "SKILL.md", "evals/cases.jsonl", "CHANGELOG.md"}
    if set(files) != allowed:
        raise RuntimeError("Invalid stored package files")
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_text() != content:
                raise RuntimeError("Candidate source was modified")
        else:
            with path.open("x") as target:
                target.write(content)
    return tank.skala._parse_package(directory / "skill.yaml", directory)


async def submit(tank: Any, body: Candidate) -> dict:
    region = local_region(tank, body.home_region)
    files = candidate_files(body)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    identity = (body.skill_id, body.version)
    async with tank.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", body.skill_id)
            exists = await conn.fetchval("SELECT 1 FROM tank.skills WHERE id=$1 AND version=$2", *identity)
            if exists or identity in tank._packages:
                raise HTTPException(409, "This version already exists; submit a new version.")
            owner = await conn.fetchrow("SELECT tenant_id,region FROM tank.agent_skill_submissions WHERE skill_id=$1 LIMIT 1", body.skill_id)
            if owner and (owner["tenant_id"] != body.tenant_id or owner["region"] != region):
                raise HTTPException(403, "This skill id belongs to another tenant or region.")
            if not owner and await conn.fetchval("SELECT 1 FROM tank.skills WHERE id=$1 LIMIT 1", body.skill_id):
                raise HTTPException(409, "Use a new id for a room-authored skill.")
            pkg = materialize(tank, *identity, files, digest)
            await conn.execute(
                "INSERT INTO tank.skills (id,version,agent,rung,description,eval_threshold,cases,state,present) "
                "VALUES ($1,$2,'base',3,$3,1.0,$4,'candidate',true)",
                *identity, body.description, len(body.cases),
            )
            await conn.execute(
                "INSERT INTO tank.agent_skill_submissions "
                "(skill_id,version,tenant_id,region,submitted_by_room,package,sha256) VALUES ($1,$2,$3,$4,'base',$5::jsonb,$6)",
                *identity, body.tenant_id, region, json.dumps(files), digest,
            )
    tank._packages[identity] = pkg
    return {"skill": pkg.ref, "state": "candidate", "submitted_by_room": ROOM, "sha256": digest}


async def restore(tank: Any) -> None:
    """Unavailable or corrupt submissions never prevent Tank from booting."""
    try:
        async with tank.pool().acquire() as conn:
            rows = await conn.fetch("SELECT * FROM tank.agent_skill_submissions WHERE submitted_by_room='base' AND region=$1", tank.box_region())
            for row in rows:
                identity = (row["skill_id"], row["version"])
                try:
                    existing = tank._packages.get(identity)
                    expected = SUBMISSIONS / row["sha256"] / identity[0] / identity[1]
                    if existing is not None and existing.path != expected:
                        raise RuntimeError("Submitted skill collides with a Git package")
                    files = json.loads(row["package"]) if isinstance(row["package"], str) else row["package"]
                    package = materialize(tank, *identity, files, row["sha256"])
                    await conn.execute("UPDATE tank.skills SET present=true WHERE id=$1 AND version=$2", *identity)
                    tank._packages[identity] = package
                except Exception:
                    # No raw package text, SQL errors or paths in logs.
                    log.warning("Base submission restore failed; version unavailable")
                    await conn.execute("UPDATE tank.skills SET present=false WHERE id=$1 AND version=$2", *identity)
    except Exception:
        log.warning("Base submission restore unavailable; Tank will continue booting")
        try:
            async with tank.pool().acquire() as conn:
                await conn.execute("UPDATE tank.skills SET present=false WHERE (id,version) IN "
                    "(SELECT skill_id,version FROM tank.agent_skill_submissions WHERE submitted_by_room='base')")
        except Exception:
            log.warning("Base submission availability update deferred")


async def export_skills(tank: Any, tenant: uuid.UUID, region: str, *, room: str = ROOM) -> dict:
    room = room_text(room)
    async with tank.pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT s.id,s.version FROM tank.skills s LEFT JOIN tank.agent_skill_submissions a "
            "ON a.skill_id=s.id AND a.version=s.version "
            "WHERE s.agent=$3 AND s.state='approved' AND s.present "
            "AND (a.skill_id IS NULL OR (a.tenant_id=$1 AND a.region=$2)) ORDER BY s.id,s.version", tenant, region, room,
        )
    skills = []
    for row in rows:
        pkg = tank.get_package(row["id"], row["version"])
        if pkg.agent != room:
            raise HTTPException(409, "Skill ownership does not match the registry.")
        name = pkg.id.replace(".", "-").replace("_", "-")[:40] + "-v" + pkg.version.replace(".", "-")
        # Export instructions and schemas, never executable source into Hermes.
        text = "---\n" + yaml.safe_dump({"name": name, "description": pkg.description or pkg.id,
            "metadata": {"skala-id": pkg.id, "skala-version": pkg.version}}) + "---\n"
        instructions = pkg.path / "SKILL.md"
        if instructions.exists():
            source = instructions.read_text()
            text += source.split("---", 2)[-1] if source.startswith("---") else source
        text += "\nExecute through skala_run with skill_id=" + pkg.id + " and version=" + pkg.version + ".\n"
        text += "Input schema:\n```json\n" + json.dumps(pkg.input_schema) + "\n```\n"
        skills.append({"id": pkg.id, "version": pkg.version, "state": "approved", "agent": room,
                       "name": name, "files": {"SKILL.md": text}, "input_schema": pkg.input_schema})
    return {"tenant_id": str(tenant), "room": room, "region": region, "skills": skills}


class BaseSkillHTTP(PublicPageHTTP):
    """Base-only public reads and the existing authenticated browser adapter."""
    def __init__(self, client: Any, tenant: uuid.UUID, task: uuid.UUID | None, services: tuple[str, ...] = ()) -> None:
        super().__init__(client, tenant, services)
        self.task = task

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        if url == "http://base-browser-gateway:3000/render":
            kwargs["timeout"] = 300
            payload = dict(kwargs.get("json") or {})
            payload["max_ms"] = max(int(payload.get("max_ms") or 0), 90000)
            kwargs["json"] = payload
            kwargs["headers"] = {"X-Tank-Key": os.environ.get("TANK_KEY_BASE", ""),
                                 "X-Tenant-ID": str(self.tenant), "X-Home-Region": "us-east"}
            if self.task:
                kwargs["headers"]["X-Task-ID"] = str(self.task)
        return await self.request("POST", url, **kwargs)


async def guard_submission_owner(tank: Any, pkg: Any, tenant_id: uuid.UUID) -> None:
    """Private submitted code is executable only in its owning tenant and region."""
    async with tank.pool().acquire() as conn:
        owner = await conn.fetchrow("SELECT tenant_id,region FROM tank.agent_skill_submissions WHERE skill_id=$1 AND version=$2", pkg.id, pkg.version)
    if owner and (owner["tenant_id"] != tenant_id or owner["region"] != tank.box_region()):
        raise HTTPException(403, "This skill belongs to another tenant or region.")


async def guard_base_run(tank: Any, pkg: Any, body: Any, ctx: Any) -> None:
    local_region(tank, body.home_region)
    if pkg.agent != ROOM or body.preview or await tank.skill_state(pkg.id, pkg.version) != "approved":
        raise HTTPException(403, "Base may run only its own approved skills; preview is founder-only.")
    await guard_submission_owner(tank, pkg, body.tenant_id)
    task = body.input.pop("_base_brain_task", None)
    if task is not None:
        try:
            ctx.task_id = uuid.UUID(str(task))
        except ValueError:
            raise HTTPException(422, "Invalid Base task id.") from None
    if body.input.pop("_base_brain_free", False):
        # The unchanged pipeline gets a free recovery pass before its existing
        # paid rungs. This flag can only remove spending capability, never add it.
        async def defer_model(**kwargs):
            raise HTTPException(409, "Model enrichment deferred until free recovery finishes.")
        ctx._model = defer_model
        ctx.env = {k: v for k, v in ctx.env.items() if not k.endswith(("_KEY", "_TOKEN"))}
    ctx.env["TIGER_URL"] = "http://base-browser-gateway:3000"
    ctx.http = BaseSkillHTTP(ctx.http, body.tenant_id, ctx.task_id, (
        ctx.env.get("SEARXNG_URL", ""), "https://api.search.brave.com/res/v1/web/search",
    ))


def build_router(tank: Any) -> APIRouter:
    if app := getattr(tank, "app", None):
        app.add_middleware(BaseBlockResponse)
    router = APIRouter()

    @router.on_event("startup")
    async def restore_candidates():
        with tenant_scope(None, administrative=True):
            await restore(tank)

    @router.get("/v1/agents/{room}/skills")
    async def approved(room: str, tenant_id: uuid.UUID, request: Request, home_region: str = "us-east"):
        room_key(room, request.headers.get("authorization", ""), request.headers.get("x-tank-key", ""))
        return await export_skills(tank, tenant_id, local_region(tank, home_region, room=room), room=room)

    @router.post("/v1/agents/{room}/skills/candidates", status_code=201)
    async def candidate(room: str, body: Candidate, request: Request):
        room_key(room, request.headers.get("authorization", ""), request.headers.get("x-tank-key", ""))
        if room != ROOM:
            raise HTTPException(403, "Skill submission is not enabled for this room.")
        return await submit(tank, body)

    @router.get("/v1/agents/{room}/receipts")
    async def receipts(room: str, tenant_id: uuid.UUID, task_id: uuid.UUID, request: Request):
        room_key(room, request.headers.get("authorization", ""), request.headers.get("x-tank-key", ""))
        local_region(tank, request.headers.get("x-home-region"), room=room)
        async with tank.pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT r.receipt FROM tank.agent_receipts r JOIN tank.ledger l ON l.id=r.ledger_id "
                "WHERE r.tenant_id=$1 AND l.tenant_id=$1 AND l.task_id=$2 AND r.room=$3 ORDER BY r.created_at",
                tenant_id, task_id, room_text(room))
        return {"receipts": [json.loads(r["receipt"]) if isinstance(r["receipt"], str) else r["receipt"] for r in rows]}

    @router.post("/v1/agents/{room}/chat/completions")
    async def chat(room: str, body: Chat, request: Request,
                   x_tenant_id: uuid.UUID = Header(), x_task_id: uuid.UUID | None = Header(default=None),
                   x_home_region: str | None = Header(default=None)):
        room_key(room, request.headers.get("authorization", ""), request.headers.get("x-tank-key", ""))
        result = await complete(tank, body, x_tenant_id, x_task_id, local_region(tank, x_home_region, room=room), room=room)
        if body.stream:
            return StreamingResponse(stream_completion(result), media_type="text/event-stream")
        return result

    from agent_tasks import install
    install(router, tank)
    return router
