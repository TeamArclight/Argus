# Argus Release Hardening & Deployment Notes (Phase 11)

## Architecture & Concurrency Model

### Database Concurrency & Scoped Locking
- **Durable Active-Lock Coordination**: `OperationLockService` provides atomic mutual exclusion per `(resource_type, resource_id, operation)` using database-level unique constraints (`uq_active_operation_locks_resource`) and PostgreSQL row-level locks (`SELECT ... FOR UPDATE`).
- **Scoped Lock Release**: `release_lock()` strictly requires the caller's `job_id` and releases only the lock owned by that specific operation. Missing or mismatched owner identifiers are rejected (`ValueError` / `False`).
- **Fail-Closed Ambiguous Recovery**: Stale locks with missing or unverified background jobs fail closed with HTTP 409 (`OPERATION_LOCK_RECOVERY_REQUIRED`). Locks are only automatically reconciled when the previous job has definitively terminated in a terminal state (`COMPLETED`, `REVIEW_REQUIRED`, `FAILED`).
- **Monotonic Sequence Allocation**: `JobEventService` allocates deterministic, sequential `seq` numbers per job under database row locks (`SELECT id FROM processing_jobs WHERE id = :job_id FOR UPDATE`), preventing duplicate or unordered event stream cursors.

### Request Idempotency & Deduplication
- **Idempotency Scoping**: `IdempotencyService` enforces unique scopes on `(principal_id, resource_type, resource_id, operation, key)` with SHA-256 payload fingerprint verification. Concurrent identical requests return HTTP 409 (`OPERATION_IN_PROGRESS`) and replay cached `COMPLETED` responses once finished.

---

## Validation Tiers & Test Matrix

| Validation Tier | Scope & Target | Execution Environment |
| :--- | :--- | :--- |
| **PostgreSQL Migration Validation** | Full Alembic migration upgrade (`head`), downgrade, re-upgrade, and drift check against SQLAlchemy declarative models. | CI live PostgreSQL 16 service container & disposable databases. |
| **Dedicated PostgreSQL Concurrency Suite** | 7 live race-condition & concurrency tests in `test_postgresql_compatibility.py` (same-key deduplication, different-key mutual exclusion, DB unique constraint enforcement, multi-threaded monotonic sequence allocation under `FOR UPDATE`, rollback isolation, fail-closed ambiguous recovery, terminal reconciliation). | CI live PostgreSQL 16 service container against disposable `TEST_POSTGRES_URL` prepared via Alembic. |
| **General SQLite-Backed Test Suite** | 215 unit and integration tests covering business rules, verification engines, RBAC policies, error envelopes, and API endpoints. | Local development and CI SQLite in-memory/file fallback. |
| **Docker Configuration** | Multi-stage Dockerfile with unprivileged user (`appuser:10001`), container healthchecks, and volume mappings. | Verified configuration files; deployment is target-environment dependent. |

---

## Known Boundaries & Operational Limits

1. **Single-Node In-Process Worker**: The current execution engine uses FastAPI background tasks within a single node process. Distributed worker queues (e.g. Celery/RabbitMQ/Redis) are not implemented; scale-out across multiple worker processes requires shared queue infrastructure in future phases.
2. **PostgreSQL Concurrency Scope**: Dedicated PostgreSQL concurrency tests target database-level locking, sequence allocation, and idempotency constraints. General functional test suites execute against SQLite.
3. **No Distributed Production Certification**: Phase 11 hardens core database safety, error handling, SSE event streaming, and request correlation. Multi-region high-availability and distributed worker orchestration remain future milestones.
