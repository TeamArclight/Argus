# ARGUS Verification and Risk Engine Backend Architecture

This document describes the backend architecture, provider health registry, verification modes, pure deterministic risk detection engine, freshness policies, historical snapshot authority, and AI/ML advisory integration boundaries for the Argus Procurement Verification platform.

---

## 1. Provider Registry, Capabilities, and Health Semantics

Argus interfaces with six domain verification providers:
1. **GST** (`general.gstin`): GSTIN validation, active status check, tax filing status.
2. **Udyam** (`general.udyam`): MSME Udyam registration validation, enterprise category classification.
3. **MCA** (`general.cin`): Corporate DIN/CIN lookup, company active status, director master check.
4. **EPFO** (`general.epfo`): EPF establishment search, challan payment verification.
5. **ESIC** (`general.esic`): ESIC employer registration and social security compliance.
6. **Blacklist** (`debarment.status`): Central debarment search, GeM blacklist verification.

### Configuration vs. Operational Health Separation
Argus cleanly distinguishes configuration status from operational health:
- **Configuration Status**: `CONFIGURED` (required credentials & API URLs present in settings) vs `UNCONFIGURED` (missing credentials/URLs).
- **Operational Health**:
  - `AVAILABLE`: Active mode is `DEMO`, `PORTAL_CACHED`, or `DOCUMENT`, or configured live gateway.
  - `UNAVAILABLE`: Unconfigured live provider or service failure.
  - `UNKNOWN`: Default status for live configured gateway prior to live query execution.
  - `DEGRADED`: Partial endpoint responsiveness or non-fatal gateway degradation.

> [!NOTE]
> Provider health endpoints (`GET /api/v1/providers`) report static configuration and cached health metadata **without** making expensive external HTTP pings on health calls.
> Demo providers report `DEMO` mode and are never represented as live government API integrations.

---

## 2. Verification Modes and Failure/Retry Policy

Supported explicit verification modes:
- **`LIVE`**: Authorized government registry API integrations. Used ONLY when explicit credentials exist. No silent fallback to `DEMO` occurs if a live request fails.
- **`PORTAL_CACHED`**: Records previously obtained or portal-derived with explicit source observation timestamps (`source_observed_at`).
- **`DOCUMENT`**: Evidence extracted directly from submitted bidder documents.
- **`DEMO`**: Deterministic synthetic datasets for demonstration and testing.

### Failure & Retry Policy
- Live requests use strict bounded timeouts (`REQUEST_TIMEOUT_SECONDS = 10.0`).
- Retries are restricted to safe, idempotent GET queries using bounded backoff.
- Failures map to stable public error status codes (`CREDENTIAL_REJECTED`, `ACCESS_FORBIDDEN`, `ENTITY_NOT_FOUND`, `SERVICE_ERROR`, `TIMEOUT`, `PROVIDER_UNAVAILABLE`, `UNSUPPORTED_MODE`).
- Internal diagnostics are safely logged without exposing raw URL tokens, credentials, or sensitive HTTP headers in public error messages.

---

## 3. Pure Deterministic Risk Engine (`app/risk/engine.py`)

The `RiskEngine` is a pure, stateless function container (`evaluate_risks()`). It accepts facts, verifications, document metadata, bidder data, and preselected comparison metadata, returning structured `RiskSignalCandidate` items.

> [!IMPORTANT]
> The `RiskEngine` contains **no database access, no HTTP calls, no AI/ML calls, and zero side effects**. It is completely separate from `ComplianceEngine`.

### Deterministic Checks

1. **Identifier Consistency**:
   - **GSTIN/PAN Mismatch**: Validates GSTIN structure (`^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$`), extracts embedded PAN (`gstin[2:12]`), and checks against claimed PAN. Flags `GSTIN_PAN_MISMATCH` if mismatched.
   - **PAN & Udyam Regex**: Checks format validity (`INVALID_PAN_FORMAT`, `INVALID_UDYAM_FORMAT`).
   - **Exact Legal Name Comparison**: Compares normalized legal names across bidder records, document facts, and registry verifications. Exact normalized mismatches generate `LEGAL_NAME_MISMATCH`. No legal equivalence is claimed from fuzzy string matching alone.

