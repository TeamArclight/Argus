"""Intelligence-owned pgvector index; never accesses API business tables."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from ..contracts import EvidenceChunk
from .embeddings import EmbeddingProvider, configured_embeddings
from .chunking import structure_aware_chunk
from .reranker import Reranker, configured_reranker


MIN_RELEVANCE_THRESHOLD = 0.20

def _jaccard(s1: set[str], s2: set[str]) -> float:
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def _build_fulltext_query(query: str) -> str:
    """Builds an OR-separated procurement tsquery string avoiding conversational filler terms."""
    STOP_WORDS = {
        "what", "is", "the", "are", "for", "to", "in", "of", "and", "or", "a", "an",
        "this", "that", "it", "at", "by", "from", "on", "as", "how", "does", "do",
        "can", "tell", "me", "about", "give", "show", "please", "applicable", "requirement",
        "requirements", "criteria", "tender", "bid", "bidders", "procurement", "clause",
        "section", "rule", "document", "documents", "qualification", "eligibility",
        "much", "rate", "fee"
    }
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOP_WORDS and len(w) > 1]
    
    terms = list(words)
    q_lower = query.lower()

    # Clause / section number detection (e.g. "clause 2.1", "2.1", "section 3.2")
    clause_nums = re.findall(r"\b([0-9]+(?:\.[0-9]+)+|[0-9]+)\b", q_lower)
    for c_num in clause_nums:
        if c_num not in terms:
            terms.append(f'"{c_num}"')
        terms.append(f'"clause {c_num}"')
        terms.append(f'"section {c_num}"')

    if "emd" in q_lower or "earnest" in q_lower:
        terms.extend(["emd", '"earnest money"', '"bid security"'])
    if "msme" in q_lower or "udyam" in q_lower or "mse" in q_lower:
        terms.extend(["msme", "mse", "udyam", '"micro and small"'])
    if "experience" in q_lower or "similar" in q_lower:
        terms.extend(["experience", '"similar work"', '"similar projects"'])
    if "turnover" in q_lower or "revenue" in q_lower:
        terms.extend(["turnover", "revenue"])
    if "jv" in q_lower or "joint venture" in q_lower:
        terms.extend(['"joint venture"', "jv", "consortium"])
    if "warranty" in q_lower:
        terms.extend(["warranty", "guarantee"])

    if not terms:
        return query
    return " OR ".join(terms)


class PgVectorRAG:
    MIN_RELEVANCE_THRESHOLD = MIN_RELEVANCE_THRESHOLD

    def __init__(self, database_url: str, embeddings: Optional[EmbeddingProvider] = None, reranker: Optional[Reranker] = None):
        self.database_url = database_url
        self.embeddings = embeddings or configured_embeddings()
        self.reranker = reranker or configured_reranker()

    def _connect(self):
        try: import psycopg
        except ImportError as exc: raise RuntimeError("pgvector RAG requires psycopg[binary]") from exc
        return psycopg.connect(self.database_url)

    def ensure_schema(self) -> None:
        dims = self.embeddings.dimensions
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("""CREATE TABLE IF NOT EXISTS intelligence_evidence_chunks (
                id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                snippet TEXT NOT NULL, source_uri TEXT, page_number INTEGER,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb, content_hash TEXT NOT NULL,
                version TEXT, effective_from TIMESTAMPTZ, effective_to TIMESTAMPTZ,
                security_level TEXT NOT NULL DEFAULT 'INTERNAL', created_at TIMESTAMPTZ NOT NULL,
                embedding vector(%d) NOT NULL, search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', snippet)) STORED
            )""" % dims)
            cur.execute("CREATE INDEX IF NOT EXISTS intelligence_chunks_embedding_idx ON intelligence_evidence_chunks USING hnsw (embedding vector_cosine_ops)")
            cur.execute("CREATE INDEX IF NOT EXISTS intelligence_chunks_fts_idx ON intelligence_evidence_chunks USING gin (search_vector)")

    def index(self, document_id: str, title: str, text: str, *, page: Optional[int] = None, **metadata: Any) -> list[EvidenceChunk]:
        digest = hashlib.sha256(text.encode()).hexdigest(); chunks: list[EvidenceChunk] = []
        
        raw_chunks = structure_aware_chunk(text)
        
        for number, chunk_data in enumerate(raw_chunks, 1):
            part = chunk_data["text"]
            clause = chunk_data.get("clause")
            
            location_metadata = {"title": title, **{key: value for key, value in metadata.items() if key not in {"version", "effective_from", "effective_to", "security_level", "source_uri"}}}
            if clause:
                location_metadata["clause"] = clause
            if "scope" not in location_metadata:
                if location_metadata.get("tender_id"):
                    location_metadata["scope"] = "TENDER"
                elif location_metadata.get("document_type") == "POLICY" or location_metadata.get("is_shared"):
                    location_metadata["scope"] = "GLOBAL_POLICY"
                
            chunk = EvidenceChunk(id="%s:%s:%s" % (document_id, number, digest[:12]), entity_type="document_chunk", entity_id=document_id, snippet=part.strip(), source_uri=metadata.get("source_uri"), page_number=page, content_hash=digest, location_metadata=location_metadata, version=metadata.get("version"), effective_from=metadata.get("effective_from"), effective_to=metadata.get("effective_to"), security_level=str(metadata.get("security_level", "INTERNAL")))
            chunks.append(chunk)
            
        embeddings_batch = self.embeddings.embed_batch([c.snippet for c in chunks]) if chunks else []
        
        with self._connect() as conn, conn.cursor() as cur:
            for chunk, emb in zip(chunks, embeddings_batch):
                cur.execute("""INSERT INTO intelligence_evidence_chunks
                (id,entity_type,entity_id,snippet,source_uri,page_number,metadata,content_hash,version,effective_from,effective_to,security_level,created_at,embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                ON CONFLICT (id) DO UPDATE SET snippet=EXCLUDED.snippet, metadata=EXCLUDED.metadata, embedding=EXCLUDED.embedding, effective_to=EXCLUDED.effective_to""",
                (chunk.id, chunk.entity_type, chunk.entity_id, chunk.snippet, chunk.source_uri, chunk.page_number, json.dumps(chunk.location_metadata), chunk.content_hash, chunk.version, chunk.effective_from, chunk.effective_to, chunk.security_level, chunk.created_at, _vector(emb)))
        return chunks

    def retrieve(self, query: str, filters: Optional[dict[str, Any]] = None, top_k: int = 5) -> list[EvidenceChunk]:
        filters = filters or {}
        clauses = [
            "(effective_from IS NULL OR effective_from <= NOW())",
            "(effective_to IS NULL OR effective_to >= NOW())"
        ]
        fulltext_q = _build_fulltext_query(query)
        params: list[Any] = [_vector(self.embeddings.embed(query)), fulltext_q]

        scoped_tender = filters.get("tender_id")
        scoped_tenant = filters.get("tenant_id")
        scoped_bidder = filters.get("bidder_id")

        if scoped_tenant is not None:
            clauses.append("((metadata ->> 'tenant_id' = %s) OR ((metadata ->> 'scope' = 'GLOBAL_POLICY' OR (metadata ->> 'document_type' = 'POLICY' AND metadata ->> 'scope' IS NULL)) AND security_level = 'PUBLIC' AND (metadata ->> 'tenant_id' IS NULL)))")
            params.append(str(scoped_tenant))

        if scoped_tender is not None:
            clauses.append("((metadata ->> 'tender_id' = %s) OR ((metadata ->> 'scope' = 'GLOBAL_POLICY' OR (metadata ->> 'document_type' = 'POLICY' AND metadata ->> 'scope' IS NULL)) AND security_level = 'PUBLIC' AND (metadata ->> 'tender_id' IS NULL)))")
            params.append(str(scoped_tender))

        if scoped_bidder is not None:
            clauses.append("(metadata ->> 'bidder_id' = %s OR metadata ->> 'bidder_id' IS NULL)")
            params.append(str(scoped_bidder))

        for key, value in filters.items():
            if key in {"tender_id", "tenant_id", "bidder_id"}:
                continue
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", str(key)):
                raise ValueError("invalid metadata filter key")
            clauses.append("metadata ->> %s = %s")
            params.extend([str(key), str(value)])

        where = " AND ".join(clauses)
        sql = """SELECT id,entity_type,entity_id,snippet,source_uri,page_number,metadata,content_hash,version,effective_from,effective_to,security_level,created_at,
        (0.7 * (1 - (embedding <=> %s::vector)) + 0.3 * ts_rank_cd(search_vector, websearch_to_tsquery('simple', %s))) AS score
        FROM intelligence_evidence_chunks WHERE """ + where + " ORDER BY score DESC LIMIT %s"
        
        fetch_k = top_k * 3
        params.append(fetch_k)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        clause_match = re.search(r"\b(?:clause|section|rule)?\s*([0-9]+(?:\.[0-9]+)+[a-z]?)\b", query.lower())
        clause_num = clause_match.group(1) if clause_match else None

        retrieved_chunks: list[EvidenceChunk] = []
        for row in rows:
            raw_score = float(row[13]) if len(row) > 13 and row[13] is not None else 0.0
            meta = dict(row[6] if isinstance(row[6], dict) else {})
            snippet = row[3] or ""

            if clause_num:
                meta_clause = str(meta.get("clause") or "").lower()
                if clause_num == meta_clause or f"clause {clause_num}" in snippet.lower() or f"section {clause_num}" in snippet.lower() or re.search(rf"\b{re.escape(clause_num)}\b", snippet):
                    raw_score = max(raw_score, 0.65) + 0.20

            if raw_score < MIN_RELEVANCE_THRESHOLD:
                continue
            # Monotonic bounded relevance score in [0.0, 1.0], preserving rank order
            bounded_score = round(max(0.0, min(1.0, raw_score)), 4)
            meta["raw_score"] = round(raw_score, 4)
            meta["bounded_relevance_score"] = bounded_score
            meta["relevance_score"] = bounded_score
            chunk = EvidenceChunk(
                id=row[0],
                entity_type=row[1],
                entity_id=row[2],
                snippet=row[3],
                source_uri=row[4],
                page_number=row[5],
                location_metadata=meta,
                content_hash=row[7],
                version=row[8],
                effective_from=row[9],
                effective_to=row[10],
                security_level=row[11],
                created_at=row[12],
            )
            retrieved_chunks.append(chunk)
        
        if not retrieved_chunks:
            return []
            
        reranked = self.reranker.rerank(query, retrieved_chunks, top_k * 2)

        # Exact deduplication by (entity_id, page_number, content_hash)
        seen_keys: set[tuple[str, Optional[int], str]] = set()
        deduped: list[EvidenceChunk] = []
        for chunk in reranked:
            key = (chunk.entity_id, chunk.page_number, chunk.content_hash)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(chunk)

        # Near-duplicate suppression (Jaccard similarity > 0.85)
        final_chunks: list[EvidenceChunk] = []
        for chunk in deduped:
            words = set(re.findall(r"[a-z0-9]+", chunk.snippet.lower()))
            is_near_dup = False
            for kept in final_chunks:
                kept_words = set(re.findall(r"[a-z0-9]+", kept.snippet.lower()))
                if _jaccard(words, kept_words) > 0.85:
                    is_near_dup = True
                    break
            if not is_near_dup:
                final_chunks.append(chunk)
            if len(final_chunks) >= top_k:
                break

        return final_chunks

    def delete(self, document_id: str, scope: Optional[dict[str, Any]] = None) -> int:
        clauses = ["entity_id = %s"]
        params: list[Any] = [document_id]
        if scope:
            for k, v in scope.items():
                if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", str(k)):
                    raise ValueError(f"invalid metadata scope key: {k}")
                clauses.append("metadata ->> %s = %s")
                params.extend([str(k), str(v)])
        sql = "DELETE FROM intelligence_evidence_chunks WHERE " + " AND ".join(clauses)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def _vector(values: list[float]) -> str: return "[" + ",".join(str(value) for value in values) + "]"
