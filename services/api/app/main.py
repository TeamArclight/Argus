from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
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

# Register CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

    return IntegrationsHealthResponse(
        gst=check_service("gst"),
        udyam=check_service("udyam"),
        mca=check_service("mca"),
        epfo=check_service("epfo"),
        esic=check_service("esic"),
        blacklist=check_service("blacklist"),
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
