from contextlib import asynccontextmanager
import os
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.api.v1.audit import router as audit_router
from app.api.v1.auth import router as auth_router
from app.api.v1.bidders import router as bidders_router
from app.api.v1.documents import router as documents_router
from app.api.v1.evaluations import router as evaluations_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.providers import router as providers_router
from app.api.v1.rag import router as rag_router
from app.api.v1.tenders import router as tenders_router
from app.auth.dependencies import require_roles
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics_collector
from app.core.middleware import RequestCorrelationMiddleware, get_request_id
from app.db.session import SessionLocal
from app.schemas.canonical import AuthenticatedPrincipal, IntegrationServiceStatus, IntegrationsHealthResponse, UserRole, VerificationMode

logger = get_logger("argus.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ARGUS API backend initializing...")
    yield
    logger.info("ARGUS API backend shutting down...")


app = FastAPI(
    title="ARGUS API",
    description="AI-Powered Integrated Bid Compliance Verification Platform for GeM Procurement",
    version="1.0.0",
    lifespan=lifespan,
)

# Register Request Correlation Middleware first
app.add_middleware(RequestCorrelationMiddleware)

# Register CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Metrics tracking middleware
@app.middleware("http")
async def track_metrics_middleware(request: Request, call_next):
    response = await call_next(request)
    metrics_collector.record_request(response.status_code)
    return response


