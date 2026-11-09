"""Round-one preparation. Dry-run has no HF client and cannot submit a job."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import date
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
import tervor_dataset
import tervor_gold
import tervor_registry

PRODUCTS = ('zenas', 'base', 'tiger')
PLATFORM = UUID('00000000-0000-0000-0000-000000000001')


def bundle(tenant, product, training, gold):
    cases = tervor_gold.verify(gold, tenant, product)
    if len(cases) < 200:
        raise ValueError('Round one needs at least 200 distinct gold cases per product')
    snapshot = tervor_dataset.build(tenant, product, training, gold_inputs=[case['input'] for case in cases])
    if not snapshot['count']:
        raise ValueError('Round one needs training rows for every product')
    return {'training': snapshot, 'gold': gold, 'sha256': tervor_registry.digest([snapshot, gold])}


def adapter_plan(product, prepared, *, version, day, rate, minutes):
    if product not in PRODUCTS or not version.startswith('tervor-lite-'):
        raise ValueError('Round one builds Lite for Zenas, Base and Tiger')
    if not rate.is_finite() or rate <= 0 or type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValueError('A positive quoted rate and bounded runtime are required')
    return dict(product=product, model_id=tervor_registry.identity(version, product, day),
                training_rows=prepared['training']['count'], data_sha256=prepared['training']['sha256'],
                gold_sha256=prepared['gold']['sha256'], bundle_sha256=prepared['sha256'],
                hardware='a10g-small', max_minutes=minutes, compute_ceiling_usd=str(rate * minutes),
                estimate_only=True, submitted=False)


def load_skill(path):
    spec = importlib.util.spec_from_file_location('tervor_' + path.parent.parent.name.replace('.', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def replay_ffl(path, directory):
    """All recorded licences pass Base code intake then Tiger's real checkpoint loop.

    A licence is not product-price evidence. Missing URLs stay explicit; this
    rehearsal neither invents websites/prices nor trains on synthetic prices.
    """
    from agents.base.engines.ffl.reference import MAX_BYTES, parse_file
    from agents.tiger import batch
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    source = parse_file(raw)
    source_hash = hashlib.sha256(raw).hexdigest()
    ingest = load_skill(ROOT / 'skills/ffl.ingest/1.0.1/run.py')
    normalize = load_skill(ROOT / 'skills/base.normalize/1.0.0/run.py')
    rows = []
    for record in source:
        mapped = await ingest.run(record['raw'], None)
        normalized = await normalize.run(mapped['record'], None)
        if not mapped['mapped'] or not normalized['fields']['name']['value']:
            raise ValueError('Base could not map a recorded licence')
        rows.append({'id': record['licence_key'], 'tenant_id': str(PLATFORM), 'state': record['state'],
                     'website': mapped['record']['website'], 'product_urls': []})
    import httpx
    def refuse(request):
        raise ValueError('Dry run has no recorded product response for this URL')
    identity = str(uuid5(PLATFORM, source_hash))
    checkpoint = Path(directory) / 'tiger-round-one.sqlite'
    async with httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(refuse)) as client:
        receipts = await batch.run(rows, str(PLATFORM), identity, checkpoint, client)
        resumed = await batch.run(rows, str(PLATFORM), identity, checkpoint, client)
    if resumed != receipts or len(receipts) != len(source):
        raise ValueError('Full-source handoff or checkpoint resume failed')
    states = Counter(row['state'] for row in rows)
    return dict(source_sha256=source_hash, source_records=len(source), base_records=len(rows),
                tiger_receipts=len(receipts), states=len(states),
                smallest_state=min(states, key=lambda state: (states[state], state)),
                source_status=dict(Counter(row['source_status'] for row in receipts)),
                resume_verified=True, model_cost_usd=0, external_requests=0)


async def dry_run(ffl, directory, datasets, *, version, day, rate, minutes):
    plans = []
    for product in PRODUCTS:
        path = Path(datasets) / (product + '.json')
        if path.is_symlink() or path.stat().st_size > 256 * 1024 * 1024:
            raise ValueError('Invalid training bundle file')
        data = json.loads(path.read_text())
        prepared = bundle(PLATFORM, product, data['training']['rows'], data['gold'])
        if data != prepared:
            raise ValueError('Training bundle integrity check failed')
        plans.append(adapter_plan(product, prepared, version=version, day=day, rate=rate, minutes=minutes))
    replay = await replay_ffl(Path(ffl), directory)
    return dict(status='dry_run', paid_jobs=0, ffl=replay, adapters=plans,
                compute_ceiling_usd=str(sum(Decimal(p['compute_ceiling_usd']) for p in plans)),
                founder_settings=['HF_TRAIN_BASE_MODEL', 'HF_TRAIN_BASE_REVISION', 'HF_TRAIN_MAX_USD'])


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', required=True)
    parser.add_argument('--ffl', type=Path, required=True)
    parser.add_argument('--datasets', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--day', type=date.fromisoformat, required=True)
    parser.add_argument('--rate-per-minute', type=Decimal, required=True)
    parser.add_argument('--minutes', type=int, required=True)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='tervor-round-one-') as directory:
        result = asyncio.run(dry_run(args.ffl, directory, args.datasets, version=args.version,
            day=args.day.isoformat(), rate=args.rate_per_minute, minutes=args.minutes))
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == '__main__':
    cli()
