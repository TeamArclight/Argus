from __future__ import annotations

import hashlib, math, re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional
from ..contracts import EvidenceChunk
from .chunking import structure_aware_chunk
from .reranker import Reranker, configured_reranker

def _terms(text: str) -> Counter[str]: return Counter(re.findall(r"[a-z0-9]+", text.lower()))
def _score(a: Counter[str], b: Counter[str]) -> float:
    dot = sum(a[x] * b[x] for x in a.keys() & b.keys())
    return dot / math.sqrt(sum(x*x for x in a.values()) * sum(x*x for x in b.values())) if a and b else 0.0

class InMemoryRAG:
    """Demo-safe lexical hybrid baseline; replace the scoring/index adapter with pgvector in API integration."""
    def __init__(self, reranker: Optional[Reranker] = None):
        self._chunks: list[EvidenceChunk] = []
        self.reranker = reranker or configured_reranker()
    def index(self, document_id: str, title: str, text: str, *, page: Optional[int] = None, **metadata: object) -> list[EvidenceChunk]:
        digest = hashlib.sha256(text.encode()).hexdigest()
        raw_chunks = structure_aware_chunk(text)
        new_chunks = []
        for n, chunk_data in enumerate(raw_chunks, 1):
            part = chunk_data["text"]
            clause = chunk_data.get("clause")
            location_metadata = {"title": title, **{key: value for key, value in metadata.items() if key not in {"version", "effective_from", "effective_to", "security_level", "source_uri"}}}
            if clause:
                location_metadata["clause"] = clause
            new_chunks.append(EvidenceChunk(id=f"{document_id}:{n}:{digest[:12]}", entity_type="document_chunk", entity_id=document_id, snippet=part.strip(), source_uri=metadata.get("source_uri"), page_number=page, content_hash=digest, location_metadata=location_metadata, version=metadata.get("version"), effective_from=metadata.get("effective_from"), effective_to=metadata.get("effective_to"), security_level=str(metadata.get("security_level", "INTERNAL"))))
        by_id = {chunk.id: chunk for chunk in self._chunks}
        by_id.update({chunk.id: chunk for chunk in new_chunks})
        self._chunks = list(by_id.values())
        return new_chunks
        
    def retrieve(self, query: str, filters: Optional[dict[str, object]] = None, top_k: int = 5) -> list[EvidenceChunk]:
        now = datetime.now(timezone.utc); filters = filters or {}; needle = _terms(query)
        candidates = [c for c in self._chunks if all(c.location_metadata.get(k) == v for k, v in filters.items())]
        candidates = [c for c in candidates if (not c.effective_from or c.effective_from <= now) and (not c.effective_to or c.effective_to >= now)]
        fetch_k = top_k * 3
        sorted_cands = sorted(candidates, key=lambda c: _score(needle, _terms(c.snippet)), reverse=True)[:fetch_k]
        if not sorted_cands:
            return []
        return self.reranker.rerank(query, sorted_cands, top_k)

    def delete(self, document_id: str, scope: Optional[dict[str, object]] = None) -> int:
        before = len(self._chunks)
        if not scope:
            self._chunks = [chunk for chunk in self._chunks if chunk.entity_id != document_id]
        else:
            self._chunks = [
                chunk for chunk in self._chunks
                if not (chunk.entity_id == document_id and all(chunk.location_metadata.get(k) == v for k, v in scope.items()))
            ]
        return before - len(self._chunks)