# Register API v1 Routers under /api/v1 prefix
app.include_router(auth_router, prefix="/api/v1")
app.include_router(tenders_router, prefix="/api/v1")
app.include_router(bidders_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(evaluations_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(rag_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
app.include_router(providers_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict[str, str]:
    # Liveness probe. Says the process is up and nothing more.
    # Deliberately touches no dependency: an orchestrator uses this to decide
    # whether to restart the container, which a database outage must not trigger.
    # Deployment readiness is /health/readiness.
    return {"status": "ok", "service": "argus-api"}
#: One representative table per core domain. Their presence is what distinguishes
#: a usable database from a connected-but-empty one.
_SCHEMA_PROBE_TABLES = ("tenders", "bidders", "processing_jobs")




@app.get("/health/readiness")
def health_readiness() -> JSONResponse:
    """Readiness probe checking database connectivity and storage availability."""
    # Connectivity alone is not readiness. Before this check a database with no
    # schema — the state a deploy is left in when migrations never run — answered
    # SELECT 1 and reported ready, so the container went healthy and every real
    # request then failed (audit finding C-5).
    db_connected = False
    schema_present = False
    storage_writable = False
    schema_revision: str | None = None
    req_id = get_request_id()

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db_connected = True

            missing_tables = []
            for table in _SCHEMA_PROBE_TABLES:
                try:
                    db.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
                except Exception:
                    missing_tables.append(table)
            schema_present = not missing_tables
            if missing_tables:
                logger.error(
                    "Readiness schema probe failed; missing tables: %s. "
                    "Run 'alembic upgrade head' before serving traffic.",
                    ", ".join(missing_tables),
                )

            # Informational only: reports the applied Alembic revision when the
            # schema is migration-managed. Not a readiness gate, so a database
            # provisioned by other means still reports ready when it is usable.
            try:
                revision = db.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
                schema_revision = str(revision) if revision else None
            except Exception:
                schema_revision = None
    except Exception as e:
        logger.error(f"Readiness DB probe failed: {e}")

    try:
        storage_path = settings.ARGUS_STORAGE_LOCAL_PATH
        os.makedirs(storage_path, exist_ok=True)
        test_file = os.path.join(storage_path, ".health_check_test")
        with open(test_file, "w") as f:
            f.write("ready")
        if os.path.exists(test_file):
            os.remove(test_file)
            storage_writable = True
    except Exception as e:
        logger.error(f"Readiness storage probe failed: {e}")

    is_ready = db_connected and schema_present and storage_writable
    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        headers={"X-Request-ID": req_id},
        content={
            "status": "ready" if is_ready else "degraded",
            "request_id": req_id,
            "components": {
                "database": "connected" if db_connected else "disconnected",
                "schema": "present" if schema_present else "missing",
                "storage": "writable" if storage_writable else "unwritable",
            },
            "schema_revision": schema_revision,
        },
    )


@app.get("/health/integrations", response_model=IntegrationsHealthResponse)
def health_integrations() -> IntegrationsHealthResponse:
    """Returns operational status and active modes for external verification registries and intelligence services."""

    supported_modes: dict[str, set[VerificationMode]] = {
        "gst": {VerificationMode.LIVE, VerificationMode.PORTAL_CACHED, VerificationMode.DEMO},
        "udyam": {VerificationMode.LIVE, VerificationMode.PORTAL_CACHED, VerificationMode.DEMO},
        "mca": {VerificationMode.LIVE, VerificationMode.PORTAL_CACHED, VerificationMode.DEMO},
        "blacklist": {VerificationMode.LIVE, VerificationMode.PORTAL_CACHED, VerificationMode.DEMO},
        "epfo": {VerificationMode.LIVE, VerificationMode.PORTAL_CACHED, VerificationMode.DOCUMENT, VerificationMode.DEMO},
        "esic": {VerificationMode.LIVE, VerificationMode.PORTAL_CACHED, VerificationMode.DOCUMENT, VerificationMode.DEMO},
    }

    def check_service(domain: str) -> IntegrationServiceStatus:
        mode = settings.get_mode_for_domain(domain)
        domain_key = domain.lower()
        valid_modes = supported_modes.get(domain_key, set())

        if mode not in valid_modes:
            return IntegrationServiceStatus(
                mode=mode,
                configured=False,
                details=f"{mode.value} mode is unsupported for {domain.upper()}.",
            )

        api_url = getattr(settings, f"{domain.upper()}_API_URL", None) or getattr(settings, f"{domain.upper()}_API_BASE_URL", None)
        api_key = getattr(settings, f"{domain.upper()}_API_KEY", None)

        if mode == VerificationMode.LIVE:
            configured = bool(api_url and api_key)
            details = f"Live authorized {domain.upper()} API gateway configured." if configured else f"Live {domain.upper()} provider unconfigured; missing API URL or credentials."
        elif mode in (VerificationMode.PORTAL_CACHED, VerificationMode.DOCUMENT):
            configured = True
            details = f"Active {mode.value} provider configured."
        else:  # DEMO
            configured = True
            details = "Deterministic SIH demo provider active."
        return IntegrationServiceStatus(mode=mode, configured=configured, details=details)

    intel_caps = []
    if settings.ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL:
        intel_caps.append("tender extraction")
    if settings.ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL:
        intel_caps.append("document extraction")
    if settings.ARGUS_INTELLIGENCE_RAG_URL:
        intel_caps.append("RAG")

    if intel_caps:
        intel_configured = True
        intel_details = f"Configured intelligence capabilities: {', '.join(intel_caps)}."
    else:
        intel_configured = False
        intel_details = "ARGUS intelligence endpoints are unconfigured."

    return IntegrationsHealthResponse(
        gst=check_service("gst"),
        udyam=check_service("udyam"),
        mca=check_service("mca"),
        epfo=check_service("epfo"),
        esic=check_service("esic"),
        blacklist=check_service("blacklist"),
        intelligence=IntegrationServiceStatus(
            mode=VerificationMode.LIVE,
            configured=intel_configured,
            details=intel_details,
        ),
    )


@app.get("/api/v1/metrics")
def get_metrics(
    principal: AuthenticatedPrincipal = Depends(require_roles(UserRole.ADMIN)),
) -> dict:
    """Returns operational metrics (protected: ADMIN role required)."""
    return metrics_collector.get_metrics()


# Global Machine-Readable Error Handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    req_id = getattr(request.state, "request_id", get_request_id())
    if isinstance(exc.detail, dict):
        code = exc.detail.get("code", f"HTTP_{exc.status_code}")
        message = exc.detail.get("message", str(exc.detail))
        details = exc.detail.get("details", {})
    else:
        code = f"HTTP_{exc.status_code}"
        message = str(exc.detail)
        details = {}

    return JSONResponse(
        status_code=exc.status_code,
        headers={"X-Request-ID": req_id},
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": req_id,
                "details": details,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    req_id = getattr(request.state, "request_id", get_request_id())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        headers={"X-Request-ID": req_id},
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request payload or path parameter format.",
                "request_id": req_id,
                "details": {"errors": exc.errors()},
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", get_request_id())
    logger.error(f"Unhandled server error on path {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        headers={"X-Request-ID": req_id},
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An internal server error occurred.",
                "request_id": req_id,
                "details": {},
            }
        },
    )
