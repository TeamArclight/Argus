# ARGUS API Backend Release & Hardening Documentation

This document describes the operational architecture, deployment configuration, security controls, error contracts, request correlation, concurrency semantics, and testing procedures for the ARGUS compliance verification platform backend.

---

## 1. Environment & Configuration Management

The backend configuration is managed via `app/core/config.py` using Pydantic Settings and loaded from environment variables or `.env` files.

### Key Environment Variables

| Variable | Type | Description | Default / Requirement |
|---|---|---|---|
| `APP_ENV` | `str` | Application environment (`development`, `staging`, `production`) | `development` |
| `DATABASE_URL` | `str` | SQLAlchemy database URL (SQLite or PostgreSQL) | `sqlite:///./argus_dev.db` |
| `ARGUS_JWT_SECRET` | `str` | Secret key for PyJWT token signing | **Required**, min 32 chars |
| `ARGUS_STORAGE_LOCAL_PATH` | `str` | Local directory for document uploads | `./data/uploads` |
| `ARGUS_MAX_UPLOAD_MB` | `int` | Maximum allowed upload file size | `20` |
| `ALLOW_DEMO_SEED` | `bool` | Explicit environment flag for demo seeding | `false` |
| `CORS_ALLOWED_ORIGINS` | `str` | Comma-separated list of allowed CORS origins | `http://localhost:3000` |

### Production Safeguards (`APP_ENV=production`)
- Wildcard CORS origins (`*`) are strictly prohibited when credentials are enabled.
- Weak or default JWT secrets (`< 32 chars`) are rejected on startup.
- Unconfigured DEMO fallback modes are explicitly reported in integration health status.

---

## 2. API Error Envelope & Request Correlation

### Public Error Envelope
All error responses return a standardized JSON structure:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description of error.",
    "request_id": "8f3b2a1c-9d0e-4f1a-8b2c-3d4e5f6a7b8c",
    "details": {}
  }
}
```

### Request Correlation (`X-Request-ID`)
- The `RequestCorrelationMiddleware` inspects incoming HTTP request headers for `X-Request-ID`.
- Incoming values are validated against format boundaries (`^[a-zA-Z0-9_\-]{1,64}$`).
- If absent or invalid, a UUID v4 string is generated.
- The request ID is context-bound using `contextvars`, attached to request state, injected into all log entries, and returned in response headers (`X-Request-ID`).

---

## 3. Database Migrations & PostgreSQL Compatibility

### Alembic Revision Baseline
- Schema migrations are managed via Alembic (`services/api/alembic`).
- Current Head Revision: `9f5627b30055_add_idempotency_records.py`.
- Models use database-agnostic SQLAlchemy types (`sa.JSON`, `sa.DateTime`, `sa.String`) fully compatible with SQLite and PostgreSQL.

### Migration Commands
```bash
# Upgrade database to head revision
python -m alembic upgrade head

