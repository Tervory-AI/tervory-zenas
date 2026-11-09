"""Independent finite-expression verification; arbitrary Python stays ungraded."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess

from tank import tervor_code as baseline
from tank import tervor_code_behaviour as observation
from tank import tervor_code_expression as semantics
from tank.tervor_registry import digest, encoded

INTERPRETER = Path(semantics.__file__)
SUPPORTED = {'sample-add-one': 'number', 'gold-bug': 'n'}
CONTROLS = {'reference_expression': {'sample-add-one': 'number + 1', 'gold-bug': '0 <= n <= 10'},
            'wrong_expression': {'sample-add-one': 'number - 1', 'gold-bug': '0 < n < 10'}}


def tasks() -> list[dict]:
    data = baseline.load()
    spec = observation.bounded_json(observation.SPEC)
    return [item for item in observation.suite(data, spec) if item['case']['id'] in SUPPORTED]


def verify(task_id: str, expression: object, sandbox: observation.Sandbox) -> dict:
    if type(task_id) is not str or task_id not in SUPPORTED:
        raise semantics.ContractError('unsupported_task')
    item = next(value for value in tasks() if value['case']['id'] == task_id)
    case, declared = item['case'], item['task']['tests']
    if case['id'] not in SUPPORTED:
        raise semantics.ContractError('unsupported_task')
    result = dict(id=case['id'], contract=semantics.CONTRACT, passed=False, rows=[],
        source_sha256=case['source']['sha256'], provenance_sha256=case['source']['provenance_sha256'],
        family_sha256=item['family_sha256'], tests_sha256=item['tests_sha256'],
        split=case['split'], execution_attested=False, arbitrary_python_grade=False)
    # All tests and expected values come from the sealed repository manifest.
    # Validate and compute every result before any transport interaction.
    verified = []
    try:
        for test in declared:
            if test['kind'] != 'call' or len(test['args']) != 1 or test.get('overrides'):
                raise semantics.ContractError('unsupported_test')
            inputs = {SUPPORTED[case['id']]: test['args'][0]}
            value = semantics.interpret(expression, inputs)
            verified.append((test, inputs, {'kind': 'return', 'value': value}))
    except semantics.ContractError as error:
        return dict(result, status='refused', reason=str(error))
    result['expression_sha256'] = digest(expression)
    if not sandbox.ready:
        return dict(result, status='infrastructure_error', reason='required_isolation_unavailable')
    for test, inputs, canonical in verified:
        try:
            # Only reviewed interpreter bytes are imported. The expression is an
            # argument to that interpreter; it can never become a source file.
            response = sandbox.run({'verified_contract.py': INTERPRETER.read_text()},
                dict(module='verified_contract', function='interpret', args=[expression, inputs], kind='call'))
        except (observation.InfrastructureError, OSError, subprocess.SubprocessError):
            return dict(result, status='infrastructure_error', reason='isolated_transport_failed')
        claim = response.get('untrusted_claim')
        transport_matches = (response['status'] == 'integrity_unverified'
                             and encoded(claim) == encoded(canonical))
        expected_matches = encoded(canonical) == encoded(test['expected'])
        result['rows'].append(dict(name=test['name'], transport_status=response['status'],
            transport_matches_verified_semantics=transport_matches,
            verified_semantics_match_expected=expected_matches,
            verified_value_sha256=digest(canonical), transport_claim_sha256=digest(claim),
            elapsed_seconds=response.get('elapsed_seconds'),
            passed=bool(transport_matches and expected_matches)))
    result['passed'] = bool(result['rows']) and all(row['passed'] for row in result['rows'])
    mismatch = any(not row['transport_matches_verified_semantics'] for row in result['rows'])
    return dict(result, status='transport_mismatch' if mismatch else (
        'verified_contract_pass' if result['passed'] else 'verified_contract_failure'))


def report(predictions: dict | None = None, *, sandbox: observation.Sandbox | None = None) -> dict:
    selected = tasks()
    if predictions is not None and (type(predictions) is not dict or set(predictions) != set(SUPPORTED)):
        raise semantics.ContractError('prediction_ids')
    sandbox = sandbox or observation.Sandbox()
    isolation = sandbox.probe()
    programs = {**CONTROLS}
    if predictions is not None:
        programs['candidate_expressions'] = predictions
    comparisons = {}
    for label, expressions in programs.items():
        rows = [verify(item['case']['id'], expressions[item['case']['id']], sandbox) for item in selected]
        comparisons[label] = dict(n=len(rows), passed=sum(row['passed'] for row in rows), rows=rows)
    return dict(schema=1, contract=semantics.CONTRACT,
        scope='Only bounded-expression semantics on declared inputs; not Python behavior or process attestation',
        product=baseline.PRODUCT, model_code='tervor-code', tenant_id=baseline.PLATFORM,
        isolation=isolation, comparisons=comparisons,
        interpreter_sha256=hashlib.sha256(INTERPRETER.read_bytes()).hexdigest(),
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        eligible=False, model_quality_measured=False, arbitrary_python_grading=False,
        execution_attested=False, cost_usd=0, training_preflight=baseline.preflight(baseline.load()))


def adversarial_proof() -> dict:
    """Focused closed-language and hostile-transport proof, never Python execution."""
    selected = 'gold-bug'
    scenarios = {
        'exact_envelope': '{"kind":"return","value":true}',
        'early_exit': '__import__("os")._exit(0)',
        'monkeypatch': 'setattr(__import__("json"), "dumps", lambda: True)',
        'introspection': '(0).__class__.__mro__',
        'stdout_forgery': '__import__("os").write(1, b\'{"kind":"return","value":true}\')',
    }
    class ForgedTransport:
        ready = True
        def run(self, files, test):
            # Forge the exact expected answer on EVERY declared input, including
            # inputs where the wrong program independently evaluates to False.
            n = test['args'][1]['n']
            return dict(status='integrity_unverified',
                        untrusted_claim={'kind': 'return', 'value': 0 <= n <= 10})
    fake = ForgedTransport()
    results = {name: verify(selected, source, fake) for name, source in scenarios.items()}
    results['forged_expected_transport_for_wrong_program'] = verify(selected, 'False', fake)
    class WrongTransport(ForgedTransport):
        def run(self, files, test):
            return dict(status='integrity_unverified', untrusted_claim={'kind': 'return', 'value': 16})
    results['forged_wrong_transport_for_correct_program'] = verify(selected, '0 <= n <= 10', WrongTransport())
    return dict(scenarios=results, all_refused_or_failed=all(not row['passed'] for row in results.values()))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', required=True)
    parser.add_argument('--predictions', type=Path)
    args = parser.parse_args(argv)
    value = report(observation.bounded_json(args.predictions) if args.predictions else None)
    value['focused_adversarial_proof'] = adversarial_proof()
    print(encoded(value))
    return 2 if value['isolation']['status'] == 'ready' else 3


if __name__ == '__main__':
    raise SystemExit(cli())
