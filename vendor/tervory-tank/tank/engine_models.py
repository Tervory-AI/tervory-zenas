"""Pinned founder catalog. Licences are decided by the founder's counsel (7 Nov 2026); no licence filter here."""
import csv
from pathlib import Path

SOURCE = 'Tervory-AI/tervory-build/models/open-weight-models.csv'
SOURCE_BLOB = '0c7f0357fc27d346dd8f8495e3b5485d6bba79ce'
LOCAL = ('local', 'local-large')
# DECISION reuse existing HF seats and their provider-specific price book.
# Catalog router minima are candidate estimates, not this provider's invoice.
# Cheapest first (roster rule); evals and calibration still decide what serves.
OPEN = ('llama-small', 'gpt-oss-small', 'qwen-small', 'gpt-oss', 'llama')
SELECTED = {
    'local': 'Qwen/Qwen2.5-1.5B-Instruct',
    'local-large': 'Qwen/Qwen3-4B-Instruct-2507',
    'gpt-oss-small': 'openai/gpt-oss-20b',
    'qwen-small': 'Qwen/Qwen3.5-9B',
    'gpt-oss': 'openai/gpt-oss-120b',
    'llama-small': 'meta-llama/Llama-3.1-8B-Instruct',
    'llama': 'meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8',
}


def candidates():
    with Path(__file__).with_name('model_candidates.csv').open(newline='') as file:
        return {r['model']: r for r in csv.DictReader(file)}


def validate():
    rows = candidates()
    for alias, model in SELECTED.items():
        row = rows[model]
        if alias in LOCAL and row['runs_on_Boston_CPU'] != 'yes':
            raise ValueError('Local candidate requires unverified free RAM or GPU capacity')
        if alias in OPEN and row['served_on_HF_router'] != 'yes':
            raise ValueError('Hosted candidate has no existing HF route')
    return rows


validate()