2. **Cross-Document Consistency**:
   - **Conflicting Turnover (Same FY)**: Groups turnover facts by financial year (`financial_year` in metadata). Flags `CONFLICTING_TURNOVER_SAME_FY` if normalized numeric values differ for the same financial year.
   - **Financial Year Isolation**: Facts from different financial years (e.g. FY 2023-24 vs FY 2024-25) are explicitly kept separate and never compared as equivalent periods.
   - **Duplicate Document Hashes**: Compares document SHA-256 digests against preselected comparison metadata from other bidders. Flags `DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER` for officer review. Identical hashes are **not** treated as proof of fraud, recognizing standard tender templates and form reuse.
   - **OEM Authorizations**: Checks OEM authorization validity dates and flags `OEM_AUTHORIZATION_EXPIRED` if expired relative to evaluation date.

3. **Financial Consistency**:
   - Compares claimed vs verified turnover using canonical numeric normalizers (`ComplianceEngine._normalize_numeric`).
   - Does not silently convert currencies or exchange rates. Missing currency/unit context is preserved explicitly.

4. **Cached Record Freshness**:
   - Preserves both `source_observed_at` (original portal observation timestamp) and `checked_at` (Argus evaluation timestamp). `checked_at` is never substituted for an unknown `source_observed_at`.
   - Missing observation timestamps trigger `MISSING_FRESHNESS_TIMESTAMP`.
   - Observation timestamps older than Argus operational freshness policy (default 30 days for GST/MCA, 90 days for Udyam) trigger `STALE_CACHED_RECORD`.
   - Staleness is an advisory risk signal and does **not** directly disqualify a bidder unless an approved tender rule explicitly requires it.

---

## 4. Risk Aggregation & Non-Authority Guarantees

Phase 10 provides transparent, explainable risk summaries (`RiskSummaryRead`):
- `signal_count`
- `counts_by_severity` (`HIGH`, `MEDIUM`, `LOW`, `CRITICAL`)
- `counts_by_type`
- `unresolved_count`
- `source_mode_breakdown`
- `risk_engine_version` ("1.0")
- `risk_policy_version` ("1.0")

> [!CAUTION]
> **No Arbitrary Risk Score**: Argus does not calculate arbitrary or uncalibrated risk scores.
> **No Statutory Authority**: Risk signals remain strictly advisory for procurement officers. Risk signals **never** set rule `PASS`/`FAIL` status, mutate approved tender requirements, or set human officer decisions (`QUALIFIED`/`DISQUALIFIED`).

---

## 5. Exact Risk Evidence Linkage

- `RiskEngine` output candidates reference exact contributing input IDs (`extracted_fact_id` or `verification_result_id`).
- `BidVerificationService` maps these input IDs to Phase 9 `Evidence` IDs staged during the run.
- If mapping is missing, unresolved input IDs are recorded in `metadata_json["unmapped_input_ids"]` without inventing fake evidence citations.
- Cross-bidder, cross-tender, and cross-run evidence links are strictly rejected.

---

## 6. Historical Risk Snapshot Strategy

Historical runs maintain full snapshot authority inside `ComplianceRun.input_snapshot_json`:
- Contains `"risk_engine_version"`, `"risk_policy_version"`, `"risk_evaluation_timestamp"`, `"risk_signals"`, and `"risk_summary"`.
- Reports reconstruct historical risk summaries and signals directly from `input_snapshot_json`.
- Post-run fact updates, new registry verifications, or later risk runs will **never** alter historical risk snapshots.
- Legacy Phase 9 runs without risk snapshots return an explicit limitations notice without querying current DB rows.

---

## 7. AI/ML Advisory Integration Boundary

Person C owns future AI/ML risk models. Argus defines a canonical backend contract (`AdvisoryMLRiskSignalRead`):
- Fields: `contract_version`, `request_id`, `bidder_id`, `run_id`, `model_identifier`, `model_version`, `advisory_signal_type`, `explanation`, `supporting_evidence_ids`, `confidence_score`, `provenance`.
- **Validation**: AI/ML model outputs are strictly advisory. They cannot set compliance status, override rule evaluations, or alter human decision authority.
- No live ML inference is executed in the backend; fake model responses or mock inference endpoints are strictly prohibited.

---

## 8. Known Limitations

- **Live Registries**: Government API endpoints require official credentials. When unconfigured, live calls return `UNAVAILABLE`.
- **Fuzzy Name Matching**: Legal name comparisons use exact normalized string matching. Fuzzy matching is not used to infer legal identity equivalence.
- **Duplicate Document Hashes**: Duplicate hashes indicate content reuse but do not constitute authoritative proof of forgery or fraud.
