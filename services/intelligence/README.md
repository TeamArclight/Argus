# ARGUS Intelligence Service

This standalone Python package provides AI and document-intelligence capabilities for ARGUS.

Public Python entry points include:

- `extract_tender(file_path)`
- `classify_document(file_path)`
- `extract_bidder_document(file_path)`
- `retrieve_policy(query, rag=...)`

The service includes deterministic extraction for tender requirements and bidder facts,
optional Gemini or OpenAI-compatible structured extraction, document classification,
risk detection, text/PDF/DOCX/XLSX/image parsing, in-memory lexical retrieval,
PostgreSQL/pgvector hybrid retrieval, page-level ingestion, and a LangGraph workflow
boundary with injectable checkpoint and human-review resume support.

## HTTP service

Run locally from this directory:

```bash
pip install -r requirements.txt
uvicorn argus_ai.http_service:app --host 0.0.0.0 --port 8100
```

Available routes are `/health`, `/extract-tender`, `/extract-document`,
`/classify-document`, `/detect-risk`, `/rag-query`, `/rag-ingest`, `/rag-delete`,
`/evaluate-bid`, and `/evaluate-bid/resume`. Set
`ARGUS_INTELLIGENCE_API_KEY` to require a bearer token. Set
`ARGUS_RAG_DATABASE_URL` to use PostgreSQL/pgvector; otherwise the service uses an
in-memory index. For model-backed extraction, set `ARGUS_MODEL_PROVIDER=gemini` and
provide `ARGUS_GEMINI_API_KEY`.

The API service calls these routes through the URLs configured in `services/api`.
The API worker supports database-backed queued tender extraction and bidder
verification when `ARGUS_JOB_WORKER_ENABLED=true`. Durable LangGraph checkpoint
storage, semantic embedding deployment, and CI wiring for the evaluation release
gates still require platform-specific integration.
