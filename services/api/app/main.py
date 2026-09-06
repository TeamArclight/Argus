from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.api.v1.bidders import router as bidders_router
from app.api.v1.evaluations import router as evaluations_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.rag import router as rag_router
from app.api.v1.tenders import router as tenders_router
from app.core.config import settings
from app.db.session import Base, engine
from app.schemas.canonical import IntegrationServiceStatus, IntegrationsHealthResponse, VerificationMode


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database schema tables exist on application startup
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="ARGUS API",
    description="AI-Powered Integrated Bid Compliance Verification Platform for GeM Procurement",
    version="1.0.0",
    lifespan=lifespan,
)

# Register API v1 Routers under /api/v1 prefix
app.include_router(tenders_router, prefix="/api/v1")
app.include_router(bidders_router, prefix="/api/v1")
app.include_router(evaluations_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(rag_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "argus-api"}


@app.get("/health/integrations", response_model=IntegrationsHealthResponse)
def health_integrations() -> IntegrationsHealthResponse:
    """Returns operational status and active modes for external verification registries and intelligence services."""

    def check_service(domain: str, base_url: str | None, key: str | None) -> IntegrationServiceStatus:
        mode = settings.get_mode_for_domain(domain)
        if mode == VerificationMode.LIVE:
            configured = bool(base_url and key)
            details = "Live authorized API gateway configured." if configured else "Live provider unconfigured; missing API base URL or credentials."
        elif mode in (VerificationMode.PORTAL_CACHED, VerificationMode.DOCUMENT):
            configured = True
            details = f"Active {mode.value} provider configured."
        else:  # DEMO
            configured = True
            details = "Deterministic SIH demo provider active."
        return IntegrationServiceStatus(mode=mode, configured=configured, details=details)

    return IntegrationsHealthResponse(
        gst=check_service("gst", settings.GST_API_BASE_URL, settings.GST_API_KEY),
        udyam=check_service("udyam", settings.UDYAM_API_BASE_URL, settings.UDYAM_API_KEY),
        mca=check_service("mca", settings.MCA_API_BASE_URL, settings.MCA_API_KEY),
        epfo=check_service("epfo", settings.EPFO_API_BASE_URL, settings.EPFO_API_KEY),
        esic=check_service("esic", settings.ESIC_API_BASE_URL, settings.ESIC_API_KEY),
        blacklist=check_service("blacklist", settings.BLACKLIST_API_BASE_URL, settings.BLACKLIST_API_KEY),
        intelligence=IntegrationServiceStatus(
            mode=VerificationMode.LIVE,
            configured=bool(settings.ARGUS_INTELLIGENCE_BASE_URL and settings.ARGUS_INTELLIGENCE_API_KEY),
            details="Intelligence gateway connected." if (settings.ARGUS_INTELLIGENCE_BASE_URL and settings.ARGUS_INTELLIGENCE_API_KEY) else "ARGUS intelligence service unconfigured.",
        ),
    )


# Global Machine-Readable Error Handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": str(exc.detail),
                "details": {},
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request payload or path parameter format.",
                "details": {"errors": exc.errors()},
            }
        },
    )
