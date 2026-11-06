"""Exercise the actual installed SDK and launcher with HTTP fully faked."""
import asyncio
import importlib.util
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import huggingface_hub
from huggingface_hub import set_client_factory

assert huggingface_hub.__version__ == '1.7.2'
calls = []

def respond(request):
    calls.append((request.method, request.url.path))
    assert request.method == 'GET' and request.url.path == '/api/jobs/hardware'
    return httpx.Response(200, json=[{'name':'a10g-small','prettyName':'A10G Small',
        'cpu':'8 vCPU','ram':'32 GB','accelerator':None,'unitCostMicroUSD':20000,
        'unitCostUSD':.02,'unitLabel':'minute'}])

set_client_factory(lambda: httpx.Client(transport=httpx.MockTransport(respond)))
root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('actual_training_launcher',root/'tank/training_jobs/run.py')
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)
os.environ.update(TANK_REGION='za',HF_ORG='placeholder-org',HF_TOKEN='placeholder',HF_TRAIN_MAX_USD='50')
result = asyncio.run(gpu.run(SimpleNamespace(agent='zenas',receipt=None,job_id='',minutes=120,
                                           submit=False,day='2026-11-06')))
assert result['status']=='ready' and result['hardware']=='a10g-small'
assert result['cost_usd']==Decimal('2.4')
assert calls == [('GET','/api/jobs/hardware')]
print('Jobs SDK hardware discovery and launcher dry run passed')
