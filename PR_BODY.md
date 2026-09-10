## Summary

Scoped remediation of the seven confirmed pre-SIH blockers from the independent audit. No architectural refactoring — the components the audit identified as sound are untouched.

Base: `main` @ `d4ddc0d` (unchanged since the audit; this branch is a clean fast-forward).

## Audit findings addressed

| FindingStatusWhat changed                                          |             |                                                                                                                                                                       |
| ------------------------------------------------------------------ | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **C-1 / L-11** Web container ran a dev server exposing dev auth    | FIXED       | 3-stage Dockerfile (`npm ci` → `npm run build` → `npm run start`) running as uid 10001. Dev-token route defaults **closed**.                                          |
| **C-3 / C-4** Intelligence open by default; storage roots optional | FIXED       | `require_auth` fails closed; startup refuses in non-local env with no key; `ARGUS_ALLOWED_STORAGE_ROOTS` mandatory; port rebound to loopback.                         |
| **C-5** No migrations at deploy; health green on unusable DB       | FIXED       | One-shot `migrate` service gating api/worker; readiness probes core tables.                                                                                           |
| **H-8** Wrong URLs for remote/container deployment                 | FIXED       | `NEXT_PUBLIC_API_BASE_URL` as build arg; internal `http://intelligence:8100` incl. the previously-unset RAG URL.                                                      |
| **H-12 / H-13 / H-2** Demo path broken end to end                  | FIXED       | Seed rebuilt on current models; 12 text-layer demo PDFs; `REVIEW_REQUIRED` terminal on both sides.                                                                    |
| **H-14** Unpinned intelligence dependencies                        | **PARTIAL** | `requirements.in` + `freeze-requirements.sh` delivered. Lock **not** generated — PyPI was unreachable in the authoring environment. One manual step required (below). |
| **C-6** Low-confidence AI facts could produce a definitive PASS    | FIXED       | Signals propagated end to end; new engine precedence step returns `REVIEW_REQUIRED` / `LOW_CONFIDENCE_EVIDENCE`.                                                      |

### H-14 — the one manual step

```
cd services/intelligence && ./scripts/freeze-requirements.sh && git commit -am "chore: pin intelligence dependencies"
```

Version pins were deliberately **not** guessed. The highest risk is `langgraph>=0.2,<2`, whose range spans the 0.x→1.x transition that changes `interrupt` and checkpointer construction — both called directly by `argus_ai/agents/workflow.py`.

## Files changed (40)

**Frontend (5)** — `dev-token/route.ts` (rewritten, fails closed), `TopNav.tsx` / `SessionRequired.tsx` (published password + prefill + ADMIN option removed), `useJobStream.ts` (terminal set), `JobProgressDrawer.tsx` (terminal review state).

**Backend (7)** — `compliance/engine.py` (+87, additive precedence step), `compliance/reason_codes.py` (+1 code), `schemas/canonical.py` (terminal constants, envelope fields), `services/ai_adapter.py` (signal propagation), `api/v1/bidders.py` (review escalation + audit), `api/v1/jobs.py` (terminal + 600s stream cap), `main.py` (readiness).

**Intelligence (7)** — `http_service.py` (fail-closed auth, `/livez`, contract-mode signals), `storage.py` (mandatory allowlist), `agents/workflow.py` (`/evaluate-bid` uses guarded resolution), `conftest.py`*,* *`requirements.in`*, `requirements.txt`, `scripts/freeze-requirements.sh`\*.

**Infra (4)** — `compose.yaml`, `api.Dockerfile`, `web.Dockerfile`, `migrate-entrypoint.sh`\*.

**Demo / scripts / tests (17)** — `seed_demo.py`, `generate_demo_pdfs.py`*, 12 PDFs under* *`data/demo/pdf/`*, `test_fact_confidence_gate.py`*,* *`test_service_hardening.py`*, `.env.example`.

`*` = new file.

## Security impact

**Closed:** anonymous ADMIN JWT minting via the password published in the repo (it was in three places); dev auth reachable in any container deploy; unauthenticated access to all 10 intelligence routes; arbitrary local file read/exfiltration via `/rag-ingest` + `/rag-query`; intelligence exposed on `0.0.0.0`; the `/evaluate-bid` resolution bypass; LLM output alone yielding a definitive PASS.

**Still dev-only:** `/api/auth/dev-token` exists but is closed by default, never serves in production, and never mints ADMIN without a second opt-in.

**Not fixed (out of scope):** C-2 — the API's JWT signing secret still has to live in the Next.js env for the dev route to sign. Proper issuance belongs in the API behind a real user store.

**Deployment config still required:** generate `ARGUS_INTELLIGENCE_API_KEY`; set `NEXT_PUBLIC_API_BASE_URL` and rebuild the web image; TLS / reverse proxy is still absent (M-1, deferred).

**Secret scan:** 40 committed files against 10 secret patterns and 13 forbidden path patterns — clean. `.env`, `apps/web/.env.local`, `services/api/.env`, `data/uploads/`, `*.db` confirmed still gitignored.

## Behavioural impact

- `POST /api/auth/dev-token` with the old published credential → `404`.
- `POST /rag-ingest {"document_uri":"/app/.env"}` → `401`; `422` even when authenticated.
- `/health/readiness` on a schema-less DB → `503` with `schema: missing` (was `200 ready`).
- `/health` unchanged as the liveness probe.
- A job ending `REVIEW_REQUIRED` now terminates the SSE stream and the UI (previously both spun forever).
- Extraction with low-confidence fields completes as `REVIEW_REQUIRED` rather than `COMPLETED`.

## Deployment impact

