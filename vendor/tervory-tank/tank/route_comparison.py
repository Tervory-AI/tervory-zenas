"""Offline comparison contracts only; never registered in the serving ladder."""
from dataclasses import dataclass
import hashlib
import json
import math
from time import perf_counter
from typing import Callable

from routellm_shadow import Configuration, configuration, REVISION as ROUTELLM_REVISION

MAX_EXAMPLES = 32
MAX_INPUT_CHARS = 16000
ARCH_REVISION = '5b156890a91b1035a86f82faf1a651debf309009'


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(rows: list[dict]) -> None:
    if not rows or len(rows) > MAX_EXAMPLES:
        raise ValueError('Expected between one and 32 examples')
    seen, groups, identities = set(), {}, set()
    for row in rows:
        if (not row.get('id') or not row.get('tenant_id') or row.get('split') not in {'development', 'holdout'}
                or row.get('reference_route') not in {'weak', 'strong', 'other'}):
            raise ValueError('Invalid example identity or route')
        if row['id'] in identities:
            raise ValueError('Duplicate example ID')
        identities.add(row['id'])
        provenance = row.get('provenance', {})
        if (provenance.get('kind') != 'authored-synthetic' or not provenance.get('source_id')
                or not provenance.get('author') or provenance.get('gold') is not False):
            raise ValueError('Only provenance-tracked synthetic non-gold examples are accepted')
        messages = row.get('messages')
        if (not isinstance(messages, list) or not messages or len(json.dumps(messages)) > MAX_INPUT_CHARS
                or any(set(m) != {'role', 'content'} or m['role'] not in {'user', 'assistant', 'system'}
                       or not isinstance(m['content'], str) for m in messages)
                or not any(m['role'] == 'user' for m in messages)):
            raise ValueError('Invalid or oversized conversation')
        # Normalize case/whitespace to catch trivial copies across split/tenant.
        content = digest([(m['role'], ' '.join(m['content'].lower().split())) for m in messages])
        if content in seen:
            raise ValueError('Duplicate conversation across comparison examples')
        seen.add(content)
        group = provenance['source_id']
        split = row['split']
        if group in groups and groups[group] != split:
            raise ValueError('Source group crosses development and holdout')
        groups[group] = split


@dataclass(frozen=True)
class Decision:
    model: str
    route: str
    abstained: bool
    escalated: bool
    reason: str


def decision(route: str, config: Configuration, reason: str = 'classified') -> Decision:
    if route == 'weak':
        return Decision(config.weak, route, False, False, reason)
    return Decision(config.strong, route, route == 'other', True, reason)


class ArchAdapter:
    """Contract injection; this module does not load or execute model weights."""
    def __init__(self, generate: Callable[[dict], str], config: Configuration, routes: list[dict]):
        if ({r['name'] for r in routes} != {'weak', 'strong'} or len(routes) != 2
                or any(not isinstance(r.get('description'), str) or not r['description'].strip() for r in routes)):
            raise ValueError('Describe exactly the same weak and strong pair')
        self.generate, self.config, self.routes = generate, config, routes

    def classify(self, messages: list[dict]) -> Decision:
        # Upstream accepts route descriptions and a conversation, returning
        # {"route": exact_name} or {"route": "other"}. References stay out.
        try:
            raw = self.generate({'routes': self.routes, 'conversation': messages})
            if not isinstance(raw, str) or len(raw) > 512:
                raise ValueError('Invalid output')
            result = json.loads(raw)
            if not isinstance(result, dict) or set(result) != {'route'} or result['route'] not in {'weak', 'strong', 'other'}:
                raise ValueError('Invalid route')
            return decision(result['route'], self.config)
        except Exception:
            return decision('other', self.config, 'invalid_or_unavailable')


class RouteLLMAdapter:
    def __init__(self, score: Callable[[str], float], config: Configuration):
        self.score, self.config = score, config

    def classify(self, messages: list[dict]) -> Decision:
        # Match existing routellm_shadow.serve: latest user turn and >= threshold.
        prompt = next(m['content'] for m in reversed(messages) if m['role'] == 'user')
        try:
            score = self.score(prompt)
            if type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError('Invalid score')
            return decision('strong' if score >= self.config.threshold else 'weak', self.config)
        except Exception:
            return decision('other', self.config, 'invalid_or_unavailable')


def dry_run(data: dict, *, tenant_id: str, split: str) -> dict:
    rows = data['examples']
    validate(rows)  # Check leakage across the complete manifest before filtering.
    selected = [row for row in rows if row['tenant_id'] == tenant_id and row['split'] == split]
    if not selected:
        raise ValueError('No examples for this tenant and split')
    config = configuration(data['policy'])
    if config is None:
        raise ValueError('Invalid existing Tank route configuration')
    # Deliberately constant, conflicting fixtures, independent of reference labels.
    # These are NOT Arch or RouteLLM predictions and cannot yield a benchmark.
    adapters = {'arch': ArchAdapter(lambda payload: '{"route":"weak"}', config, data['routes']),
                'routellm': RouteLLMAdapter(lambda prompt: .75, config)}
    report = {}
    for name, adapter in adapters.items():
        counts = dict(examples=len(selected), reference_matches=0, abstentions=0, escalations=0)
        elapsed = []
        for row in selected:
            started = perf_counter()
            result = adapter.classify(row['messages'])
            elapsed.append((perf_counter() - started) * 1000)
            counts['reference_matches'] += int(result.route == row['reference_route'])
            counts['abstentions'] += int(result.abstained)
            counts['escalations'] += int(result.escalated)
        report[name] = {**counts, 'fixture_adapter_latency_ms': {'mean': sum(elapsed) / len(elapsed), 'max': max(elapsed)}}
    return {'mode': 'fixture-only', 'dataset_sha256': digest(rows), 'split': split,
            'selected_examples_sha256': digest(selected),
            'configuration_fingerprint': config.fingerprint, 'route_descriptions_sha256': digest(data['routes']),
            'sources': {'arch_revision': ARCH_REVISION, 'routellm_revision': ROUTELLM_REVISION},
            'fixture_contract': report,
            'model_comparison': {'status': 'unrun', 'route_quality': None, 'abstention_rate': None,
                'escalation_rate': None, 'classifier_latency_ms': None, 'inference_cost_usd': None,
                'cost_status': 'unknown', 'savings_usd': None,
                'commercial_permission': 'pending-counsel-review',
                'gates': ['verified local CPU model/runtime for both classifiers',
                          'independently reviewed development/holdout route labels']}}
