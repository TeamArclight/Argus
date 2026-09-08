"""Replaceable embedding providers. Hash embeddings are demo-only, never production retrieval."""
from __future__ import annotations

import hashlib
import os
import re
from typing import Protocol


class EmbeddingProvider(Protocol):
    dimensions: int
    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedding:
    """Deterministic local fallback, useful for tests only; it has no semantic guarantees."""
    def __init__(self, dimensions: int = 768): self.dimensions = dimensions
    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            slot = int(hashlib.sha256(token.encode()).hexdigest(), 16) % self.dimensions
            vector[slot] += 1.0
        magnitude = sum(value * value for value in vector) ** .5
        return [value / magnitude for value in vector] if magnitude else vector
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class GeminiEmbedding:
    def __init__(self, api_key: str | None = None, dimensions: int = 768):
        key = api_key or os.getenv("ARGUS_GEMINI_API_KEY")
        if not key: raise RuntimeError("ARGUS_GEMINI_API_KEY is not configured")
        try: from google import genai
        except ImportError as exc: raise RuntimeError("Gemini embeddings require google-genai") from exc
        self.client, self.dimensions = genai.Client(api_key=key), dimensions
        self.model = os.getenv("ARGUS_EMBEDDING_MODEL", "gemini-embedding-001")
    def embed(self, text: str) -> list[float]:
        result = self.client.models.embed_content(model=self.model, contents=text, config={"output_dimensionality": self.dimensions})
        return list(result.embeddings[0].values)
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts: return []
        result = self.client.models.embed_content(model=self.model, contents=texts, config={"output_dimensionality": self.dimensions})
        return [list(emb.values) for emb in result.embeddings]


def configured_embeddings() -> EmbeddingProvider:
    dimensions = int(os.getenv("ARGUS_EMBEDDING_DIMENSIONS", "768"))
    provider = os.getenv("ARGUS_EMBEDDING_PROVIDER", "hash").lower()
    live_required = os.getenv("ARGUS_REQUIRE_LIVE_EMBEDDINGS", "false").lower() in {"true", "1", "yes"} or os.getenv("APP_ENV") == "production"
    if provider == "gemini":
        return GeminiEmbedding(dimensions=dimensions)
    elif live_required:
        raise RuntimeError("Live embeddings are required but ARGUS_EMBEDDING_PROVIDER is not configured for a live provider.")
    return HashEmbedding(dimensions)
