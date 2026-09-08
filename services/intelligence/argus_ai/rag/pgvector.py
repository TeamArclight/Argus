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


class PgVectorRAG:
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
        params: list[Any] = [_vector(self.embeddings.embed(query)), query]

        scoped_tender = filters.get("tender_id")
        scoped_tenant = filters.get("tenant_id")
        scoped_bidder = filters.get("bidder_id")

        if scoped_tenant is not None:
            clauses.append("(metadata ->> 'tenant_id' = %s OR security_level = 'PUBLIC')")
            params.append(str(scoped_tenant))

        if scoped_tender is not None:
            clauses.append("(metadata ->> 'tender_id' = %s OR security_level = 'PUBLIC' OR (metadata ->> 'is_shared') = 'true' OR (metadata ->> 'document_type' = 'POLICY' AND metadata ->> 'tender_id' IS NULL))")
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
        (0.7 * (1 - (embedding <=> %s::vector)) + 0.3 * ts_rank_cd(search_vector, plainto_tsquery('simple', %s))) AS score
        FROM intelligence_evidence_chunks WHERE """ + where + " ORDER BY score DESC LIMIT %s"
        
        fetch_k = top_k * 3
        params.append(fetch_k)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            
        retrieved_chunks = [EvidenceChunk(id=row[0], entity_type=row[1], entity_id=row[2], snippet=row[3], source_uri=row[4], page_number=row[5], location_metadata=row[6], content_hash=row[7], version=row[8], effective_from=row[9], effective_to=row[10], security_level=row[11], created_at=row[12]) for row in rows]
        
        if not retrieved_chunks:
            return []
            
        return self.reranker.rerank(query, retrieved_chunks, top_k)

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
