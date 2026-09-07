# Evidence + Compliance Pipeline Architecture & Technical Reference

## Overview & Technical Scope

Phase 9 implements an evidence-backed, snapshot-reproducible compliance pipeline in the Argus backend (`services/api`). The system guarantees historical report integrity, evidence ownership isolation, strict trust distinction between document claims and live registry verifications, and deterministic rule evaluation.

---

## 1. Canonical Evidence Model

The `Evidence` domain model ([domain.py](file:///c:/Users/user/OneDrive/Documents/My%20Projects/Argus-main/services/api/app/models/domain.py)) and schema ([canonical.py](file:///c:/Users/user/OneDrive/Documents/My%20Projects/Argus-main/services/api/app/schemas/canonical.py)) store structured, fine-grained evidence records with complete sourcing provenance.

### Schema Fields & Provenance
* `id`: Authoritative UUID v4 primary key.
* `bidder_id`: Foreign key to `bidders.id`.
* `tender_id`: Foreign key to `tenders.id`.
* `document_id`: Optional foreign key to `documents.id`.
* `extracted_fact_id`: Optional foreign key to `extracted_facts.id`.
* `verification_result_id`: Optional foreign key to `verification_results.id`.
* `run_id`: Foreign key to `compliance_runs.id`.
* `source_type`: Provenance category (`"DOCUMENT"`, `"REGISTRY"`, `"PORTAL_CACHE"`, `"DEMO"`).
* `source_reference`: Reference string (e.g. document filename or API reference ID).
* `page_number`: 1-based page number where extracted text was located.
* `snippet`: Raw text snippet or verified summary representation.
* `sha256`: Digest of the underlying document bytes at extraction time.
* `verification_mode`: Sourcing operational mode (`LIVE`, `PORTAL_CACHED`, `DEMO`, `DOCUMENT`).
* `verification_status`: Verification outcome (`VERIFIED`, `UNVERIFIED`, `MISMATCH`, `SERVICE_ERROR`, `UNAVAILABLE`, `TIMEOUT`).
* `provider_identifier`: Identifier of the registry provider or extraction engine.
* `observed_at`: UTC timestamp when evidence was observed or verified.

---

## 2. Trust Semantics & Verification Modes

The pipeline enforces strict truthfulness boundaries between extracted document claims and live external registry verifications:

1. **Document Extraction Claims**:
   - `verification_mode = VerificationMode.DOCUMENT`
   - `verification_status = VerificationStatus.UNVERIFIED`
   - Extracted text claims (e.g. turnover in PDF certificates or claimed GSTIN) are never automatically upgraded to live verified status.
2. **Registry Verification Adapters**:
   - `verification_mode` reflects actual operational mode (`LIVE`, `PORTAL_CACHED`, `DEMO`).
   - Mismatches, network errors, timeouts, or fallback modes preserve their true status (`MISMATCH`, `SERVICE_ERROR`, `TIMEOUT`, `UNAVAILABLE`).
   - System error or missing configuration states never simulate successful verification.

---

## 3. Evidence Ownership Isolation & Normalization

`EvidenceNormalizationService` ([evidence_service.py](file:///c:/Users/user/OneDrive/Documents/My%20Projects/Argus-main/services/api/app/services/evidence_service.py)) provides centralized validation:

* **Ownership Validation**:
  - `validate_ownership()` verifies that bidder IDs, tender IDs, document IDs, facts, and verification results belong to the target entity.
  - Cross-bidder or cross-tender linkage raises explicit `ValueError` exceptions to prevent data leakage.
* **Deterministic UUID Generation**:
  - Generates authoritative primary keys for evidence entries.

---

## 4. Historical Input Snapshot Strategy

To prevent historical report mutation when new facts are extracted or rules are reprocessed:

* `ComplianceRun.input_snapshot_json` records a complete, self-contained input snapshot at run execution time:
  - `snapshot_version`: Version string (e.g., `"1.0"`).
  - `evaluated_at`: ISO timestamp of run execution.
  - `approved_requirements`: List of approved `TenderRequirement` definitions active at run time.
  - `facts`: List of `ExtractedFact` records active at run time.
  - `verifications`: List of `VerificationResult` records generated during the run.
  - `evidence`: List of `Evidence` records created during the run.
  - `documents`: List of document metadata and SHA-256 digests.
* **Historical Reconstruction**:
  - `GET /api/v1/bidders/{id}/report?run_id=...` and `GET /api/v1/bidders/{id}/matrix` reconstruct matrix rows and evidence citations strictly from the recorded `input_snapshot_json` and run-scoped records.
  - Post-run fact extractions or requirement updates do not alter historical reports.

---

## 5. Compliance Matrix & Citation Structure

`ComplianceMatrixRead` exposes structured matrix rows (`ComplianceMatrixRow`):

* `clause`: Clause reference (e.g. `"4.2"`).
* `requirement_type`: Categorical requirement type.
* `field`: Canonical fact key.
* `operator`: Deterministic operator (`EQ`, `GTE`, `EXISTS`, etc.).
* `expected_value`: Rule threshold or expected value.
* `observed_value`: Value observed during evaluation.
* `status`: Outcome status (`PASS`, `FAIL`, `REVIEW_REQUIRED`, `UNKNOWN`, `NOT_APPLICABLE`).
* `evidence_refs`: Array of evidence citations (`document_id`, `source_page`, `source_text`, `document_sha256`, `verification_mode`, `verification_timestamp`).
* `verification_refs`: Array of registry verification citations.
* `source_refs`: Array of fact/document source citations.
* `review_required`: True if requirement failed or requires manual officer review.

---

## 6. Separation of Automated Compliance & Human Decisions

* **Automated Engine**: `ComplianceEngine` ([engine.py](file:///c:/Users/user/OneDrive/Documents/My%20Projects/Argus-main/services/api/app/compliance/engine.py)) evaluates pure deterministic logic on approved rules.
* **Human Procurement Decision**: `HumanDecision` ([bidders.py](file:///c:/Users/user/OneDrive/Documents/My%20Projects/Argus-main/services/api/app/api/v1/bidders.py)) records officer qualification state (`QUALIFIED`, `DISQUALIFIED`, `MANUAL_REVIEW`, `PENDING`).
* Human officer decisions remain separate top-level fields (`human_decision`) and never overwrite or mutate deterministic `ComplianceRun.overall_status`.

---

## 7. Database Migration & Historical Limitations

* **Alembic Migration**: `7f3416a29033_evidence_compliance_pipeline_fields.py` adds `input_snapshot_json` to `compliance_runs` and sourcing columns to `evidence`.
* **Legacy Run Handling**: For legacy compliance runs created prior to Phase 9 that lack snapshot data, reports output `historical_limitations_notice = "Historical compliance run was completed before snapshot recording (Phase 9). Evidence citations and exact evaluation inputs cannot be reconstructed historically for this run."`
