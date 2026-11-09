"""Offline Opli Code fixtures and Tervor Code preflight. Never executes answers."""
from __future__ import annotations

import argparse
import ast
import json
import hashlib
from pathlib import Path, PurePosixPath
import sys

from tank.tervor_registry import digest, encoded, identity

PLATFORM = '00000000-0000-0000-0000-000000000001'
PRODUCT = 'opli'
CATEGORIES = frozenset({'multi_file_patch', 'bug_fix', 'tests', 'command_safety', 'repository_rules'})
FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures/tervor_code/baseline.json'
MAX_BYTES = 1024 * 1024
SAFE_COMMANDS = (['python', '-m', 'pytest', '-q'], ['git', 'diff', '--check'])


def read_json(path: Path) -> object:
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Only bounded regular JSON files are accepted')
    return json.loads(path.read_text())


def source_hash(source: dict) -> str:
    return digest(source['files'])


def provenance_hash(source: dict) -> str:
    return digest({key: source[key] for key in ('kind', 'reference', 'revision', 'licence', 'provider')})


def valid_files(files: object) -> bool:
    return isinstance(files, dict) and all(
        isinstance(path, str) and path and not PurePosixPath(path).is_absolute()
        and '..' not in PurePosixPath(path).parts and '\\' not in path
        and isinstance(text, str) for path, text in files.items())


def load(path: Path = FIXTURES) -> dict:
    data = read_json(path)
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('cases'), list):
        raise ValueError('Unsupported coding fixture schema')
    cases = data['cases']
    if not 1 <= len(cases) <= 100:
        raise ValueError('Expected one to 100 small coding fixtures')
    seen_ids, seen_inputs, seen = set(), set(), {}
    for case in cases:
        if (case['split'] not in ('train', 'validation', 'gold') or case['category'] not in CATEGORIES
                or case['id'] in seen_ids or case['tenant_id'] != PLATFORM or case['product'] != PRODUCT):
            raise ValueError('Invalid split, identity or platform-only scope')
        seen_ids.add(case['id'])
        input_hash = digest(case['input'])
        if input_hash in seen_inputs:
            raise ValueError('Duplicate coding input')
        seen_inputs.add(input_hash)
        source = case['source']
        if (source['kind'] not in ('public', 'synthetic') or source['provider'] not in ('public-code', 'synthetic-fixture')
                or not all(isinstance(source[k], str) and source[k].strip()
                           for k in ('reference', 'revision', 'licence', 'provider'))
                or not valid_files(source['files']) or not valid_files(case['input']['files'])
                or not valid_files(case['expected']['files'])):
            raise ValueError('Private or unprovenanced data is forbidden')
        if (source['sha256'] != source_hash(source)
                or source['provenance_sha256'] != provenance_hash(source)):
            raise ValueError('Source integrity check failed')
        if source['kind'] == 'public':
            raw = (path.parent / 'sampleproject-simple.py.txt').read_bytes()
            licence = (path.parent / 'sampleproject-LICENSE.txt').read_bytes()
            if (source.get('raw_sha256') != hashlib.sha256(raw).hexdigest()
                    or source.get('licence_sha256') != hashlib.sha256(licence).hexdigest()
                    or source['files'] != {'src/sample/simple.py': raw.decode()}):
                raise ValueError('Public code or licence evidence hash mismatch')
        if case['split'] == 'train' and (source['kind'] != 'public' or source['licence'] != 'MIT'):
            raise ValueError('Only licensed public code may enter this training baseline')
        # A changed answer, renamed task, or rewrapped provenance cannot move a source
        # across splits. Input and individual source-file hashes also catch copies.
        fingerprints = [source['sha256'], source['provenance_sha256'], digest(case['input']),
                        digest(case['expected'])]
        fingerprints += [digest(text) for text in source['files'].values() if text.strip()]
        fingerprints += [digest(text) for text in case['input']['files'].values() if text.strip()]
        for fingerprint in fingerprints:
            if fingerprint in seen and seen[fingerprint] != case['split']:
                raise ValueError('Source or held-out content overlaps splits')
            seen[fingerprint] = case['split']
        if case['expected']['commands'] and any(command not in SAFE_COMMANDS for command in case['expected']['commands']):
            raise ValueError('Reference contains an unapproved command')
        if set(case['expected']) != {'files', 'commands'}:
            raise ValueError('Unsupported reference answer')
    if {case['split'] for case in cases} != {'train', 'validation', 'gold'}:
        raise ValueError('All three splits are required')
    return data


def snapshots(data: dict) -> dict:
    result = {}
    for split in ('train', 'validation', 'gold'):
        rows = []
        for case in data['cases']:
            if case['split'] != split:
                continue
            source = case['source']
            rows.append(dict(tenant_id=PLATFORM, product=PRODUCT, kind='public',
                source=source['reference'], provider=source['provider'], licence=source['licence'],
                source_ref=source['revision'] + ':' + source['sha256'],
                task_type=PRODUCT + '.' + case['category'], input=case['input'],
                output=case['expected'], holdout=split != 'train'))
        rows.sort(key=digest)
        result[split] = dict(rows=rows, count=len(rows), sha256=digest(rows))
    return result


