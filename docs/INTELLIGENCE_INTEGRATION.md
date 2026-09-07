# ARGUS Intelligence Integration Architecture

This document defines the technical integration contract, service boundary, security controls, and failure handling policies between the ARGUS Procurement Platform Backend (`services/api`) and the ARGUS Intelligence Gateway (`services/intelligence`).

---

## 1. Intelligence Boundary & Scope

1. **Service Decoupling**: The backend platform communicates with the intelligence service purely via structured HTTP JSON endpoints. The backend does not run direct LLM inference, embedding pipelines, or vector databases.
2. **Contract-First Specification**: The intelligence gateway is treated as a versioned third-party dependency. Endpoints operate under strict request/response envelope schemas (`contract_version: "1.0"`).
3. **Model Authority Isolation**: AI model output is strictly non-authoritative. The model cannot set requirement approval status (`is_approved`), compliance pass/fail determinations, or officer audit records.

---

## 2. Input Validation & Secure Document Transfer

To prevent synthetic, corrupted, or spoofed inputs from reaching the intelligence gateway, the backend enforces strict pre-dispatch validation:

* **Document Identity**: Requires a verified, non-empty `document_id` corresponding to a persisted `documents` row.
* **Document Hash Integrity**: Requires a 64-character hexadecimal SHA-256 string (`document_sha256`).
* **Raw Document Bytes**: Requires actual, non-empty document binary bytes (`file_bytes`).
* **Digest Equivalence**: Computes `hashlib.sha256(file_bytes).hexdigest()` and verifies case-insensitive equality against `document_sha256`. If mismatched, the backend aborts with `DOCUMENT_SHA256_MISMATCH` before dispatching any HTTP request.
* **Bidder Association**: Document extraction requests require an explicit `bidder_id`.

No synthetic fallbacks (e.g., mock hashes, dummy bytes, or URI-derived IDs) are permitted.

---

## 3. Strict Versioned Response Envelope

Endpoints return structured response envelopes (`AIResponseEnvelope`). For `contract_version: "1.0"`:

* **Tender Extraction**: Output must be contained in the `requirements` array (`list[TenderRequirementCreate]`). Missing or `null` `requirements` arrays return `SCHEMA_VALIDATION_FAILED`. Legacy `data` fields are rejected.
* **Bidder Extraction**: Output must be contained in the `facts` array (`list[ExtractedFactCreate]`). Missing or `null` `facts` arrays return `SCHEMA_VALIDATION_FAILED`.
* **Provenance Verification**: Returned envelopes must echo back matching `request_id`, `document_id`, `document_sha256`, and (where applicable) `bidder_id`. Mismatches abort processing with specific error codes (`REQUEST_ID_MISMATCH`, `DOCUMENT_ID_MISMATCH`, `BIDDER_ID_MISMATCH`).

---

## 4. Extraction Authority & Officer Approval Policy

* **Unapproved Candidates**: All newly AI-extracted tender requirements default to `is_approved = False`, regardless of model confidence scores (even if `confidence == 1.0`).
* **Scrubbing Model Authority**: Model-supplied fields such as `is_approved`, `approved_by`, `approved_at`, `approval_status`, or `approved` are unconditionally scrubbed during parsing.
* **Officer Approval Endpoint**: `POST /api/v1/tenders/{tender_id}/requirements/{requirement_id}/approve` allows authorized `ADMIN` or `PROCUREMENT_OFFICER` users to convert an unapproved candidate into an authoritative rule. Approval records the authenticated user ID and timestamp in `metadata_json` and emits an audit event.
* **Compliance Execution**: The compliance engine strictly filters for `is_approved == True`. Unapproved candidates are ignored during compliance evaluation. If no approved rules exist, evaluation yields `UNKNOWN` with `NO_APPROVED_REQUIREMENTS`.

---

## 5. Non-Destructive Data Preservation

* **Reprocessing Tenders**: Re-extracting tender requirements deduplicates new candidates while preserving all existing approved rules and manual requirements intact.
* **Reprocessing Bidder Documents**: Re-extracting facts from bidder documents performs non-destructive deduplication without deleting existing `ExtractedFact` rows, preserving historical evidence trails.

---

## 6. Error Handling & Health Integration

* **Fail-Closed Policy**: HTTP 4xx errors return `AI_SERVICE_REQUEST_REJECTED` or `AI_SERVICE_AUTH_ERROR`. HTTP 5xx or connection timeouts return retryable `AI_SERVICE_UNAVAILABLE`. Sensitive internal exception details are scrubbed from user-facing error messages.
* **Health Endpoint**: `GET /health/integrations` reports `intelligence.configured: true` only when explicit endpoint URLs are configured in environment settings.