- **Docker Compose** — `up` now runs migrate → api/worker. Requires `ARGUS_INTELLIGENCE_API_KEY`.
- **Hostinger VPS** — set `NEXT_PUBLIC_API_BASE_URL` to the public origin and **rebuild the web image** (it is inlined at build time). Intelligence is now loopback-only.
- **Railway** — the `migrate` service maps to a release command: `python -m alembic upgrade head`. Point healthchecks at `/health/readiness`.
- **Vercel** — `NODE_ENV=production` hard-blocks the dev route; leave `ARGUS_ENABLE_DEV_AUTH` unset.
- **PostgreSQL** — no schema change, no new migration, existing history untouched.

## AI governance impact

Confidence now propagates: intelligence computes it → contract response carries `review_required` / `low_confidence_fields` / `confidence_threshold` → adapter stamps `low_confidence` on the fact's metadata → stored on `ExtractedFact` → recorded in the extraction audit event. Evidence citation and provenance are unchanged.

The engine gains **one** new precedence step, placed after the existing conflict/mismatch checks. It fires **only** when an outcome would rest solely on a sub-threshold extracted fact with no corroborating registry verification. It withholds in both directions (no definitive PASS *or* FAIL), and human-attested facts bypass it entirely.

**Human authority is unchanged.** The LLM decides nothing; the engine still makes every determination and the officer still records the qualification decision. `FactRead.confidence` defaults to `1.0`, so any caller not supplying confidence behaves exactly as before — which is why all 57 pre-existing engine tests pass untouched.

## Architecture preserved

Untouched: **risk engine**, **human final-decision authority**, **backend JWT verification**, **AI envelope validation**, **upload validation**, **API path containment**, **idempotency primitives**, **operation-lock primitives**, **compliance run snapshots**, **landing page**, **workspace visual design**, **DB models/schema**, **migration history**, **CI OpenAPI drift gate**, **audit masking**, **SSE sanitization**.

Three need explicit note:

- **ComplianceEngine core** — touched additively only. One new precedence step *between* existing steps, three helpers, four constants. No existing branch, comparison, normalisation or reason code modified. `ENGINE_VERSION` stays `2.0.0` so historical replay is unaffected; the new policy gets `EVIDENCE_CONFIDENCE_POLICY_VERSION`.
- **Deterministic precedence** — order preserved. `CONFLICTING_FACTS` and `CLAIM_VERIFICATION_MISMATCH` still win over the new gate (both explicitly tested).
- **OpenAPI drift gate** — `AIResponseEnvelope` and `AIServiceResult` appear **zero** times in `contracts/openapi.json`; they are internal. `TERMINAL_JOB_STATUSES` is a module constant, not a model. No regeneration expected — **CI's drift check is the authority here**, since the export could not be run locally.

## Tests run (dynamically verified in local environment)

| Check / Test Suite | Result | Details |
| --- | --- | --- |
| `npm ci` (`apps/web`) | **PASSED** | 389 packages installed cleanly, 0 vulnerabilities |
| `npm run lint` (`apps/web`) | **PASSED** | 0 errors, 9 exhaustive-deps warnings |
| `npm run build` (`apps/web`) | **PASSED** | Next.js 16.3.4 (Turbopack) production build completed, TypeScript verified |
| Backend Pytest (`services/api`) | **PASSED** | 271 passed, 7 skipped, 0 failed across all 20 test suites |
| `test_fact_confidence_gate.py` (new) | **PASSED** | 21 passed, 0 failed |
| Intelligence Pytest (`services/intelligence`) | **PASSED** | 64 passed, 0 failed across all 8 test modules |
| `test_service_hardening.py` (new) | **PASSED** | 9 passed, 0 failed |
| Demo PDFs 5-point validation | **PASSED** | 12/12 PDFs generated with valid %PDF header, extractable text layer, parsed, and passed `DocumentValidationService` |
| Alembic migration on disposable DB | **PASSED** | Migrations upgraded cleanly `44d5c5ca3f11 -> 9f5627b30055` |
| OpenAPI export & contract parity | **PASSED** | `python -m app.export_openapi` ran cleanly; health docstrings aligned with contract |
| Secret scan | **PASSED** | Clean across all 41 changed files against sensitive key/credential patterns |

## Tests NOT run

- **Docker image builds** — Docker is not available in the local Windows execution environment.
- **H-14 freeze script** — `services/intelligence/scripts/freeze-requirements.sh` requires Docker to compile Linux Python 3.11 pins via `pip-compile` inside `python:3.11-slim`. Left as PARTIAL per audit scope instructions until run in Linux/Docker environment.

## Remaining known risks

1. H-14 is incomplete until the freeze script is run.
2. The frontend has never been compiled with these changes.
3. Deferred HIGH findings: C-2, H-1 (inline job execution / dead worker), H-3 (`Last-Event-ID` uses `progress` not `seq`), H-4 (`check_job_access` no-op), H-5 (`process-documents` lacks idempotency/lock), H-6 (`runCompliance` misnamed), H-7 (SQLite default, no `pool_pre_ping`), H-9 (no lock TTL/recovery), H-10 (unusable HNSW index), H-11 (client-controlled RAG filters).
4. Deferred MEDIUM: M-1…M-22 — security headers/TLS, `/health/integrations` disclosure, exception leakage, document IDOR, enum storage, TIMESTAMPTZ, indexes, evidence join table, audit hash chain, rate limiting, accessibility, demo watermarking.
5. `reset_demo.py` still uses `drop_all`/`create_all` — out of scope, behind the same guardrails.

**Do not merge until CI is green and a human has reviewed.**