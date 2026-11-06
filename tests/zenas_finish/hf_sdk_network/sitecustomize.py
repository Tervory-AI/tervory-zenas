"""Offline subprocess boundary; the actual installed HF SDK remains unchanged."""
import json
import os
from pathlib import Path
import socket

def no_network(*args, **kwargs):
    raise AssertionError('Live network is disabled in the HF Jobs SDK regression')

socket.socket.connect = no_network
socket.create_connection = no_network

import huggingface_hub
if hasattr(huggingface_hub, 'set_client_factory'):
    import httpx
    def respond(request):
        assert request.method == 'GET' and request.url.path == '/api/jobs/hardware'
        with Path(os.environ['HF_JOBS_TEST_EVENTS']).open('a') as events:
            events.write(json.dumps({'sdk':huggingface_hub.__version__,
                                     'method':request.method,'path':request.url.path})+'\n')
        return httpx.Response(200,json=[{'name':'a10g-small','prettyName':'A10G Small',
            'cpu':'8 vCPU','ram':'32 GB','accelerator':None,'unitCostMicroUSD':20000,
            'unitCostUSD':.02,'unitLabel':'minute'}])
    huggingface_hub.set_client_factory(lambda:httpx.Client(transport=httpx.MockTransport(respond)))
