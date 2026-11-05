import hashlib
import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from agents.zenas.core.engine import summarize

ROOT = Path(__file__).resolve().parents[2]


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.words = []

    def handle_data(self, data):
        self.words.append(data)


def rendered(mode):
    result = subprocess.run(['node', str(ROOT / 'tests/zenas_finish/gate4_render.mjs'), mode],
                            cwd=ROOT, text=True, capture_output=True, check=True)
    html = json.loads(result.stdout)['html']
    parsed = Text()
    parsed.feed(html)
    return html, ' '.join(parsed.words)


@pytest.mark.parametrize('mode', ['view', 'deadlines', 'feeds', 'review', 'matters', 'moot', 'public-moot'])
def test_every_empty_client_surface_has_real_empty_values_and_a_first_action(mode):
    html, text = rendered(mode)
    assert '0' in text or '—' in text
    assert any(action in text for action in ['Open a matter', 'Load now', 'Load a locked civil appeal problem', 'Moot Court'])
    for sample in ['Worked example', '2026-01-16', '2026-01-02', 'Fourteen days under FRCP', 'Alpha Shipping', 'Beta Carriers', 'Kanos', 'Mokoena']:
        assert sample not in text
    assert 'sample.json' not in html


def test_empty_scores_and_sources_keep_their_table_shape():
    html, text = rendered('feeds')
    assert html.count('<table>') == 2 and html.count('<thead>') == 2
    for column in ['Cases', 'Outcome', 'Elements', 'Citations', 'Cost', 'Documents', 'Status', 'Last load']:
        assert column in text
    assert 'Load now · Convert cases' in text
    assert '%' not in text


def test_zero_case_score_is_unknown_instead_of_a_fabricated_percentage():
    _html, text = rendered('feeds-zero')
    assert '99%' not in text and '100%' not in text and '0%' not in text
    assert '—' in text


def test_nonempty_sources_and_measured_scores_preserve_real_values():
    _html, text = rendered('feeds-data')
    assert '42' in text and '99%' in text and '100%' in text
    assert 'Federal Rules of Civil Procedure' in text


def test_new_deadlines_do_not_pretend_alerts_have_been_configured():
    _html, text = rendered('deadlines')
    assert 'Upcoming' in text and 'Alerts' in text
    assert 'Open a matter' in text
    assert '5 court days before' not in text and '1 court day before' not in text


def test_empty_moot_receipt_never_claims_zero_billed_cost():
    _html, text = rendered('moot')
    assert 'Cost: —' in text
    assert '$0' not in text


def test_a_new_tenant_has_zero_activity_in_every_metric():
    values = summarize(runs=[], cards=[], worked_count=0, pair_count=0)
    assert values and all(value == 0 for value in values.values())
    assert {'worked_matters', 'training_pairs', 'first_answer_cost_usd', 'later_matter_cost_usd'} <= values.keys()


def test_existing_finish_patch_remains_byte_for_byte_unchanged():
    content = (ROOT / 'tank-patches/finish.patch').read_bytes()
    assert hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest() == '3c0003eb7cd32269f889e290ec6666c434ff5cbf'


def test_client_configuration_messages_name_only_recognized_settings():
    run = subprocess.run(['node', str(ROOT / 'tests/zenas_finish/gate4_render.mjs'), 'configuration'],
                         cwd=ROOT, text=True, capture_output=True, check=True)
    result = json.loads(run.stdout)
    assert result['note'] == result['review'] == result['ask']['message'] == 'Missing setting: DATABASE_URL, TANK_KEY, ZENAS_PII_KEY'
    assert result['unknown'] == ''
    assert 'placeholder-private-value' not in run.stdout and 'placeholder hidden' not in run.stdout
