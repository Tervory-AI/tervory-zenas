"""Per-task code tool context bounds; summaries are local deterministic excerpts."""
from __future__ import annotations

import json
import asyncio
import os
import re

from fastapi import HTTPException
from anyio import CapacityLimiter, to_thread

SETTING = 'ENGINE_CODE_TOOL_LIMITS_ENABLED'
LIMITER = CapacityLimiter(1)
WORKERS: set[asyncio.Task] = set()
LOCAL_SECONDS = 10
BINARY_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


class PreparationError(HTTPException):
    def __init__(self):
        super().__init__(503,'Local output preparation is busy. No model call was made.')


def enabled():
    return os.environ.get(SETTING, '').strip() == 'true'


def serialize(rows):
    return json.dumps(rows, ensure_ascii=False, separators=(',', ':'))


DIAGNOSTIC = re.compile(r'\berror\b|\bfailed\b|\b\w*(?:Error|Exception)\b|^E |^Traceback|\b[A-Z]{1,5}[0-9]{2,5}\b',re.I)


def diagnostic_text(result):
    if result.name != 'run_command':
        return result.output
    try:
        envelope = json.loads(result.output)
    except (ValueError,RecursionError):
        return result.output
    if not isinstance(envelope,dict) or not isinstance(envelope.get('result'),dict):
        return result.output
    output = envelope['result'].get('output')
    if not isinstance(output,str):
        return result.output
    lines = output.splitlines()
    selected = set()
    for index,line in enumerate(lines):
        if DIAGNOSTIC.search(line):
            selected.update(range(max(0,index-1),min(len(lines),index+3)))
    if not selected:
        return result.output
    return '[local diagnostic lines with adjacent context; other text omitted]\n' + '\n'.join(lines[index] for index in sorted(selected))


def excerpt(encoding, tokens, keep):
    if len(tokens) <= keep:
        return encoding.decode(tokens)
    # Decode each edge independently so a cut through a Unicode character
    # drops only the incomplete character, never inventing replacement text.
    head = keep // 2
    first = encoding.decode_bytes(tokens[:head]).decode('utf-8', errors='ignore')
    last = encoding.decode_bytes(tokens[-(keep-head):]).decode('utf-8', errors='ignore') if keep > head else ''
    return first + '\n[local excerpt: middle omitted; request a narrower tool result]\n' + last


def render(results, limit, encoding):
    if type(limit) is not int or limit < 1:
        raise HTTPException(422, 'This task needs a positive tool-output token limit.')
    if any(BINARY_CONTROL.search(result.output) for result in results):
        raise HTTPException(422,'Request text diagnostics instead of binary tool output.')
    rows = [{'name': result.name, 'output': result.output} for result in results]
    original = json.dumps(rows, ensure_ascii=False)
    before = len(encoding.encode(original))
    if before <= limit:
        return original, {'tokens_before':before, 'tokens_after':before, 'limit':limit, 'summarized':False}
    vectors = [encoding.encode(diagnostic_text(result)) for result in results]
    keep = max(1, limit // max(1,len(rows)))
    while keep:
        shortened = serialize([{'name':row['name'], 'output':excerpt(encoding,tokens,keep)}
                               for row,tokens in zip(rows,vectors)])
        after = len(encoding.encode(shortened))
        if after <= limit:
            return shortened, {'tokens_before':before, 'tokens_after':after, 'limit':limit, 'summarized':True}
        keep //= 2
    # A cap smaller than the structural envelope is not a reason to spend.
    raise HTTPException(422, 'The local tool result envelope exceeds this task token limit. Request fewer or narrower results.')


def prepare(results, limit):
    from agent_skills import input_encoding
    from code_sessions import code_text
    from types import SimpleNamespace
    cleaned = [SimpleNamespace(name=result.name, output=ANSI.sub('',code_text(result.output))) for result in results]
    return render(cleaned,limit,input_encoding())


async def context(tenant_id, task_type, results):
    from main import load_policy
    # Reuse the existing tenant override and task policy; no model is selected.
    # Tank still enforces min(request.max_tokens, policy.max_tokens_out).
    try:
        async with asyncio.timeout(LOCAL_SECONDS):
            policy = await load_policy(task_type,tenant_id)
            owner = object()
            await LIMITER.acquire_on_behalf_of(owner)
            # The request may time out or disconnect while native tokenization
            # continues. Only that worker may release its slot. Queued requests
            # are cancellable before creating a worker, so they cannot pile up
            # abandoned CPU jobs behind it.
            worker = asyncio.create_task(run_preparation(results,policy.get('max_tokens_out'),owner,LIMITER))
            WORKERS.add(worker)
            worker.add_done_callback(finished)
            return await asyncio.shield(worker)
    except TimeoutError:
        raise PreparationError() from None


async def run_preparation(results, limit, owner, limiter):
    try:
        return await to_thread.run_sync(prepare,results,limit)
    finally:
        limiter.release_on_behalf_of(owner)


def finished(worker):
    WORKERS.discard(worker)
    # If the caller already received a safe timeout, consume a later local
    # exception without logging private source or diagnostics.
    if not worker.cancelled():
        worker.exception()
