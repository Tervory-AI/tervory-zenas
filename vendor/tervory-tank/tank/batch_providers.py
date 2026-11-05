"""Batch capabilities of the pinned LiteLLM v1.106.4, without provider credentials.

Only providers authorized in this lane: OpenAI (existing), Vertex Gemini,
Anthropic via LiteLLM pass-through (founder decision, 28 Oct 03:15 PT).
Rates are the fraction charged, not the fraction saved. Other providers do not
inherit a discount. Sources and pinned adapter evidence are in
 tests/tank_batch_providers/PR.md.
"""
from typing import Any


def charge_multiplier(provider: str, native: str) -> float | None:
    # OpenAI Batch: https://platform.openai.com/docs/guides/batch
    if provider == 'openai':
        return 0.5
    # Google's Gemini batch pricing is 50% of standard input and output prices.
    # Do not apply Gemini pricing to Model Garden or Anthropic-on-Vertex models.
    if provider == 'vertex_ai' and native.startswith('gemini-'):
        return 0.5
    # Anthropic Message Batches also charge half the normal token rate.
    # https://platform.claude.com/docs/en/build-with-claude/batch-processing
    if provider == 'anthropic':
        return 0.5
    return None


def supports(prices: dict[str, Any]) -> bool:
    return charge_multiplier(prices['provider'], prices['native']) is not None


def compatible(prices: dict[str, Any], messages: list[dict], generation: dict) -> bool:
    if not supports(prices):
        return False
    if prices['provider'] != 'anthropic':
        return True
    # Pass-through is native Messages format. Never silently drop an OpenAI-only
    # generation option; such requests retain the foreground LiteLLM translation.
    return (not (set(generation) - {'disable_fallbacks'})
            and all(m['role'] in {'system', 'user', 'assistant'} for m in messages)
            and any(m['role'] != 'system' for m in messages))


def anthropic_params(body: dict[str, Any]) -> dict[str, Any]:
    result = {key: body[key] for key in ('model', 'max_tokens')}
    result['messages'] = [m for m in body['messages'] if m['role'] != 'system']
    system = [{'type': 'text', 'text': m['content']} for m in body['messages'] if m['role'] == 'system']
    if system:
        # One prompt-cache breakpoint after the fixed system text. Anthropic ignores it
        # below the model's minimum cacheable length; items sharing it read it at 0.1x.
        system[-1] = {**system[-1], 'cache_control': {'type': 'ephemeral'}}
        result['system'] = system
    return result


def anthropic_path(batch_id: str | None = None) -> str:
    from urllib.parse import quote
    base = '/anthropic/v1/messages/batches'
    return base if batch_id is None else base + '/' + quote(batch_id, safe='')


async def submit(gateway, batch, lines: list[str]) -> tuple[str, str | None]:
    import json
    if batch['provider'] == 'anthropic':
        requests = []
        for line in lines:
            row = json.loads(line)
            requests.append({'custom_id': row['custom_id'], 'params': anthropic_params(row['body'])})
        response = await gateway('POST', anthropic_path(), model=batch['model'], json={'requests': requests})
        return response.json()['id'], None
    uploaded = await gateway('POST', '/v1/files', model=batch['model'],
        data={'purpose': 'batch', 'custom_llm_provider': batch['provider']},
        files={'file': ('requests.jsonl', ('\n'.join(lines)+'\n').encode(), 'application/jsonl')})
    file_id = uploaded.json()['id']
    response = await gateway('POST', '/v1/batches', model=batch['model'], json={
        'input_file_id': file_id, 'endpoint': '/v1/chat/completions', 'completion_window': '24h',
        'custom_llm_provider': batch['provider'], 'metadata': {'tank_batch_id': str(batch['id'])}})
    return response.json()['id'], file_id


async def status(gateway, batch) -> dict[str, Any]:
    if batch['provider'] == 'anthropic':
        response = await gateway('GET', anthropic_path(batch['provider_id']), model=batch['model'])
        state = response.json()['processing_status']
        # Native 'ended' includes partial errors, cancellation and expiry. Only
        # then read item outcomes and use the existing bounded per-item retry.
        return {'status': {'in_progress': 'in_progress', 'canceling': 'cancelling', 'ended': 'completed'}[state],
                'output_file_id': batch['provider_id'] if state == 'ended' else None}
    response = await gateway('GET', f"/v1/batches/{batch['provider_id']}", model=batch['model'],
                             params={'custom_llm_provider': batch['provider']})
    return response.json()


async def cancel(gateway, batch) -> None:
    if batch['provider'] == 'anthropic':
        await gateway('POST', anthropic_path(batch['provider_id']) + '/cancel', model=batch['model'])
    else:
        await gateway('POST', f"/v1/batches/{batch['provider_id']}/cancel", model=batch['model'],
                      json={'custom_llm_provider': batch['provider']})


def anthropic_result(row: dict[str, Any]) -> dict[str, Any]:
    result = row['result']
    if result['type'] in {'errored', 'canceled', 'expired'}:
        # No provider error text is stored. Existing _batch_result triggers the
        # same reserved-budget fallback as a failed OpenAI or Vertex item.
        return {'custom_id': row['custom_id'], 'response': {'status_code': 400}}
    if result['type'] != 'succeeded':
        raise ValueError('unknown_batch_result')
    message = result['message']
    from prompt_cache import anthropic_usage
    text = ''.join(block['text'] for block in message['content'] if block['type'] == 'text')
    return {'custom_id': row['custom_id'], 'response': {'status_code': 200, 'body': {
        'choices': [{'message': {'content': text}, 'finish_reason': message.get('stop_reason')}],
        # input_tokens excludes cache reads and writes; the booked prompt total includes them.
        'usage': anthropic_usage(message['usage'])}}}


async def results(gateway, batch, file_id: str) -> dict[str, Any]:
    import json
    if batch['provider'] == 'anthropic':
        # Never follow the upstream results_url: every call stays inside LiteLLM.
        response = await gateway('GET', anthropic_path(batch['provider_id']) + '/results', model=batch['model'])
        rows = [anthropic_result(json.loads(line)) for line in response.text.splitlines() if line.strip()]
    else:
        response = await gateway('GET', f'/v1/files/{file_id}/content', model=batch['model'],
                                 params={'custom_llm_provider': batch['provider']})
        rows = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    return {row['custom_id']: row for row in rows}
