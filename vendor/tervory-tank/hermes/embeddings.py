"""Local embeddings. The model runs in this container. No provider key.

BAAI/bge-small-en-v1.5 is the default (384 dimensions, CPU). Client text is
not sent to an embedding API.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Protocol


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


MODEL = json.loads(Path(__file__).with_name("model.json").read_text())
MODEL_ID = MODEL["name"] + "@" + MODEL["revision"]


class FastEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        if model_name != MODEL["name"]:
            raise ValueError("embedding model does not match the image pin")
        self.model_name = model_name
        self.name = MODEL_ID
        self.dim = MODEL["dimensions"]
        self._lock = Lock()
        self._model: object | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        from fastembed import TextEmbedding

        model = TextEmbedding(
            model_name=self.model_name,
            specific_model_path=MODEL["directory"],
            local_files_only=True,
            providers=["CPUExecutionProvider"],
            threads=1,
        )
        self._model = model

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            self.load()
            if self._model is None:
                raise RuntimeError("embedding model is not loaded")
            return [[float(item) for item in vector] for vector in self._model.embed(texts, batch_size=16)]

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]
