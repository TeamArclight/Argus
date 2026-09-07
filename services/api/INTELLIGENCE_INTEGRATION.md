# ARGUS Intelligence Service Backend Integration Contract

## Overview

This document specifies the contract-first integration boundary between the ARGUS FastAPI Backend and the ARGUS Intelligence Service (owned by Person C).

The backend interfaces with the intelligence service via structured HTTP calls to extract candidate tender eligibility requirements and candidate bidder submission facts. The backend strictly validates all incoming model outputs against canonical Pydantic models, preserves truthful provenance, enforces candidate approval policies, and manages job states without delegating deterministic compliance evaluation or human decision authority to AI model outputs.

---

## 1. Service Contract & Endpoints

### Endpoints (Person C Specification Handoff)

| Method | Endpoint Path | Description |
|---|---|---|
| `POST` | `/api/v1/extract/tender` | Extract candidate eligibility requirements from a tender notice |
| `POST` | `/api/v1/extract/bidder-document` | Extract candidate facts from a bidder submission document |

---

## 2. Configuration Settings

The backend resolves intelligence service endpoints dynamically from environment configuration (`app/core/config.py`):

| Setting Key | Description | Default |
|---|---|---|
| `ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL` | Full URL for tender extraction endpoint | `None` (Unconfigured) |
| `ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL` | Full URL for bidder document extraction endpoint | `None` (Unconfigured) |
| `ARGUS_INTELLIGENCE_API_KEY` | Optional Bearer authentication token | `None` |
| `REQUEST_TIMEOUT_SECONDS` | HTTP request timeout in seconds | `10.0` |

If an endpoint URL is unconfigured or set to empty, `AIServiceAdapter` immediately returns an `error_code="NOT_CONFIGURED"` result without making transport attempts.

---

## 3. Secure Document Transfer Strategy

To ensure zero information leakage and prevent path traversal vulnerability vectors:
- **No Absolute Filesystem Paths**: The backend **never** passes internal server filesystem paths (e.g. `C:\Users\...` or `/data/uploads/...`) to external services.
- **Byte Transfer / Base64 Payload**: Physical file content is retrieved from the Phase 7 `StorageProvider` interface and transmitted via base64 encoded byte string (`file_bytes_base64`) or authenticated temporary token reference.
- **Identity & Hash Preservation**: `document_id` and `document_sha256` are explicitly passed across the boundary and checked on response return.

---

## 4. Request & Response Payload Specs

### 4.1 Tender Requirement Extraction (`POST /api/v1/extract/tender`)

#### Request Body Schema
```json
{
  "contract_version": "1.0",
  "request_id": "8f3b202e-9d21-4f11-9a74-d4ef19a842b1",
  "tender_id": "t_2026_gem_001",
  "document_id": "doc_991823",
  "document_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "filename": "gem_tender_notice.pdf",
  "content_type": "application/pdf",
  "file_bytes_base64": "<base64_encoded_file_bytes>"
}
```

#### Response Body Schema (Success)
```json
{
  "contract_version": "1.0",
  "request_id": "8f3b202e-9d21-4f11-9a74-d4ef19a842b1",
  "status": "COMPLETED",
  "provider_model": "gemini-2.5-pro",
  "requirements": [
    {
      "clause": "4.2.1",
      "requirement_type": "TURNOVER",
      "field": "financial.average_annual_turnover",
      "operator": "GTE",
      "expected_value": 5000000.0,
      "unit": "INR",
      "mandatory": true,
      "source_page": 12,
      "source_text": "The bidder must have an average annual turnover of at least Rs. 50 Lakhs during the last 3 financial years.",
      "confidence": 0.95,
      "requires_verification": true
    }
  ]
}
```

---

### 4.2 Bidder Document Fact Extraction (`POST /api/v1/extract/bidder-document`)

#### Request Body Schema
```json
{
  "contract_version": "1.0",
  "request_id": "c71a33b9-1120-4e31-8f9d-192a83ef112c",
  "bidder_id": "b_acme_corp",
  "document_id": "doc_771239",
  "document_type": "GST_CERT",
  "document_sha256": "8a32b001fc9e421...",
  "filename": "gst_registration_cert.pdf",
  "content_type": "application/pdf",
  "file_bytes_base64": "<base64_encoded_file_bytes>"
}
```