def shared_bundle(data: dict) -> dict:
    """Consume M7/M14 when merged; no vendored implementation or DB reads.

    This small smoke set deliberately fails M9's 200-reviewed-case launch gate.
    """
    from tervor_dataset import build
    from tervor_gold import verify
    parts = snapshots(data)
    heldout = [row['input'] for split in ('validation', 'gold') for row in parts[split]['rows']]
    training = build(PLATFORM, PRODUCT, parts['train']['rows'], gold_inputs=heldout)
    cases = [dict(id=case['id'], input=case['input'], expected=case['expected'],
        task_type=PRODUCT + '.' + case['category'], tolerance=0, source=case['source']['reference'],
        source_ref=case['source']['revision'], provider=case['source']['provider'],
        licence=case['source']['licence'], holdout=True)
        for case in data['cases'] if case['split'] == 'gold']
    cases.sort(key=lambda case: digest(case['input']))
    gold = dict(tenant_id=PLATFORM, product=PRODUCT, region='us-east', holdout=True,
                cases=cases, count=len(cases), sha256=digest(cases))
    verify(gold, PLATFORM, PRODUCT)
    return dict(training=training, gold=gold, sha256=digest([training, gold]))


def score(case: dict, answer: object) -> dict:
    shape = isinstance(answer, dict) and set(answer) == {'files', 'commands'}
    files = answer.get('files') if shape else None
    commands = answer.get('commands') if shape else None
    paths = valid_files(files)
    syntax = paths
    if paths:
        for path, text in files.items():
            if path.endswith('.py'):
                try:
                    ast.parse(text)
                except (SyntaxError, ValueError, RecursionError):
                    syntax = False
    safe = isinstance(commands, list) and all(command in SAFE_COMMANDS for command in commands)
    exact = shape and answer == case['expected']
    return dict(id=case['id'], category=case['category'], exact_patch=exact,
                syntax=bool(syntax), command_safety=safe, passed=bool(exact and syntax and safe))


def compare(data: dict, predictions: dict | None = None, *, split: str = 'gold') -> dict:
    if split not in ('validation', 'gold'):
        raise ValueError('Compare only held-out splits')
    cases = sorted((case for case in data['cases'] if case['split'] == split), key=lambda case: case['id'])
    if predictions is not None and (not isinstance(predictions, dict) or set(predictions) != {c['id'] for c in cases}):
        raise ValueError('Every held-out task needs exactly one answer; unknown IDs refused')
    variants = {'no_change': {c['id']: {'files': c['input']['files'], 'commands': []} for c in cases},
                'reference_oracle': {c['id']: c['expected'] for c in cases}}
    if predictions is not None:
        variants['candidate'] = predictions
    results = {}
    for name, answers in variants.items():
        rows = [score(case, answers[case['id']]) for case in cases]
        passed = sum(row['passed'] for row in rows)
        results[name] = dict(n=len(rows), passed=passed, agreement=passed / len(rows), rows=rows)
    return dict(schema=1, product=PRODUCT, model_code='tervor-code', method='exact_coding_patch',
        split=split, gold_holdout=split == 'gold', data_sha256=digest(data),
        gold_sha256=snapshots(data)['gold']['sha256'], comparisons=results,
        eligible=False, status='offline_only', cost_usd=0, ledger_ids=[],
        reason='Exact patch/syntax smoke test; no behavioral execution, model inference, DeepEval or promotion evidence')


def preflight(data: dict) -> dict:
    # MODEL.md gives no exact coding checkpoint. Do not infer one from Lite's
    # legacy Qwen3-4B default, ambient HF settings, or a tiny CI test checkpoint.
    parts = snapshots(data)
    return dict(status='blocked', phase='training_preflight', model_code='tervor-code', product=PRODUCT,
        proposed_id=identity('tervor-code-1.0', PRODUCT, '2026-11-08'),
        base_model=None, base_revision=None, base_licence=None,
        base_evidence='tervory-build/MODEL.md: Tervor Code: a Qwen coder base; exact choice undecided',
        missing=['Founder-approved coding base model ID, immutable revision and commercial licence evidence',
                 'Merged coding-capable training interface (current M9 accepts only Zenas/Base/Tiger)',
                 'At least 200 independently reviewed gold cases and calibrated Tank/DeepEval evaluation'],
        split_hashes={key: value['sha256'] for key, value in parts.items()},
        split_counts={key: value['count'] for key, value in parts.items()},
        cost_usd=None, cost_reason='Base, hardware fit, quote and runtime are not determined',
        budget_ceiling_usd=100, paid_jobs=0, submitted=False, eligible=False)


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', required=True)
    parser.add_argument('--predictions', type=Path)
    parser.add_argument('--split', choices=('validation', 'gold'), default='gold')
    args = parser.parse_args(argv)
    data = load()
    predictions = read_json(args.predictions) if args.predictions else None
    print(encoded(dict(evaluation=compare(data, predictions, split=args.split), preflight=preflight(data))))
    return 2  # Machine-readable blocked preflight, never a successful training launch.


if __name__ == '__main__':
    sys.exit(cli())
