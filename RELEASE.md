# Argus Release Notes & Hardening Specifications

## Phase 12: Deterministic Compliance Engine Hardening

### 1. Engine & Policy Versioning
- **Engine Version (`ENGINE_VERSION`)**: `2.0.0`
- **Normalization Policy Version (`NORMALIZATION_POLICY_VERSION`)**: `1.0.0`
- **Operator Semantics Version (`OPERATOR_SEMANTICS_VERSION`)**: `1.1.0`
- **Financial Context Policy Version (`FINANCIAL_CONTEXT_POLICY_VERSION`)**: `1.0.0`
- **Temporal Policy Version (`TEMPORAL_POLICY_VERSION`)**: `1.0.0`
- **Supported Replay Engine Versions (`SUPPORTED_REPLAY_VERSIONS`)**: `{"2.0.0"}`

### 2. Financial Context & Scale Semantics
- **Typed Financial Context (`FinancialContext`)**: Enforces explicit compatibility checks across `currency`, `metric` (e.g. `turnover`, `net_worth`), `financial_year` (e.g. `FY2023-24`), `averaging_period` (e.g. `3_year_avg`), and `is_base_unit`.
- **No Context Bypasses**: Failed financial context parsing or mismatched currencies/metrics/periods strictly return `REVIEW_REQUIRED` with canonical reason codes (`CURRENCY_MISMATCH`, `FINANCIAL_CONTEXT_MISMATCH`, `MISSING_FINANCIAL_CONTEXT`). Financial values never fall back to context-free numeric comparison.
- **Scale Independence**: Rule units (`rule.unit`) are resolved independently and never imposed on observed input values. Observed scales are derived strictly from explicit input representations or input metadata.
- **Single Scale Application & Base-Unit Protection**: Explicit scales (e.g. `"5 Crore"`) are scaled to base units exactly once. Already-normalized base-unit inputs (`is_base_unit=True` or `50000000 INR`) are not multiplied again.
- **Contradictory Scale Detection**: Conflicting scales between raw input text and metadata (e.g. `"5 Crore"` with metadata `unit="Lakh"`) return `REVIEW_REQUIRED` with `UNIT_MISMATCH`.
- **Exact Numeric Bounds**: `Decimal` arithmetic enforces string length $\le 100$, total digits $\le 38$, and exponent bounds $[-30, 30]$. Floats, booleans, `NaN`, `Inf`, and corrupt comma structures are rejected.

### 3. Temporal Semantics & Clock Policy
- **Pure Functional Clock**: Removed all `datetime.now()` fallbacks from pure evaluation.
- **Evaluation Clock Context**: Time-dependent rules (`EXPIRES_AFTER`, `MIN_YEARS_EXPERIENCE_FROM_DATE`, relative timestamps) require an explicit `evaluation_timestamp` (derived from recorded `run.started_at` in UTC). Missing clock context fails closed with `UNKNOWN` (`MISSING_EVALUATION_CLOCK`) and `evaluated_at=None`.
- **Three-Tier Classification**:
  - `DATE_ONLY`: Calendar date comparisons (`YYYY-MM-DD`) without time-of-day or timezone offsets (correct across leap years).
  - `AWARE_DATETIME`: ISO-8601 timestamps normalized to UTC prior to comparison.
  - `NAIVE_DATETIME`: Local timestamps without timezone offsets.
- **Ambiguity & Mismatch Handling**:
  - Comparing `NAIVE_DATETIME` against `AWARE_DATETIME` returns `UNKNOWN` with `AMBIGUOUS_TIMEZONE`.
  - Comparing `DATE_ONLY` against datetimes returns `UNKNOWN` with `TEMPORAL_CONTEXT_MISMATCH`.

### 4. Applicability Truth Table & Exemption Policies
- **Boolean Validation**: Strict parsing of boolean metadata (`True`, `False`, `"true"`, `"false"`, `1`, `0`). Malformed strings (e.g. `"maybe"`) return `UNKNOWN` with `INVALID_APPLICABILITY_POLICY`.
- **Approved Exemptions**: Only approved exemption policies (`"APPROVED"`, `True`, `"GRANTED"`) evaluate to `NOT_APPLICABLE` (`NOT_APPLICABLE_EXEMPTION`). Unsupported exemptions return `UNKNOWN` with `INVALID_APPLICABILITY_POLICY`.
- **Categorical Bidder Context**: Bidder type/category must come from authorized context. Missing bidder category is NOT treated as a categorical mismatch.
- **Mandatory Exemption Protection**: `optional_missing_policy` cannot override an explicitly applicable mandatory requirement. Missing evidence on mandatory requirements always evaluates to `UNKNOWN` (`MISSING_EVIDENCE`).

