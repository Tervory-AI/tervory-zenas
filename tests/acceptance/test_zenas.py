"""One offline implementation-path case per US/ZA founder-sheet row.

Saved gateway output, in-memory regional stores and existing fake transports only.
No live models, production writes, skill approval or feature activation.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from pathlib import Path
import re
import subprocess
from unittest.mock import AsyncMock

import pytest

from agents.zenas.core import engine, selftest, service
from agents.zenas.core.ladder_store import LadderMemory
from agents.zenas.core.review_switch import card_servable, review_required
from agents.zenas.core.scope_rules import classify
from agents.zenas.core.store import MemoryStore, register_home

ROOT = Path(__file__).resolve().parents[2]

ROWS = [
    ('Z01', 'us-east', 'console'),
    ('Z02', 'us-east', 'scope'),
    ('Z03', 'us-east', 'home'),
    ('Z04', 'us-east', 'answer'),
    ('Z05', 'us-east', 'review'),
    ('Z06', 'us-east', 'dates'),
    ('Z07', 'us-east', 'fake'),
    ('Z08', 'us-east', 'reuse'),
    ('Z09', 'us-east', 'version'),
    ('Z10', 'us-east', 'draft'),
    ('Z11', 'us-east', 'slots'),
    ('Z12', 'us-east', 'guide'),
    ('Z13', 'us-east', 'reviewdoc'),
    ('Z14', 'us-east', 'convert'),
    ('Z15', 'us-east', 'selftest'),
    ('Z16', 'us-east', 'outage'),
    ('Z17', 'us-east', 'brain'),
    ('Z18', 'us-east', 'release'),
    ('Z19', 'za', 'console'),
    ('Z20', 'za', 'scope'),
    ('Z21', 'za', 'home'),
    ('Z22', 'za', 'answer'),
    ('Z23', 'za', 'review'),
    ('Z24', 'za', 'dates'),
    ('Z25', 'za', 'fake'),
    ('Z26', 'za', 'reuse'),
    ('Z27', 'za', 'version'),
    ('Z28', 'za', 'draft'),
    ('Z29', 'za', 'slots'),
    ('Z30', 'za', 'guide'),
    ('Z31', 'za', 'reviewdoc'),
    ('Z32', 'za', 'convert'),
    ('Z33', 'za', 'selftest'),
    ('Z34', 'za', 'outage'),
    ('Z35', 'za', 'brain'),
    ('Z36', 'za', 'release'),
    ('Z37', 'us-east', 'sources'),
    ('Z38', 'us-east', 'usnight'),
    ('Z39', 'us-east', 'usdeadline'),
    ('Z40', 'us-east', 'watch'),
    ('Z41', 'za', 'zasources'),
    ('Z42', 'za', 'zanight'),
    ('Z43', 'za', 'resume'),
    ('Z44', 'za', 'zadeadline'),
    ('Z45', 'za', 'kb'),
    ('Z46', 'za', 'split'),
    ('Z47', 'za', 'cap'),
    ('Z48', 'za', 'exam'),
    ('Z49', 'za', 'promotion'),
    ('Z50', 'za', 'ownkey'),
    ('Z51', 'us-east', 'links'),
    ('Z52', 'za', 'links'),
    ('Z53', 'za', 'zawatch'),
    ('Z54', 'za', 'meaning'),
]


async def scenario(module, name, *args):
    """Keep regression harness assertions; only external services are fixtures."""
    function = getattr(importlib.import_module(module), name)
    if inspect.iscoroutinefunction(function):
        await function(*args)
    else:
        await asyncio.to_thread(function, *args)


async def cited_answer(region, monkeypatch):
    monkeypatch.setenv('ZENAS_REVIEW', 'off')
    ladder = LadderMemory()
    tenant = '11111111-1111-4111-8111-111111111111'
    await ladder.register_tenant(tenant, region)
    store = MemoryStore()
    await store.register_tenant(tenant, region)
    matter = await store.add_matter({'tenant_id': tenant, 'jurisdiction': 'za' if region == 'za' else 'us',
                                    'area': 'contracts', 'title': 'Fixture civil supply', 'parties': [],
                                    'status': 'open', 'created_by': 'founder'})
    citation = '[2026] ZACAC 108' if region == 'za' else '1 F.2d 108'
    quote = 'The written supply agreement binds both parties to its commercial terms.'
    passage = {'doc_id': 'fixture-document', 'version_id': 'fixture-version',
               'citation': citation, 'text': quote, 'status': 'effective'}
    gateway = AsyncMock()
    gateway.complete.return_value = {'content': json.dumps({'elements': [
        {'id': 'written', 'test': 'The agreement is written.', 'citation': citation, 'quote': quote}],
        'decision_table': [{'element_id': 'written', 'when': {'written': True}, 'finding': 'met'}]}),
        'model': 'fixture-model', 'cost_usd': 0}
    result = await service.answer_question(store, ladder, gateway, tenant_id=tenant,
        home_region=region, box_region=region, jurisdiction='za' if region == 'za' else 'us',
        provider='za-local' if region == 'za' else 'luna',
        question=f'What did {citation} hold about the civil supply agreement?',
        passages=[passage], features={'written': True}, matter_id=matter['id'])
    assert result['ok'] and result['delivered'] and not result['reviewed']
    assert quote in result['memo']['text'] and citation in result['memo']['text']
    assert service._LEGAL_NOTICE in result['memo']['text']


@pytest.mark.asyncio
@pytest.mark.parametrize('ident,region,feature', ROWS, ids=[row[0] for row in ROWS])
async def test_zenas_founder_row(ident, region, feature, monkeypatch, caplog):
    monkeypatch.setenv('TANK_REGION', region)
    jurisdiction = 'za' if region == 'za' else 'us'
    if feature == 'console' and region == 'za':
        await regional_queue()
    elif feature == 'console':
        rendered = await asyncio.to_thread(subprocess.run,
            ['node', 'tests/acceptance/zenas_review.cjs', region], cwd=ROOT,
            capture_output=True, text=True, timeout=60)
        assert rendered.returncode == 0, rendered.stdout + rendered.stderr
        assert 'PASS reviewer page ' + region in rendered.stdout
        await scenario('test_zenas_review_console', 'test_the_console_shows_the_locked_worked_example')
        await scenario('test_zenas_review_console', 'test_a_reviewer_opens_the_console_and_the_logos_stay_official')
    elif feature == 'scope':
        assert classify('Defend criminal charges and sentencing.', jurisdiction=jurisdiction)['in_scope'] is False
        assert classify('A civil supply agreement dispute.', jurisdiction=jurisdiction)['in_scope'] is True
        await scenario('test_zenas_z1', 'test_criminal_question_never_reaches_another_skill')
    elif feature == 'home':
        store = MemoryStore()
        tenant = '11111111-1111-4111-8111-111111111111'
        first = await register_home(store, tenant_id=tenant, home_region=region, box_region=region)
        assert first['ok'] and first['created']
        foreign = 'us-east' if region == 'za' else 'za'
        result = await register_home(store, tenant_id=tenant, home_region=region, box_region=foreign)
        assert not result['ok'] and len(store.tenants) == 1
        changed = await register_home(store, tenant_id=tenant, home_region=foreign, box_region=foreign)
        assert not changed['ok'] and store.tenants[0]['home_region'] == region
    elif feature == 'answer':
        await cited_answer(region, monkeypatch)
    elif feature == 'review':
        ladder = LadderMemory()
        await ladder.set_review(region=region, review='on')
        assert await review_required(ladder, region=region)
        assert not card_servable({'status': 'draft', 'active': True}, review_on=True)
        assert card_servable({'status': 'lawyer_approved'}, review_on=True)
        assert not card_servable({'status': 'stale', 'active': True}, review_on=False)
    elif feature == 'dates':
        await scenario('test_zenas_rule_card_dated_quotes', 'test_true_dated_quote_passes_and_is_shown_masked')
        await scenario('test_zenas_rule_card_dated_quotes', 'test_true_patent_number_quote_passes_and_an_altered_one_fails')
        await scenario('test_zenas_rule_card_dated_quotes', 'test_engine_defaults_are_mains_behaviour')
    elif feature == 'fake':
        statement = {'citation': 'Invented v Nobody, 99 F.2d 999', 'quote': 'Invented court words.'}
        assert not engine.cite_check([statement], [])['passed']
        await scenario('test_zenas_rule_card_dated_quotes', 'test_masked_card_quote_cannot_be_attributed_to_another_case')
        await scenario('test_zenas_rule_card_dated_quotes', 'test_masked_quote_without_the_right_fingerprint_fails')
    elif feature == 'reuse':
        await scenario('test_zenas_assembly', 'test_released_prior_with_the_same_facts_is_reused_word_for_word', region)
        await scenario('test_zenas_ladder', 'test_second_client_reuses_the_approved_card_for_free')
    elif feature == 'version':
        await versioned_law(region)
    elif feature == 'draft':
        from agents.zenas.core.drafting import TEMPLATES
        for template in TEMPLATES:
            await scenario('test_zenas_assembly', 'test_every_template_drafts_a_real_word_file_end_to_end', region, template)
    elif feature == 'slots':
        await scenario('test_zenas_assembly', 'test_missing_fields_and_missing_clause_are_marked_in_word_as_tracked_insertions', region)
    elif feature == 'guide':
        store = MemoryStore()
        tenant, other = 'fixture-tenant', 'fixture-other'
        await store.register_tenant(tenant, region)
        await store.register_tenant(other, region)
        for owner in (tenant, other):
            added = await service.add_playbook(store, tenant_id=owner, home_region=region,
                box_region=region, name=owner, template='letter',
                positions=[{'kind': 'subject', 'text': 'Fixture review position.'}])
            assert added['ok']
        listed = await service.list_playbooks(store, tenant_id=tenant, home_region=region, box_region=region)
        assert [item['name'] for item in listed['items']] == [tenant]
    elif feature == 'reviewdoc':
        await regional_review(region)
    elif feature == 'convert':
        await scenario('test_zenas_z8', 'test_fixture_cases_become_worked_matters_with_about_one_in_five_held_out')
        await scenario('test_zenas_z8', 'test_us_conversion_never_reads_an_sa_case_and_the_reverse')
    elif feature == 'selftest':
        from test_zenas_z5_selftest import Gateway, library
        cases = library()
        if region == 'us-east':
            cases = [{**case, 'citation': f'1 F.2d {index + 1}'} for index, case in enumerate(cases)]
        result = await selftest.run_made_up_test(LadderMemory(), model=Gateway(), region=region, library=cases)
        assert result['passed'] == result['asked'] == 50
        assert result['skills_may_be_approved']
    elif feature == 'outage':
        import httpx
        await scenario('test_zenas_z5_selftest', 'test_gateway_failures_return_unavailable_with_safe_metadata',
                       httpx.ReadTimeout('fixture private prompt'), None)
        await scenario('test_zenas_z5_selftest', 'test_structured_paid_refusal_keeps_only_known_code')
        await scenario('test_zenas_z5_selftest', 'test_diagnostics_reject_arbitrary_fields_and_messages')
    elif feature == 'brain':
        await scenario('zenas_brain.test_brain', 'test_compose_keeps_both_runtimes_off_hardened_and_box_local', region)
        await scenario('zenas_brain.test_brain', 'test_standby_cannot_dispatch_any_matter', monkeypatch, region)
    elif feature == 'release':
        if region == 'za':
            await scenario('test_zenas_z1', 'test_za_tenant_needs_consent_on_the_za_box')
        await scenario('test_zenas_z1', 'test_other_tenant_cannot_read_the_matter')
        await scenario('test_zenas_release_mask', 'test_court_judgments_stay_byte_identical')
        await scenario('test_zenas_release_mask', 'test_released_client_work_is_masked_and_the_original_stays_private', monkeypatch, caplog)
    elif feature == 'sources':
        await scenario('test_zenas_z7', 'test_saved_responses_parse_to_many_documents')
    elif feature == 'usnight':
        await scenario('test_zenas_us_nightly_budget', 'test_progressing_bulk_yields_preserves_checkpoint_and_runs_later_source', monkeypatch)
    elif feature == 'usdeadline':
        await scenario('test_zenas_deadlines', 'test_frcp_fourteen_days_include_the_last_weekday')
        await scenario('test_zenas_deadlines', 'test_frcp_rolls_a_weekend_and_an_observed_holiday')
    elif feature == 'watch':
        await scenario('zenas_regulation_alerts.test_alerts', 'test_federal_register_paging_and_changed_version')
        from zenas_regulation_alerts.test_alerts import setup_watch, tick, FR, TENANT
        from types import SimpleNamespace
        from chassis import events
        monkeypatch.setattr(events, 'publish', AsyncMock(return_value=True))
        mailbox = SimpleNamespace(send=AsyncMock())
        delivery = {'transport': mailbox, 'recipient': 'fixture@example.test'}
        store, watch, http, tank = await setup_watch()
        assert (await tick(store, http, tank, 0, **delivery))['changes'] == 0
        http.rows = [FR[2]]
        assert (await tick(store, http, tank, 2, **delivery))['changes'] == 0
        assert not await store.changes(TENANT)
        http.rows = [FR[0], FR[1]]
        change = await tick(store, http, tank, 3, **delivery)
        assert change['changes'] == change['sent'] == 1
        again = await tick(store, http, tank, 3, **delivery)
        assert again['changes'] == again['sent'] == 0
        assert mailbox.send.await_count == 1
        changes = await store.changes(TENANT)
        assert len(changes) == 1 and changes[0]['watch_id'] == watch['id']
    elif feature == 'zasources':
        await scenario('test_zenas_judgment_training', 'test_every_training_source_is_an_official_registered_full_text_adapter')
    elif feature == 'zanight':
        await scenario('test_zenas_za_nightly_deadline', 'test_whole_night_defers_unreached_sources_and_prioritises_them_tomorrow', monkeypatch)
    elif feature == 'resume':
        from test_zenas_pool_nightly_archive_streaming import _quick
        _quick.__wrapped__(monkeypatch)
        await scenario('test_zenas_za_nightly_deadline',
            'test_archive_yields_at_five_minutes_and_resumes_only_unacknowledged_files', monkeypatch, None)
        await scenario('test_zenas_za_nightly_deadline', 'test_convert_judgment_training_and_later_jobs_share_the_run_deadline', monkeypatch)
    elif feature == 'zadeadline':
        await scenario('test_zenas_deadlines_service', 'test_service_date_controls_sa_even_when_order_date_is_earlier')
        await scenario('test_zenas_deadlines_service', 'test_missing_or_invalid_service_never_uses_order', None)
    elif feature == 'kb':
        import httpx
        await scenario('test_zenas_z5_selftest', 'test_kb_failure_retains_library_and_marks_answer', monkeypatch, httpx.ReadError('fixture outage'))
    elif feature == 'split':
        await scenario('test_zenas_court_training_handoff', 'test_splitter_reaches_writer_with_owning_room_key', monkeypatch)
        await scenario('test_zenas_judgment_training', 'test_headed_judgment_uses_only_court_sections_and_order_label')
        await scenario('test_zenas_judgment_training', 'test_unheaded_and_criminal_judgments_are_excluded_without_guessing')
        await scenario('test_zenas_judgment_training', 'test_nightly_split_path_calls_only_tank_court_text_writer', monkeypatch)
        await training_counts(monkeypatch)
    elif feature == 'cap':
        await scenario('test_grad1_hf_training', 'test_refuses_bad_or_over_budget_job', '10', 120, '1')
        await scenario('test_grad1_hf_training', 'test_training_run_and_receipt_refuse_tenant_admin_before_provider', monkeypatch)
        await scenario('test_grad1_hf_training', 'test_private_or_cross_region_never_exports', {'client_matter': True}, monkeypatch)
    elif feature == 'exam':
        await scenario('test_zenas_training_gate3', 'test_outcome_scorer_requires_exact_code_label')
        await scenario('test_zenas_training_gate3', 'test_outcome_scorer_rejects_missing_or_guessed_labels')
    elif feature == 'promotion':
        await scenario('test_zenas_training_gate3', 'test_gate_needs_both_99_percent_and_200_rows')
    elif feature == 'meaning':
        await meaning_search(monkeypatch)
    elif feature == 'links':
        await scenario('test_zenas_citation_source_links',
            'test_source_metadata_uses_each_box_region_and_exact_version', region, jurisdiction)
        await scenario('test_zenas_citation_source_links', 'test_link_comes_from_the_quote_checked_document_version')
        await scenario('test_zenas_citation_source_links', 'test_nonpublic_or_credential_source_links_are_not_returned',
                       'https://example.com/?api_key=fixture')
    elif feature == 'zawatch':
        await za_watch(monkeypatch)
        await scenario('test_zenas_w3', 'test_failed_laws_africa_uses_loaded_govza_with_own_attribution')
    elif feature == 'ownkey':
        await scenario('test_zenas_training_gate3', 'test_own_model_is_sealed_and_disabled_by_default')
        import yaml
        us = yaml.safe_load((ROOT / 'litellm/config.yaml').read_text())
        za = yaml.safe_load((ROOT / 'litellm/config.za.yaml').read_text())
        assert not any(row.get('model_info', {}).get('owned') for row in us['model_list'])
        owned = [row for row in za['model_list'] if row.get('model_info', {}).get('owned')]
        assert len(owned) == 1 and owned[0]['model_info']['hosted_in'] == 'za'
        assert owned[0]['litellm_params']['api_key'] == 'os.environ/ZENAS_OWN_MODEL_KEY'
    else:
        pytest.fail(f'{ident}: no executable implementation path for {feature}')


def test_sheet_has_exactly_one_fixture_case_per_row():
    sheet = (ROOT / 'docs/acceptance/zenas.md').read_text()
    ids = re.findall(r'^\| (Z\d\d) \|', sheet, flags=re.M)
    assert ids == [row[0] for row in ROWS]
    assert len(ids) == len(set(ids)) == 54
    for ident, region, _ in ROWS:
        line = next(line for line in sheet.splitlines() if line.startswith(f'| {ident} |'))
        assert f'| {"ZA" if region == "za" else "US"} — ' in line
        settings = line.split('|')[-2].strip()
        inventory = (ROOT / 'docs/ops/FOUNDER-SETTINGS.md').read_text()
        assert settings == 'none' or all(name.strip() in inventory for name in settings.split(','))


async def za_watch(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from chassis import events
    from agents.zenas.core.regulation_sources import Sources, document
    from agents.zenas.core.regulation_store import MemoryWatches
    from agents.zenas.core.regulations import add_watch, run_daily
    from zenas_regulation_alerts.test_alerts import TankStub

    monkeypatch.setenv('LAWS_AFRICA_API_TOKEN', 'fixture-placeholder')
    monkeypatch.setattr(events, 'publish', AsyncMock(return_value=True))
    store, tenant = MemoryWatches(), '11111111-1111-4111-8111-111111111111'
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    watch = await add_watch(store, tenant_id=tenant, region='za', source='laws-africa',
                            topic='companies', options={}, now=now)
    version = 'initial'

    async def snapshot(saved):
        assert saved['tenant_id'] == tenant
        return [document('govza-acts', 'fixture-act', 'Companies Act',
                         'The companies rule requires a written notice. ' + version,
                         'https://www.gov.za/documents/fixture-act', '2026-11-01' if version == 'initial' else '2026-11-05',
                         version=version, kind='act', attribution='Official public record')]

    class Mailbox:
        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(message)

    mailbox = Mailbox()
    sources, tank = Sources('za', laws_snapshot=snapshot), TankStub()
    first = await run_daily(store, sources, tank, region='za', now=now,
                            transport=mailbox, recipient='fixture@example.test')
    assert first['changes'] == 0
    version = 'changed'
    changed = await run_daily(store, sources, tank, region='za', now=now + timedelta(days=1),
                              transport=mailbox, recipient='fixture@example.test')
    assert changed['changes'] == changed['sent'] == 1
    again = await run_daily(store, sources, tank, region='za', now=now + timedelta(days=1),
                            transport=mailbox, recipient='fixture@example.test')
    assert again['changes'] == again['sent'] == 0
    changes = await store.changes(tenant)
    assert len(changes) == len(mailbox.messages) == 1
    assert changes[0]['watch_id'] == watch['id']
    assert changes[0]['document']['attribution'] == 'Official public record'


async def versioned_law(region):
    from datetime import date
    from agents.zenas.pool.builddoc import build_document
    from agents.zenas.pool.catalogue import sources_for_region
    from agents.zenas.pool.chunk import chunk_document
    from agents.zenas.pool.model import Section
    from agents.zenas.pool.store import MemoryStore as Pool

    source = next(row for row in sources_for_region(region) if row.jurisdiction == ('za' if region == 'za' else 'us'))
    pool = Pool(home_region=region)
    await pool.record_source(source)
    for year, wording in ((2020, 'Earlier commercial wording.'), (2024, 'Current commercial wording.')):
        on = date(year, 1, 1)
        document = build_document(source, citation='Fixture Act section 2', title='Fixture civil act',
            level='national' if region == 'za' else 'federal', kind='statute', body='Official fixture',
            sections=[Section('(a)', 'Commercial rule', wording, [])],
            url=source.canonical_url, status='in_force', doc_date=on, effective_from=on)
        assert await pool.upsert_version(document, chunk_document(document), on=on)
    jurisdiction = 'za' if region == 'za' else 'us'
    past = await pool.find_by_citation('Fixture Act section 2(a)', jurisdiction=jurisdiction, as_at=date(2022, 1, 1))
    current = await pool.find_by_citation('Fixture Act section 2(a)', jurisdiction=jurisdiction, as_at=None)
    assert past and current
    assert 'Earlier commercial wording.' in past[0].text
    assert 'Current commercial wording.' in current[0].text
    assert past[0].version_id != current[0].version_id


async def training_counts(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import UUID
    import training

    class Database:
        @asynccontextmanager
        async def acquire(self):
            yield self

        async def fetch(self, sql, *args):
            if 'zenas_pool.documents' in sql:
                assert set(args[0]) == set(training.OFFICIAL_ZA_CASE_SOURCES)
                return [{'source_id': 'concourt', 'count': 3}]
            if 'judgment_splits' in sql:
                return [{'source_id': 'concourt', 'status': 'split', 'count': 2},
                        {'source_id': 'concourt', 'status': 'excluded', 'count': 1}]
            if 'split_part(source_ref' in sql:
                assert "source='court_text'" in sql and 'NOT client_matter AND NOT holdout' in sql
                assert args == (training.PLATFORM_TENANT,)
                return [{'source_id': 'concourt', 'count': 1}]
            return []

        async def fetchrow(self, *_):
            return None

    monkeypatch.setattr(training, 'runtime', lambda: SimpleNamespace(box_region=lambda: 'za', pool=lambda: Database()))
    result = await training.training_status(training.Reviewer('fixture-founder', UUID(int=1), True))
    counts = {item['name']: item['count'] for item in result['items']}
    assert {name: counts['concourt ' + name] for name in ('judgments stored', 'split', 'excluded', 'pairs ready')} == {
        'judgments stored': 3, 'split': 2, 'excluded': 1, 'pairs ready': 1}
    assert len(counts) == 4 * len(training.OFFICIAL_ZA_CASE_SOURCES)


async def regional_review(region):
    from test_zenas_draft import Model, _docx
    store = MemoryStore()
    tenant, other = '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'
    for owner in (tenant, other):
        await store.register_tenant(owner, region)
    approved = 'Keep the mutual NDA confidential for two years.'
    supplied = 'Keep the mutual NDA confidential for five years.'
    guide = await service.add_playbook(store, tenant_id=tenant, home_region=region,
        box_region=region, name='Fixture NDA guide', template='nda',
        positions=[{'kind': 'confidentiality', 'stance': 'must', 'text': approved}])
    assert guide['ok']
    for owner in (tenant, other):
        result = await service.review_contract(store, Model('low'), tenant_id=owner,
            home_region=region, box_region=region, playbook_id=guide['playbook']['id'], text=supplied)
        if owner == other:
            assert not result['ok'] and result['reason'] == 'no such playbook'
        else:
            assert result['ok'] and result['deviations'] == 1
            assert result['inserts'] == [approved]
            xml = _docx(result)
            assert 'w:ins' in xml and 'w:del' in xml
            assert supplied in xml and approved in xml


async def regional_queue():
    store, ladder = MemoryStore(), LadderMemory()
    tenant, other = 'fixture-za-owner', 'fixture-za-other'
    for owner in (tenant, other):
        await store.register_tenant(owner, 'za')
        matter = await store.add_matter({'tenant_id': owner, 'jurisdiction': 'za',
            'title': owner, 'area': 'contracts', 'status': 'open', 'parties': [], 'created_by': 'founder'})
        await store.add_document({'tenant_id': owner, 'matter_id': matter['id'], 'version': 1,
            'kind': 'draft', 'body': owner + ' civil draft', 'state': 'in_review',
            'sources': [{'citation': 'Fixture official judgment', 'quote': 'Fixture court words.'}]})
    result = await service.review_queue(store, ladder, tenant_id=tenant, home_region='za',
                                        box_region='za', jurisdiction='za')
    assert result['ok']
    assert len(result['items']) == 1 and result['items'][0]['matter']['title'] == tenant
    assert result['items'][0]['document']['sources'][0]['citation'] == 'Fixture official judgment'
    assert other not in json.dumps(result)


async def meaning_search(monkeypatch):
    import sys
    from agents.zenas.pool.adapters import laws_africa_kb
    hermes = importlib.import_module('zenas_za_meaning_search.test_hermes_law')
    monkeypatch.setitem(sys.modules, 'test_hermes_law', hermes)
    retrieval = importlib.import_module('zenas_za_meaning_search.test_retrieval')
    monkeypatch.setattr(laws_africa_kb, 'za_passages', AsyncMock(return_value=[]))
    services = retrieval.services.__wrapped__(monkeypatch)
    try:
        await retrieval.test_everyday_question_reaches_actual_ask_bridge_with_exact_citation_provenance(
            monkeypatch, next(services))
    finally:
        services.close()
