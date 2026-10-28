"""Real HTTP routes and Qdrant's local store; only the encoder is stubbed."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from qdrant_client import QdrantClient

from hermes.embeddings import FastEmbedder, MODEL, MODEL_ID
from hermes.law_store import LawIndex
from hermes.qdrant_store import QdrantStore
from hermes.service import create_app

ROOT = Path(__file__).resolve().parents[2]


class Encoder:
    def __init__(self):
        self.calls = []

    def embed_many(self, texts):
        self.calls.extend(texts)
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def stack(region):
    qdrant = QdrantClient(":memory:")
    store = QdrantStore(qdrant, 4)
    store.law = LawIndex(qdrant, 4, region=region)
    encoder = Encoder()
    return qdrant, encoder, create_app(store, encoder)


def point():
    return {"chunk_id": "companies-act:66:version:1", "version_id": "companies-act:66:version",
            "url": "https://api.laws.africa/v3/akn/za/act/2008/71/eng.html#sec_66",
            "version_date": "2024-01-01", "attribution": "Laws.Africa", "licence": "Laws.Africa",
            "vector": [1.0, 0.0, 0.0, 0.0], "fingerprint": "0" * 64}


@pytest.mark.asyncio
async def test_region_auth_and_source_boundary_before_embedding_or_writing(monkeypatch):
    monkeypatch.setenv("TANK_KEY_ZENAS", "TANK_KEY_ZENAS")
    monkeypatch.setenv("TANK_KEY_RIG", "TANK_KEY_RIG")
    monkeypatch.setenv("TANK_API_KEY", "TANK_API_KEY")
    qdrant, encoder, app = stack("za")
    request = {"jurisdiction": "za", "model": MODEL_ID}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hermes:8000") as client:
        for key in ("TANK_KEY_RIG", "TANK_API_KEY", ""):
            denied = await client.post("/law/embeddings", headers={"X-Tank-Key": key}, json={**request, "texts": ["civil law"]})
            assert denied.status_code in (401, 403)
        headers = {"X-Tank-Key": "TANK_KEY_ZENAS"}
        for route, values in (("embeddings", {"texts": ["civil law"]}), ("points", {"points": [point()]}),
                              ("search", {"vector": point()["vector"]})):
            denied = await client.post("/law/" + route, headers=headers, json={**request, **values, "jurisdiction": "us"})
            assert denied.status_code == 409
        assert encoder.calls == []
        assert qdrant.get_collections().collections == []
        missing = await client.post("/law/points", headers=headers, json={**request, "points": [{**point(), "url": ""}]})
        assert missing.status_code == 422
        encoded = await client.post("/law/embeddings", headers=headers, json={**request, "texts": ["civil law"]})
        assert encoded.json() == {"model": MODEL_ID, "vectors": [point()["vector"]]}
        written = await client.post("/law/points", headers=headers, json={**request, "points": [point()]})
        assert written.status_code == 200
        await client.post("/law/points", headers=headers, json={**request, "points": [point()]})
        found = await client.post("/law/search", headers=headers, json={**request, "vector": point()["vector"]})
        assert found.json() == {"chunk_ids": [point()["chunk_id"]]}
        assert qdrant.count("law_za").count == 1
        assert not qdrant.collection_exists("law_us")
        payload = qdrant.scroll("law_za")[0][0].payload
        assert payload["url"] == point()["url"] and payload["version_date"] == "2024-01-01"
        assert payload["attribution"] == "Laws.Africa"
    qdrant.close()


@pytest.mark.parametrize("region,jurisdiction", [("za", "za"), ("us-east", "us")])
def test_collection_is_chosen_only_from_the_box_region(region, jurisdiction):
    qdrant, _, app = stack(region)
    index = LawIndex(qdrant, 4, region=region)
    assert index.collection == "law_" + jurisdiction
    other = "za" if jurisdiction == "us" else "us"
    with pytest.raises(ValueError):
        index.upsert(other, [point()], MODEL_ID)
    with pytest.raises(ValueError):
        index.query(other, point()["vector"], MODEL_ID, limit=1)
    with pytest.raises(ValueError):
        index.upsert(jurisdiction, [point()], "unreviewed revision")
    assert qdrant.get_collections().collections == []
    qdrant.close()


def test_pinned_encoder_uses_cpu_local_files_and_never_probes_or_downloads(monkeypatch):
    options = []
    class StubModel:
        def __init__(self, **kwargs):
            options.append(kwargs)
        def embed(self, texts, batch_size):
            assert batch_size == 16
            return [[1.0] * 384 for _ in texts]
    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=StubModel))
    encoder = FastEmbedder()
    encoder.load()
    assert encoder.embed_many(["civil law", "contract"]) == [[1.0] * 384] * 2
    assert options == [{"model_name": MODEL["name"], "specific_model_path": MODEL["directory"],
                        "local_files_only": True, "providers": ["CPUExecutionProvider"], "threads": 1}]
    assert encoder.name == MODEL_ID
    with pytest.raises(ValueError):
        FastEmbedder("unpinned")


def test_model_download_is_in_dependency_layer_before_application_code_copy():
    dockerfile = (ROOT / "hermes/Dockerfile").read_text()
    manifest = json.loads((ROOT / "hermes/model.json").read_text())
    assert manifest["name"] == "BAAI/bge-small-en-v1.5"
    assert manifest["revision"] == "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
    assert "revision=m['revision']" in dockerfile
    assert dockerfile.index("COPY hermes/requirements.txt hermes/model.json") < dockerfile.index("snapshot_download") < dockerfile.index("COPY hermes/*.py")
    assert dockerfile.index("python /tmp/memory_encoder.py") < dockerfile.index("COPY hermes/*.py")
    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "TextEmbedding(" not in dockerfile  # Build downloads files; does not run inference.


def test_production_qdrant_connection_disables_proxy_and_redirects(monkeypatch):
    import qdrant_client
    from hermes import embeddings, runtime
    built = []
    class StubEncoder:
        dim = 384
        def __init__(self, *args):
            pass
        def load(self):
            pass
    class Client:
        def __init__(self, **kwargs):
            built.append(kwargs)
        def get_collections(self):
            return []
    monkeypatch.setattr(embeddings, "FastEmbedder", StubEncoder)
    monkeypatch.setattr(qdrant_client, "QdrantClient", Client)
    monkeypatch.setenv("TANK_REGION", "za")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "QDRANT_API_KEY")
    store, _ = runtime.connect_production()
    assert store.law.collection == "law_za"
    assert built[0]["url"] == "http://qdrant:6333"
    assert built[0]["trust_env"] is False and built[0]["follow_redirects"] is False
    monkeypatch.setenv("QDRANT_URL", "https://remote.tervory.com")
    with pytest.raises(RuntimeError, match="Qdrant must be on this box"):
        runtime.connect_production()
    assert len(built) == 1
