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
        now = datetime.now(timezone.utc)
        filters = filters or {}
        needle = _terms(query)

        scoped_tender = filters.get("tender_id")
        scoped_tenant = filters.get("tenant_id")
        scoped_bidder = filters.get("bidder_id")

        candidates = []
        for c in self._chunks:
            # Temporal validity
            if c.effective_from and c.effective_from > now:
                continue
            if c.effective_to and c.effective_to < now:
                continue

            # Check shared policy status
            is_shared = (
                c.security_level == "PUBLIC"
                or c.location_metadata.get("is_shared") is True
                or (c.location_metadata.get("document_type") == "POLICY" and not c.location_metadata.get("tender_id"))
            )

            # Tenant isolation
            chunk_tenant = c.location_metadata.get("tenant_id")
            if scoped_tenant is not None and chunk_tenant is not None and chunk_tenant != scoped_tenant:
                if not is_shared:
                    continue

            # Tender isolation
            chunk_tender = c.location_metadata.get("tender_id")
            if scoped_tender is not None:
                if chunk_tender is not None and chunk_tender != scoped_tender:
                    continue
                if chunk_tender is None and not is_shared:
                    continue

            # Bidder isolation
            chunk_bidder = c.location_metadata.get("bidder_id")
            if scoped_bidder is not None and chunk_bidder is not None and chunk_bidder != scoped_bidder:
                continue

            # Other narrowing filters
            matches_all = True
            for k, v in filters.items():
                if k in {"tender_id", "tenant_id", "bidder_id"}:
                    continue
                if c.location_metadata.get(k) != v:
                    matches_all = False
                    break
            if not matches_all:
                continue

            candidates.append(c)

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
