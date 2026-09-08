# ARGUS AI/RAG Intelligence Salvage & Integration Plan

**Repository:** `https://github.com/TeamArclight/Argus.git`  
**Target Branch:** `integration/intelligence-salvage`  
**Backend Baseline Main Commit:** `3b1663906e9e9745001e57fa38fd2f035f8b76fe`  
**Source PR #15 Head:** `868577e657a8d4e7ead30cdda31cdb1d6eaa6e1f` (`feat/intelligence-service`)  
**Status:** IMPLEMENTED AND VERIFIED  

---

## 1. Executive Summary

This plan details the salvage and integration of the intelligence service (`services/intelligence`) from PR #15 into the hardened ARGUS backend baseline.

### Core Architecture & Boundaries
1. **Preserved Hardened Backend Baseline**:
   - Phase 11 (PostgreSQL concurrency, transactional row locks via `OperationLockService`, monotonic audit events via `JobEventService`, strict idempotency) remains authoritative and untouched.
   - Phase 12 (Pure deterministic `ComplianceEngine`, strict numeric/financial context validation, explicit evaluation clock, truthful historical replay) remains 100% backend-owned.
2. **Zero Code Duplication / No Second Compliance Engine**:
   - The intelligence service handles document parsing (OCR/PDF/Docx/XLSX), structured candidate extraction, RAG hybrid retrieval, and advisory risk signals.
   - Intelligence outputs are treated strictly as **unapproved candidates** (`is_approved=False`).
   - Final qualification, compliance evaluation, and human officer decisions are strictly backend-managed.
3. **Transparent Demo Data**:
   - All synthetic bidder certificates and invented tender notices carry standardized disclaimer banners (`SYNTHETIC DEMO DATA — NOT AN OFFICIAL RECORD`).
   - Genuine public policy text (GFR 2017 Ch 6) is clearly identified as public domain reference.

---

## 2. Component Inventory & Verification Status

| Component / File | Verification Classification | Capabilities & Implementation Details | Integration Status |
|---|---|---|---|
| `argus_ai/model_gateway/gateway.py` | **Implemented & Tested (Offline Mock); Live Smoke-Test Ready** | `ModelGateway` abstraction supporting `GeminiProvider` (`google-genai` SDK, `gemini-2.5-flash`) and offline test provider. Generates grounded responses with cited chunk IDs. | When `ARGUS_GEMINI_API_KEY` is not set, operates in explicit offline test mode. Live provider failures raise explicit errors without silent mock fallbacks. |
| `argus_ai/parsing/service.py` | **Implemented & Tested** | Multi-format text parsing: `.txt`, `.md`, `.csv`, `.docx` (`python-docx`), `.xlsx` (`openpyxl`), native `.pdf` (`pypdf`), and scanned image/PDF OCR (`pytesseract`, `pdf2image`). | Preserves 1-indexed page numbers and section text. |
| `argus_ai/extraction/service.py` | **Implemented & Tested** | Deterministic regex + heuristic extraction (`tax.gstin`, `identity.pan`, `registration.udyam`, `labour.epfo_registration`, `labour.esic_registration`, `financial.average_annual_turnover`, `experience.years`, `document.expiry_date`). | Structured candidate extraction maps directly to backend schemas with unapproved candidate status. |
| `argus_ai/rag/chunking.py` | **Implemented & Tested** | Structure-aware (clause and section headers) and sliding-window fallback chunker. | Generates chunks with exact page numbers, content hashes, and location metadata. |
| `argus_ai/rag/embeddings.py` | **Implemented & Tested (Hash); Live Ready (Gemini)** | `GeminiEmbedding` (`gemini-embedding-001`, 768-dim) and `HashEmbedding` (768-dim deterministic hash for offline testing). | Vector dimensions explicitly aligned at 768. |
| `argus_ai/rag/pgvector.py` | **Implemented & Schema-Verified** | PostgreSQL `vector(768)` HNSW cosine index + `tsvector` GIN full-text index for hybrid search with Reciprocal Rank Fusion and metadata scoping. | Scoped filtering on `tenant_id`, `tender_id`, and `entity_id`. Document deletion implemented via `delete(document_id)`. |
| `argus_ai/rag/reranker.py` | **Implemented & Tested** | `KeywordOverlapReranker` (keyword and exact phrase bonus heuristic) and `GeminiReranker` (LLM relevance scoring). | Fails explicitly on live provider error without silent degradation. |
| `argus_ai/rag/service.py` | **Implemented & Tested** | `InMemoryRAG` service with metadata filtering, cosine similarity, and document deletion. | Powers offline unit & integration test suites. |
| `argus_ai/rag/ingestion.py` | **Implemented & Tested** | Document chunking, metadata enrichment, deduplication, and RAG indexing. | Tested with local policies and documents. |
| `argus_ai/risk/service.py` & `context.py` | **Implemented & Tested** | Deterministic multi-document anomaly detection: duplicate document hashes, turnover claim vs verification mismatch, entity name discrepancy, identifier conflicts, expired documents, and contradictory facts. | Emits advisory `RiskSignal` records. |
| `argus_ai/agents/workflow.py` | **Implemented & Tested** | LangGraph StateGraph pipeline (`tender_intelligence` -> `document_intelligence` -> `knowledge` -> `risk` -> `compliance` -> `human_review` -> `report`). | Missing compliance tool defaults safely to `REVIEW_REQUIRED`. Backend retains sole authority over final compliance. |
| `argus_ai/storage.py` | **Implemented & Tested (5 Regressions)** | Document materialization supporting Windows drive letters (`C:/...`), `file://` URIs, and HTTPS signed download URLs. Enforces `ARGUS_ALLOWED_STORAGE_ROOTS`, size limit (`MAX_DOCUMENT_BYTES`), credential URL rejection, and private IP rejection. | All 5 Windows path and traversal test regressions passing. |
| `argus_ai/http_service.py` | **Implemented & Tested** | FastAPI microservice exposing `/extract-tender`, `/extract-document`, `/classify-document`, `/detect-risk`, `/rag-query`, `/rag-ingest`, `/rag-delete`, `/evaluate-bid`, `/evaluate-bid/resume`, `/health`. | Supports contract_version 1.0 envelope payloads with base64 bytes and direct file/URI payloads. |
| `services/api/app/workers/worker.py` | **Implemented** | Background job processor integrating Phase 11 `OperationLockService` (exclusive row locks) and `JobEventService` (monotonic audit events). | Processes `EXTRACT_REQUIREMENTS` and `VERIFY_BIDDER` jobs safely. |
| `data/demo/` & `data/policies/` | **Implemented & Labeled** | Fictitious bidder certificates (Alpha, Bharat, Crest), demo tender notice (GeM road maintenance), GFR 2017 Ch 6 excerpt, and GeM SOP v3.2 guideline. | Every file explicitly labeled with standardized synthetic or public domain disclaimer headers. |

