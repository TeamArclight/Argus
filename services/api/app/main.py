from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.api.v1.bidders import router as bidders_router
from app.api.v1.evaluations import router as evaluations_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.rag import router as rag_router
from app.api.v1.tenders import router as tenders_router
from app.db.session import Base, engine


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
