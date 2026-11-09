"""C4: reusable vectors, keyed only by pinned model and exact text digest."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import math
import os
import sys
from threading import Lock
import time
import uuid

from qdrant_client import QdrantClient, models

TTL = 7 * 86400
_measure = ContextVar('embedding_cache_measurement', default=None)
_cache = None
_init_lock = Lock()


def enabled():
    return os.environ.get('ENGINE_C4_ENABLED') == '1'


class Measurement:
    def __init__(self):
        self.lock = Lock()
        self.values = dict(attempts=0, hits=0, saved_usd=0., saved_compute_ms=0.)

    def add(self, values):
        with self.lock:
            for key in self.values:
                value = values.get(key, 0)
                if type(value) in {int, float} and math.isfinite(value) and value >= 0:
                    self.values[key] += value


@contextmanager
def measure():
    counter = Measurement()
    token = _measure.set(counter)
    try:
        yield counter
    finally:
        _measure.reset(token)


def merge(values):
    counter = _measure.get()
    if counter is not None and isinstance(values, dict):
        counter.add(values)


class VectorCache:
    def __init__(self, client=None):
        self.client = client
        self.local = OrderedDict()
        self.lock = Lock()
        self.stripes = [Lock() for _ in range(64)]
        self.collections = set()
        self.swept = {}

    def store(self):
        if self.client is None:
            # DECISION reuse regional Qdrant; no new service, dependency or raw text.
            self.client = QdrantClient(url=os.environ.get('QDRANT_URL', 'http://qdrant:6333'),
                api_key=os.environ.get('QDRANT_API_KEY') or None, timeout=1,
                check_compatibility=False, trust_env=False, follow_redirects=False)
        return self.client

    def collection(self, model, dimensions):
        name = 'tank_c4_' + hashlib.sha256(f'{model}:{dimensions}'.encode()).hexdigest()
        with self.lock:
            if name not in self.collections:
                client = self.store()
                if not client.collection_exists(name):
                    try:
                        # DOT preserves the original vector; COSINE would normalize it.
                        client.create_collection(name, vectors_config=models.VectorParams(
                            size=dimensions, distance=models.Distance.DOT))
                    except Exception:
                        if not client.collection_exists(name):
                            raise
                self.collections.add(name)
        return name

    @staticmethod
    def valid(entry, key, dimensions):
        if not entry or entry.get('key') != key:
            return False
        if any(type(entry.get(k)) not in {int, float} or not math.isfinite(entry[k]) or entry[k] < 0
               for k in ('expires', 'compute_ms')) or entry['expires'] <= time.time():
            return False
        vector = entry.get('vector')
        return (isinstance(vector, list) and len(vector) == dimensions
                and all(isinstance(v, (float, int)) and math.isfinite(v) for v in vector))

    def embed(self, model, text, dimensions, compute):
        if not enabled():
            return compute()
        key = hashlib.sha256((model + '\0' + text).encode()).hexdigest()
        with self.stripes[int(key[:8], 16) % len(self.stripes)]:
            with self.lock:
                entry = self.local.get(key)
            name, point = None, str(uuid.UUID(key[:32]))
            if not self.valid(entry, key, dimensions):
                try:
                    name = self.collection(model, dimensions)
                    rows = self.store().retrieve(name, [point], with_vectors=True)
                    entry = {**(rows[0].payload or {}), 'vector': rows[0].vector} if rows else None
                except Exception:
                    entry = None  # Cache outages never turn a valid embedding into an error.
            hit = self.valid(entry, key, dimensions)
            if not hit:
                started = time.perf_counter()
                vector = list(compute())
                entry = {'key': key, 'vector': vector, 'expires': time.time() + TTL,
                         'compute_ms': (time.perf_counter() - started) * 1000}
                if not self.valid(entry, key, dimensions):
                    raise ValueError('Invalid embedding vector')
                if name is not None:
                    try:
                        self.store().upsert(name, points=[models.PointStruct(id=point, vector=vector,
                            payload={k: v for k, v in entry.items() if k != 'vector'})], wait=True)
                        with self.lock:
                            sweep = time.time() - self.swept.get(name, 0) > 3600
                            if sweep:
                                self.swept[name] = time.time()
                        if sweep:
                            self.store().delete(name, points_selector=models.FilterSelector(filter=models.Filter(
                                must=[models.FieldCondition(key='expires', range=models.Range(lt=time.time()))])), wait=True)
                    except Exception:
                        pass
            with self.lock:
                self.local[key] = entry
                self.local.move_to_end(key)
                while len(self.local) > 8192:
                    self.local.popitem(last=False)
            merge(dict(attempts=1, hits=int(hit), saved_compute_ms=entry['compute_ms'] if hit else 0.))
            return list(entry['vector'])


def embed(model, text, dimensions, compute):
    if not enabled():
        return compute()
    global _cache
    with _init_lock:
        if _cache is None:
            _cache = VectorCache()
    return _cache.embed(model, text, dimensions, compute)


def measured_route(function):
    @wraps(function)
    async def wrapped(req, *args, **kwargs):
        if not enabled():
            return await function(req, *args, **kwargs)
        with measure() as counter:
            answer = await function(req, *args, **kwargs)
        rt = sys.modules[function.__module__]
        if isinstance(answer, rt.RouteResponse):
            import cache_metrics
            answer = await cache_metrics.record(rt, req, answer, 'C4', counter.values)
        return answer
    return wrapped