#### Response Body Schema (Success)
```json
{
  "contract_version": "1.0",
  "request_id": "c71a33b9-1120-4e31-8f9d-192a83ef112c",
  "status": "COMPLETED",
  "provider_model": "gemini-2.5-pro",
  "facts": [
    {
      "field": "gstin",
      "value": "27AAACA12341ZV",
      "source_page": 1,
      "source_text": "GSTIN / UIN: 27AAACA12341ZV",
      "confidence": 0.98
    }
  ]
}
```

---

## 5. Candidate Approval Policy & Deterministic Compliance Boundary

1. **Candidate Approval Policy (`is_approved`)**:
   - AI model outputs extract candidate rules (`TenderRequirement`).
   - High-confidence candidates (`confidence >= 0.9`) default to `is_approved = True`. Lower confidence candidates default to `is_approved = False` requiring procurement officer approval.
   - Manually created requirements default to `is_approved = True`.

2. **Deterministic Compliance Boundary**:
   - The `ComplianceEngine` evaluates **only** approved rules (`is_approved == True`).
   - Model outputs are prohibited from setting compliance status (`PASS`, `FAIL`, `REVIEW_REQUIRED`) or human decision status (`QUALIFIED`, `DISQUALIFIED`).

---

## 6. Non-Destructive Reprocessing & Idempotency

- When re-extracting requirements or facts for an existing tender or bidder document:
  - Any existing `TenderRequirement` or `ExtractedFact` row referenced in a prior `RuleEvaluation` (`evidence_ids`) is **preserved** to maintain historical compliance audit trails.
  - Only unreferenced existing rows for that target document are updated/replaced.

---

## 7. Truthful Provenance Model

Every extracted fact and requirement records full provenance metadata:
- `document_id`: ID of physical document
- `document_sha256`: SHA-256 checksum of physical file
- `source_page`: 1-based page number where fact was detected (or `null` if unpaged)
- `source_text`: Exact verbatim snippet from source file
- `confidence`: Calibrated score between `0.0` and `1.0`
- `metadata_json`: Contains `request_id`, `provider_model`, and extraction timestamp.

---

## 8. Failure Semantics & Error Codes

The backend handles failures safely without crashing or exposing internal stack traces:

| Error Code | HTTP Scenario | Retryable | Description |
|---|---|---|---|
| `NOT_CONFIGURED` | Endpoint URL unconfigured or 404 | No | Gateway URL missing |
| `UNAVAILABLE` | HTTP 502/503/504, Connection error | Yes (Max 3) | Gateway down or unreachable |
| `TIMEOUT` | HTTP 408 / request timeout | Yes (Max 3) | Request exceeded `REQUEST_TIMEOUT_SECONDS` |
| `PROCESSING_FAILED` | HTTP 400/422 or payload rejection | No | Malformed request or unparseable file |
| `INVALID_RESPONSE` | Invalid JSON or Pydantic validation failure | No | Service returned invalid schema payload |

### Multi-Document Partial Failures
When processing multiple bidder documents:
- If all documents succeed -> Job `COMPLETED` (Progress 100%).
- If subset succeeds and subset fails -> Job `COMPLETED` with `error_message="Partial extraction completion: X/Y processed successfully"`. Successful facts are preserved.
- If all documents fail -> Job `FAILED`.

---

## 9. RBAC & Audit Events

- **Mutations** (`POST /api/v1/tenders/{id}/process`, `POST /api/v1/bidders/{id}/process-documents`): Restricted to `ADMIN` and `PROCUREMENT_OFFICER` roles.
- **Audit Events**: Emits structured audit events:
  - `TENDER_EXTRACTION_REQUESTED`
  - `TENDER_EXTRACTION_COMPLETED`
  - `TENDER_EXTRACTION_FAILED`
  - `DOCUMENT_EXTRACTION_REQUESTED`
  - `DOCUMENT_EXTRACTION_COMPLETED`
  - `DOCUMENT_EXTRACTION_FAILED`
