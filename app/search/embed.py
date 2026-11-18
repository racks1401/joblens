"""Embedding backends: fastembed (ONNX, CPU) for MiniLM and bge-small, plus a hashing fake for tests."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from app.config import get_settings

DIM = 384


class Embedder(Protocol):
    name: str

    def embed_docs(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """fastembed TextEmbedding wrapper. Models are downloaded once into FASTEMBED_CACHE."""

    def __init__(self, key: str, model_name: str, query_prefix: str = "", cache_dir: str | None = None,
                 batch_size: int = 64) -> None:
        from fastembed import TextEmbedding

        self.name, self.query_prefix, self.batch_size = key, query_prefix, batch_size
        self.model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed_docs(self, texts: Sequence[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.embed(list(texts), batch_size=self.batch_size)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.embed([self.query_prefix + text]))).tolist()


class FakeEmbedder:
    """Deterministic hashed bag-of-words vectors (L2-normalized). Lexical, but enough for tests."""

    def __init__(self, key: str = "fake") -> None:
        self.name = key

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for tok in re.findall(r"[a-z0-9+#.]+", text.lower()):
            h = int(hashlib.md5((self.name + tok).encode()).hexdigest(), 16)
            v[h % DIM] += 1.0 if (h >> 9) % 2 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_docs(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def model_keys() -> list[str]:
    return list(get_settings().cfg.embeddings.models)


@lru_cache
def get_embedder(key: str | None = None) -> Embedder:
    """Embedder for a model key from settings (minilm | bge-small); EMBEDDER=fake gives the fake."""
    s = get_settings()
    key = key or s.cfg.embeddings.default_model
    if key not in s.cfg.embeddings.models:
        raise KeyError(f"unknown embedding model {key!r}")
    if s.env.embedder == "fake":
        return FakeEmbedder(key)
    m = s.cfg.embeddings.models[key]
    return FastEmbedder(key, m.name, m.query_prefix, s.env.fastembed_cache, s.cfg.embeddings.batch_size)
