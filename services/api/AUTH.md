# ARGUS API Authentication & Procurement RBAC Guide

This document details the JWT-based authentication mechanism and procurement Role-Based Access Control (RBAC) architecture implemented in the ARGUS API service.

---

## 1. Overview

ARGUS enforces stateless, claim-driven authentication using JSON Web Tokens (JWT) passed via standard HTTP Bearer headers:

```http
Authorization: Bearer <JWT_TOKEN>
```

All non-public endpoints under `/api/v1` require a valid JWT access token. Caller-supplied identity headers or unauthenticated client inputs are strictly rejected.

---

## 2. Configuration & Environment Variables

Authentication parameters are configured via environment variables (or `.env` file) in `services/api/app/core/config.py`:

| Environment Variable | Default Value | Description |
| :--- | :--- | :--- |
| `ARGUS_JWT_SECRET` | *(Development Secret)* | HMAC SHA-256 secret key used for signing and verifying JWT tokens. **Must be overridden in production.** |
| `ARGUS_JWT_ALGORITHM` | `HS256` | JWT signing algorithm. |
| `ARGUS_JWT_ISSUER` | `argus-api` | Token issuer claim (`iss`). |
| `ARGUS_JWT_AUDIENCE` | `argus-clients` | Token audience claim (`aud`). |
| `ARGUS_JWT_ACCESS_TOKEN_MINUTES` | `60` | Token validity duration in minutes. |

---

## 3. JWT Claims Structure

A valid ARGUS JWT payload contains the following required claims:

```json
{
  "sub": "user-101",
  "role": "PROCUREMENT_OFFICER",
  "name": "Rajesh Kumar",
  "email": "rajesh.kumar@gov.in",
  "iss": "argus-api",
  "aud": "argus-clients",
  "iat": 1772841600,
  "exp": 1772845200
}
```

- `sub` (string, required): Unique user identifier (`user_id`).
- `role` (string, required): One of `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR`.
- `name` (string, optional): Human-readable officer name for attribution.
- `email` (string, optional): User email address.
- `iss` (string, required): Must equal `settings.ARGUS_JWT_ISSUER`.
- `aud` (string, required): Must equal `settings.ARGUS_JWT_AUDIENCE`.
- `exp` (integer, required): Expiration timestamp (UNIX epoch).

---

## 4. Roles & Permissions Matrix (RBAC)

ARGUS defines four procurement-focused roles:

1. **`ADMIN`**: Platform administrator with full access to all system actions and audit logs.
2. **`PROCUREMENT_OFFICER`**: Authorized procurement official who can create/process tenders, register bidders, trigger verification workflows, and record human qualification decisions.
3. **`REVIEWER`**: Technical or domain reviewer with read-only access to tenders, bidders, compliance runs, reports, and evidence traces. Cannot record decisions or mutate records.
4. **`AUDITOR`**: Compliance auditor with read-only access to procurement records, reports, evidence traces, and system audit event logs. Cannot mutate procurement state.

### Endpoint Authorization Matrix

| Endpoint | Method | Allowed Roles |
| :--- | :--- | :--- |
| `/health`, `/health/integrations` | `GET` | Public (No token required) |
| `/api/v1/auth/me` | `GET` | All Authenticated Users |
| `/api/v1/tenders` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/tenders/{id}` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/tenders/{id}/requirements` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/tenders` | `POST` | `ADMIN`, `PROCUREMENT_OFFICER` |
| `/api/v1/tenders/{id}/process` | `POST` | `ADMIN`, `PROCUREMENT_OFFICER` |
| `/api/v1/tenders/{id}/bidders` | `POST` | `ADMIN`, `PROCUREMENT_OFFICER` |
| `/api/v1/bidders/{id}` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/bidders/{id}/compliance` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/bidders/{id}/runs` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/bidders/{id}/runs/{run_id}` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/bidders/{id}/report` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/evaluations/{id}/evidence` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/bidders/{id}/verify` | `POST` | `ADMIN`, `PROCUREMENT_OFFICER` |
| `/api/v1/bidders/{id}/decision` | `POST` | `ADMIN`, `PROCUREMENT_OFFICER` |
| `/api/v1/jobs/{id}` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/jobs/{id}/events` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |
| `/api/v1/audit/events` | `GET` | `ADMIN`, `PROCUREMENT_OFFICER`, `AUDITOR` |
| `/api/v1/rag/query` | `POST` | `ADMIN`, `PROCUREMENT_OFFICER`, `REVIEWER`, `AUDITOR` |

---

## 5. Identity Attribution & Anti-Spoofing Protections

### Officer Identity Derivation
When recording human procurement decisions (`POST /api/v1/bidders/{id}/decision`):
- `officer_id` is set strictly to `principal.user_id` from the decoded JWT token.
- `officer_name` is set to `principal.name` (or fallback `principal.user_id`).

### Anti-Spoofing Payload Strictness
The `HumanDecisionCreate` schema defines `model_config = ConfigDict(extra="forbid")`. Any request payload containing `officer_id` or `officer_name` fields is immediately rejected with `422 Unprocessable Entity`.

### Audit Trail Attribution
Every state mutation logs an `AuditEvent` with:
- `actor_id`: Set to `principal.user_id`.
- `actor_role`: Set to `principal.role.value`.

Every `ComplianceRun` records:
- `triggered_by`: Set to `principal.user_id`.

---

## 6. Local Development & Testing Utilities

### Generating Development Tokens

A CLI helper script is provided at `services/api/scripts/generate_dev_token.py` to generate signed development tokens for testing:

```bash
# Generate token for a Procurement Officer
python -m scripts.generate_dev_token --role PROCUREMENT_OFFICER --user-id PO-4021 --name "Rajesh Kumar"

# Generate token for an Auditor
python -m scripts.generate_dev_token --role AUDITOR --user-id AUD-101 --name "Audit Team"

# Generate token for an Administrator
python -m scripts.generate_dev_token --role ADMIN --user-id ADMIN-001 --name "System Admin"
```

### Using Tokens with cURL / Postman

```bash
export TOKEN="<GENERATED_JWT_TOKEN>"

curl -X GET http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"
```