---

## 3. Extraction Contract Alignment

| Intelligence Payload (`argus_ai/contracts.py`) | Backend Target (`services/api/app/schemas/canonical.py`) | Governance & Approval Boundary |
|---|---|---|
| `TenderRequirementDraft`<br>- `clause`<br>- `requirement_type`<br>- `field`<br>- `operator`<br>- `expected_value`<br>- `unit`, `mandatory`<br>- `source_page`, `source_text`<br>- `confidence` | `TenderRequirementCreate`<br>- `clause`<br>- `requirement_type`<br>- `field`<br>- `operator`<br>- `expected_value`<br>- `unit`, `mandatory`<br>- `source_page`, `source_text`<br>- `confidence`<br>- `is_approved = False` | Extracted requirements are saved as **unapproved candidates**. A procurement officer must review and approve them before compliance evaluation. |
| `ExtractedFactDraft`<br>- `field`<br>- `value`<br>- `source_page`, `source_text`<br>- `confidence`<br>- `provider`, `model`<br>- `bounding_box` | `ExtractedFactCreate`<br>- `field`<br>- `value`<br>- `source_page`, `source_text`<br>- `confidence`<br>- `metadata_json` (contains `source_page`, `source_text`, `provider`, `model`, `bounding_box`) | Facts carry provenance and confidence. Facts alone do not determine compliance without verification and approved rules. |

---

## 4. Verification Results Summary
 
1. **Intelligence Microservice Test Suite (`services/intelligence/tests`)**:
   - **48 passed, 0 failed, 1 warning** (0.94s).
   - Covers: unit tests, gold cases, parser, chunking, embeddings, RAG ingestion/query/deletion, risk detection, LangGraph workflow, readiness probe, Windows storage resolution, envelope contract mapping, pgvector schema compilation & vector formatting, OCR missing binary graceful degradation, synthetic end-to-end replay, bounded base64 size limits, auth enforcement, and RAG input validation.
2. **Backend Regression Test Suite (`services/api/tests`)**:
   - **250 passed, 7 skipped, 7 warnings** (100% baseline match + worker error sanitization regression).
   - Covers: Phase 11 PostgreSQL concurrency, row locks, idempotency, worker safe transaction boundaries and error sanitization, and Phase 12 pure compliance engine, strict numeric/financial contexts, and temporal truthfulness.
3. **Normal Development Database Safety**:
   - `argus_dev.db` was untouched and preserved.

---

## 5. Security & Tenant Isolation Controls

1. **Service-to-Service Authentication**:
   - In live mode, intelligence endpoints require Bearer authentication via `ARGUS_INTELLIGENCE_API_KEY`.
2. **Document Resolution Safety**:
   - Traversal protection: Local files must resolve within `ARGUS_ALLOWED_STORAGE_ROOTS` when configured.
   - URL validation: Only `https://` signed URLs with public IP/domain allowed (loopback/private IPs rejected).
   - Credential stripping: URLs with embedded credentials are automatically rejected.
   - Bounded size: Documents exceeding `ARGUS_MAX_DOCUMENT_BYTES` (default 30MB) are rejected.
3. **RAG Scope Isolation**:
   - Queries and deletions strictly filter by `tenant_id`, `tender_id`, or `document_id`.