### 5. Historical Replay & Provenance
- **Canonical Rule Hashing**: Rules hash (`canonical_rule_hash`) is computed via SHA-256 over deterministic JSON representations of rule attributes (`clause`, `field`, `operator`, `expected_value`, `unit`, `mandatory`, `requirement_type`) and semantic policy metadata (`applicability`, `currency`, `financial_year`, `metric`, `averaging_period`, `optional_missing_policy`), while strictly omitting transient database IDs and timestamps.
- **Replay Boundaries**: Historical reconstruction uses authoritative recorded snapshots (`input_snapshot_json`). Full semantic re-evaluation is bounded to supported engine versions matching `ENGINE_VERSION`. Incompatible historical versions return `REVIEW_REQUIRED` with `HISTORICAL_VERSION_UNSUPPORTED` without fabricating missing historical clock or currency metadata.

---

## Phase 11: Release Hardening & Concurrency Model

### Database Concurrency & Scoped Locking
- **Durable Active-Lock Coordination**: `OperationLockService` provides atomic mutual exclusion per `(resource_type, resource_id, operation)` using database-level unique constraints (`uq_active_operation_locks_resource`) and PostgreSQL row-level locks (`SELECT ... FOR UPDATE`).
- **Scoped Lock Release**: `release_lock()` strictly requires the caller's `job_id` and releases only the lock owned by that specific operation. Missing or mismatched owner identifiers are rejected (`ValueError` / `False`).
- **Fail-Closed Ambiguous Recovery**: Stale locks with missing or unverified background jobs fail closed with HTTP 409 (`OPERATION_LOCK_RECOVERY_REQUIRED`). Locks are only automatically reconciled when the previous job has definitively terminated in a terminal state (`COMPLETED`, `REVIEW_REQUIRED`, `FAILED`).
- **Monotonic Sequence Allocation**: `JobEventService` allocates deterministic, sequential `seq` numbers per job under database row locks (`SELECT id FROM processing_jobs WHERE id = :job_id FOR UPDATE`), preventing duplicate or unordered event stream cursors.

### Request Idempotency & Deduplication
- **Idempotency Scoping**: `IdempotencyService` enforces unique scopes on `(principal_id, resource_type, resource_id, operation, key)` with SHA-256 payload fingerprint verification. Concurrent identical requests return HTTP 409 (`OPERATION_IN_PROGRESS`) and replay cached `COMPLETED` responses once finished.

---

## Validation Tiers & Test Matrix

| Validation Tier | Scope & Target | Execution Environment | Status |
| :--- | :--- | :--- | :--- |
| **PostgreSQL Migration Validation** | Full Alembic migration upgrade (`head`), downgrade, re-upgrade, and drift check against SQLAlchemy declarative models. | CI live PostgreSQL 16 service container & disposable databases. | Verified (Head: `9f5627b30055`) |
| **Dedicated PostgreSQL Concurrency Suite** | 7 live race-condition & concurrency tests in `test_postgresql_compatibility.py` (same-key deduplication, different-key mutual exclusion, DB unique constraint enforcement, multi-threaded monotonic sequence allocation under `FOR UPDATE`, rollback isolation, fail-closed ambiguous recovery, terminal reconciliation). | CI live PostgreSQL 16 service container against disposable `TEST_POSTGRES_URL` prepared via Alembic. | 7 tests skipped locally, verified in CI |
| **Backend Test Suite** | 241 unit and integration tests covering compliance engine, numeric context, temporal semantics, applicability, risk engine, verification adapters, RBAC policies, error envelopes, and API endpoints. | Local development and CI SQLite in-memory/file fallback. | **241 passed, 7 skipped** |
| **OpenAPI Contract Validation** | Byte-for-byte schema export and drift verification against `contracts/openapi.json`. | CI OpenAPI drift check step. | Verified (`cd9eacd10475a8de6293e991dbf62e81750d0990d0a3ff485ab6f4b9c08f941e`) |

---

## Database Migrations
- **Current Alembic Head**: `9f5627b30055` (`9f5627b30055_add_idempotency_records.py`)
- Verified via `python -m alembic heads` and `python -m alembic current` against disposable database instances.