# Verify zero schema drift against SQLAlchemy models
python -m alembic check
```

---

## 4. Concurrency & Request Idempotency

### Idempotency Control (`X-Idempotency-Key`)
- Endpoints executing state-changing verification or processing (`POST /api/v1/bidders/{id}/verify`, `POST /api/v1/tenders/{id}/process`) support the `X-Idempotency-Key` header.
- Scoped to `(principal_id, operation, key)`.
- If an identical request key is received:
  - **Status `COMPLETED`**: Returns cached response payload immediately.
  - **Status `PROCESSING`**: Returns `409 Conflict` (`OPERATION_IN_PROGRESS`).
  - **Mismatched request parameters**: Returns `409 Conflict` (`IDEMPOTENCY_KEY_COLLISION`).

### Active Processing Deduplication
- Triggering verification or extraction while a job/run is actively `QUEUED` or `RUNNING` returns the existing active `ProcessingJob` without spawning duplicate background workflows.

---

## 5. Job Lifecycle & SSE Streaming

### Event Streaming Endpoint
- `GET /api/v1/jobs/{id}/events` streams real-time Server-Sent Events (SSE).
- Reconnection support via `Last-Event-ID` header.
- Uses short-lived database sessions during long-polling loops to prevent DB connection pool exhaustion.
- Sends SSE comment heartbeats (`: ping\n\n`) every 15s to keep idle client connections alive.
- Clean stream termination on terminal job states (`COMPLETED`, `FAILED`).

---

## 6. Health & Readiness Probes

| Endpoint | Purpose | Details |
|---|---|---|
| `GET /health` | Liveness | Returns `{"status": "ok", "service": "argus-api"}` |
| `GET /health/readiness` | Readiness | Performs lightweight `SELECT 1` DB query and local storage write probe. Does NOT call external government APIs. |
| `GET /health/integrations` | Integration Status | Reports configured verification modes (`LIVE`, `PORTAL_CACHED`, `DOCUMENT`, `DEMO`) for GST, Udyam, MCA, EPFO, ESIC, Blacklist. |

---

## 7. Metrics & Observability

- `GET /api/v1/metrics`: Protected endpoint (requires `ADMIN` role).
- Returns operational counters (`uptime_seconds`, `requests_total`, `errors_total`, `jobs_completed_total`, `jobs_failed_total`, `provider_successes_total`).
- Avoids high-cardinality sensitive labels (no bidder IDs, PAN, or GSTIN strings in metrics).

---

## 8. Docker Deployment

### Building & Running with Compose
```bash
# Build and launch backend API, PostgreSQL, and Web services
docker compose -f infra/compose.yaml up -d --build
```
- API container runs as non-root user (`appuser`, UID 10001).
- Container healthchecks configured for PostgreSQL (`pg_isready`) and API (`curl`).
- Uploaded document storage persisted via named volume `argus_uploads`.

---

## 9. Demo Seed Safety Policy

- Demo seeding (`scripts/seed_demo.py`) and reset (`scripts/reset_demo.py`) require **BOTH**:
  1. `ALLOW_DEMO_SEED=true` set in environment.
  2. A positively identified disposable database URL (containing `demo`, `disposable`, `test`, or `tmp`).
- Refuses to execute against production or development databases (`argus_dev.db`).

---

---

## 10. Verification Matrix & Validation Tiers

| Tier | Environment | Scope | Validation Method |
|---|---|---|---|
| **Local SQLite Suite** | Local Dev / In-Memory & File SQLite | Unit, regression, error contracts, API endpoints, deterministic compliance rules | Pytest suite (214 tests, 213 passed, 1 live PG skipped locally) |
| **PostgreSQL Migration Validation** | PostgreSQL 16 (CI Service) | Schema creation, baseline-to-head migration upgrade (`44d5c5ca3f11` -> `9f5627b30055`), populated data backfill, zero schema drift | `alembic upgrade head` and `alembic check` |
| **PostgreSQL Concurrency Validation** | PostgreSQL 16 (CI Service) | Same-key concurrent requests, different-key concurrent requests for same bidder, database unique constraint enforcement, multi-threaded `JobEvent` sequence allocation with `SELECT ... FOR UPDATE`, transaction rollback isolation, fail-closed ambiguous lock recovery, completed operation response recovery | Dedicated multi-threaded test suite in `test_postgresql_compatibility.py` |
| **Docker Deployment Verification** | Linux Container | Non-root `appuser` (UID 10001), healthchecks (`pg_isready`, HTTP liveness), volume persistence | Dockerfile & compose validation |

---

## 11. Known Operational & Concurrency Limitations

- **Single-Node / In-Process Execution**: Job processing runs within FastAPI background tasks / in-process workers. Concurrency safety is enforced at the database layer via row-level locks (`SELECT ... FOR UPDATE`) and database unique constraints (`uq_active_operation_locks_resource`, `uq_job_events_job_seq`, `uq_idempotency_scoped_key`). Distributed task queues (e.g. Celery, Redis Streams, Kafka) are outside the current architectural scope.
- **Fail-Closed Ambiguous Lock Recovery**: If a process crashes unexpectedly after acquiring an `ActiveOperationLock` but before creating a durable `ProcessingJob` or `ComplianceRun`, the lock remains in an ambiguous state. Subsequent requests fail closed with HTTP 409 (`OPERATION_LOCK_RECOVERY_REQUIRED`) to prevent automated replay of external verification without administrative or operational inspection.
- **Live Registry Credentials**: External verification adapters require production gateway credentials for `LIVE` mode. In development and test environments, `DEMO` and `PORTAL_CACHED` modes provide verified deterministic fixture responses.

