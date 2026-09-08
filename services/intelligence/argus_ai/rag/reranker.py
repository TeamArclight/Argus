from __future__ import annotations

import os
import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from argus_ai.contracts import EvidenceChunk


class Reranker(Protocol):
    def rerank(self, query: str, chunks: list[EvidenceChunk], top_k: int) -> list[EvidenceChunk]:
        ...


class KeywordOverlapReranker:
    def rerank(self, query: str, chunks: list[EvidenceChunk], top_k: int) -> list[EvidenceChunk]:
        query_terms = set(re.findall(r"\w+", query.lower()))
        query_phrase = query.lower()
        
        def score(chunk: EvidenceChunk) -> float:
            text = chunk.snippet.lower()
            chunk_terms = set(re.findall(r"\w+", text))
            overlap = len(query_terms & chunk_terms)
            phrase_bonus = 5.0 if query_phrase in text else 0.0
            return overlap + phrase_bonus
            
        ranked = sorted(chunks, key=score, reverse=True)
        return ranked[:top_k]


class GeminiReranker:
    def rerank(self, query: str, chunks: list[EvidenceChunk], top_k: int) -> list[EvidenceChunk]:
        if not chunks:
            return []
            
        from argus_ai.model_gateway.gateway import configured_gateway
        gateway = configured_gateway()
        
        class ChunkScore(BaseModel):
            model_config = ConfigDict(extra="forbid")
            chunk_id: str
            score: float = Field(ge=0.0, le=1.0)
            
        class RerankResult(BaseModel):
            model_config = ConfigDict(extra="forbid")
            scores: list[ChunkScore]
            
        instruction = f"Rate the relevance of these chunks to the query from 0.0 to 1.0. Query: {query}"
        
        untrusted = ""
        for chunk in chunks:
            untrusted += f"Chunk ID: {chunk.id}\nText: {chunk.snippet}\n\n"
            
        result = gateway.extract_structured(instruction, untrusted, RerankResult)
        score_map = {s.chunk_id: s.score for s in result.scores}
        def score_chunk(c: EvidenceChunk) -> float:
            return score_map.get(c.id, 0.0)
            
        ranked = sorted(chunks, key=score_chunk, reverse=True)
        return ranked[:top_k]


def configured_reranker() -> Reranker:
    provider = os.getenv("ARGUS_RERANKER", "keyword").lower()
    if provider == "gemini":
        return GeminiReranker()
    return KeywordOverlapReranker()
