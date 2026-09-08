"""Standalone intelligence HTTP boundary consumed by services/api adapters."""
from __future__ import annotations

from datetime import date, datetime, timezone
from contextlib import asynccontextmanager
from typing import Any, Optional

import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .contracts import (DocumentClassification, DocumentExtractionResponse,
                        EvidenceChunk, RiskSignal, TenderExtractionResponse)
from .extraction.service import classify_document, extract_document, extract_tender
from .model_gateway.gateway import configured_gateway
from .rag.service import InMemoryRAG
from .rag.pgvector import PgVectorRAG
from .rag.ingestion import ingest_document
from .readiness import readiness
from .risk.service import detect_risk
from .storage import DocumentResolutionError, resolved_document

# ---------------------------------------------------------------------------
# Confidence threshold: facts below this trigger review_required in response.
# ---------------------------------------------------------------------------
_LOW_CONFIDENCE_THRESHOLD = float(os.getenv("ARGUS_LOW_CONFIDENCE_THRESHOLD", "0.6"))


_MAX_DOCUMENT_BYTES = int(os.getenv("ARGUS_MAX_DOCUMENT_BYTES", str(30 * 1024 * 1024)))
_MAX_B64_LEN = int(_MAX_DOCUMENT_BYTES * 4 / 3) + 1024


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

import base64
import hashlib
import re
import tempfile
from pathlib import Path

class TenderExtractRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    contract_version: Optional[str] = None
    request_id: Optional[str] = None
    tender_id: str
    document_id: Optional[str] = None
    document_uri: Optional[str] = None
    document_sha256: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    file_bytes_base64: Optional[str] = None

class DocumentExtractRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    contract_version: Optional[str] = None
    request_id: Optional[str] = None
    document_id: str
    bidder_id: str
    document_uri: Optional[str] = None
    document_type: Optional[str] = None
    document_sha256: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    file_bytes_base64: Optional[str] = None

class ClassifyDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    document_uri: str

class RAGQueryRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    query: str
    tender_id: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1, le=50)

class RAGIngestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    document_id: str
    title: str
    document_uri: str
    document_type: str
    source_uri: Optional[str] = None
    version: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    security_level: str = "INTERNAL"
    tender_id: Optional[str] = None
    clause: Optional[str] = None

class RAGDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    document_id: str
    tender_id: Optional[str] = None
    tenant_id: Optional[str] = None

class RiskDetectRequest(BaseModel):
    """Risk detection request — all fields optional; caller supplies what's available."""
    model_config = ConfigDict(extra="ignore")
    document_hash: Optional[str] = None
    prior_hashes: list[str] = Field(default_factory=list)
    claim_value: Optional[Any] = None
    verified_value: Optional[Any] = None
    claimed_entity_name: Optional[str] = None
    verified_entity_name: Optional[str] = None
    identifiers: Optional[dict[str, list[str]]] = None
    expiry_date: Optional[str] = None
    document_text: Optional[str] = None
    prior_document_texts: Optional[dict[str, str]] = None
    oem_authorization_hash: Optional[str] = None
    prior_oem_authorization_hashes: list[str] = Field(default_factory=list)
    facts_by_field: Optional[dict[str, list[list[Any]]]] = None
    evidence_ids: list[str] = Field(default_factory=list)


class EvaluateBidRequest(BaseModel):
    """Full workflow evaluation request."""
    model_config = ConfigDict(extra="ignore")
    tender_document_uri: str
    bidder_document_uri: str
    document_id: str
    bidder_id: str
    rag_query: Optional[str] = None
    thread_id: Optional[str] = None

class ResumeWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    thread_id: str
    decision: Any


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(rag: Optional[Any] = None, checkpointer: Optional[Any] = None) -> FastAPI:
    if rag is None:
        database_url = os.getenv("ARGUS_RAG_DATABASE_URL")
        live_rag_required = os.getenv("ARGUS_REQUIRE_LIVE_RAG", "false").lower() in {"true", "1", "yes"} or os.getenv("APP_ENV") == "production"
        if database_url:
            rag = PgVectorRAG(database_url)
        elif live_rag_required:
            raise RuntimeError("Live RAG is required but ARGUS_RAG_DATABASE_URL is not configured")
        else:
            rag = InMemoryRAG()
    store = rag

    def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
        expected = os.getenv("ARGUS_INTELLIGENCE_API_KEY")
        require_auth_flag = os.getenv("ARGUS_REQUIRE_AUTH", "false").lower() in {"true", "1", "yes"}
        is_production = os.getenv("APP_ENV", "development").lower() == "production"
        if expected:
            if not authorization or not hmac.compare_digest(authorization, f"Bearer {expected}"):
                raise HTTPException(status_code=401, detail="Invalid intelligence service credentials")
        elif require_auth_flag or is_production:
            raise HTTPException(status_code=401, detail="Intelligence service authentication is required but ARGUS_INTELLIGENCE_API_KEY is not configured")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if isinstance(store, PgVectorRAG): store.ensure_schema()
        yield
    app = FastAPI(title="ARGUS Intelligence Service", lifespan=lifespan)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/health")
    def health(_: None = Depends(require_auth)) -> dict[str, Any]:
        return readiness()

    # ------------------------------------------------------------------
    # Tender extraction
    # ------------------------------------------------------------------

    @app.post("/extract-tender")
    def extract_tender_endpoint(payload: TenderExtractRequest, _: None = Depends(require_auth)) -> Any:
        gw = configured_gateway()
        computed_sha = None
        temp_file = None
        try:
            if payload.file_bytes_base64:
                if len(payload.file_bytes_base64) > _MAX_B64_LEN:
                    raise HTTPException(413, "Base64 document payload exceeds maximum allowed size")
                try:
                    file_bytes = base64.b64decode(payload.file_bytes_base64)
                except Exception as b64_err:
                    raise HTTPException(422, f"Invalid base64 document bytes: {b64_err}")
                if len(file_bytes) > _MAX_DOCUMENT_BYTES:
                    raise HTTPException(413, "Document size exceeds maximum allowed size")
                computed_sha = hashlib.sha256(file_bytes).hexdigest()
                if payload.document_sha256 and computed_sha.lower() != payload.document_sha256.lower():
                    raise HTTPException(422, "Document SHA-256 digest mismatch")
                suffix = Path(payload.filename or "tender.pdf").suffix or ".bin"
                with tempfile.NamedTemporaryFile(prefix="argus-tender-", suffix=suffix, delete=False) as handle:
                    temp_file = Path(handle.name)
                    handle.write(file_bytes)
                requirements = extract_tender(temp_file, gw)
            elif payload.document_uri:
                with resolved_document(payload.document_uri) as path:
                    requirements = extract_tender(path, gw)
            else:
                raise HTTPException(422, "Either file_bytes_base64 or document_uri is required")

            if payload.request_id or payload.contract_version:
                return {
                    "contract_version": payload.contract_version or "1.0",
                    "request_id": payload.request_id or "",
                    "document_id": payload.document_id or "",
                    "document_sha256": payload.document_sha256 or computed_sha or "",
                    "status": "COMPLETED",
                    "requirements": [
                        {
                            "clause": req.clause,
                            "requirement_type": req.requirement_type.value if hasattr(req.requirement_type, "value") else str(req.requirement_type),
                            "field": req.field,
                            "operator": req.operator.value if hasattr(req.operator, "value") else str(req.operator),
                            "expected_value": req.expected_value,
                            "unit": req.unit,
                            "mandatory": req.mandatory,
                            "source_page": req.source_page,
                            "source_text": req.source_text,
                            "confidence": req.confidence,
                            "requires_verification": req.requires_verification,
                            "is_approved": False,
                            "metadata_json": {
                                "source_page": req.source_page,
                                "source_text": req.source_text,
                                "confidence": req.confidence,
                            },
                        }
                        for req in requirements
                    ],
                    "provider_model": gw.model_name,
                }
            return TenderExtractionResponse(requirements=requirements)
        except (ValueError, DocumentResolutionError) as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Document extraction with low-confidence flagging
    # ------------------------------------------------------------------

    @app.post("/extract-document")
    def extract_document_endpoint(payload: DocumentExtractRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        gw = configured_gateway()
        computed_sha = None
        temp_file = None
        try:
            if payload.file_bytes_base64:
                if len(payload.file_bytes_base64) > _MAX_B64_LEN:
                    raise HTTPException(413, "Base64 document payload exceeds maximum allowed size")
                try:
                    file_bytes = base64.b64decode(payload.file_bytes_base64)
                except Exception as b64_err:
                    raise HTTPException(422, f"Invalid base64 document bytes: {b64_err}")
                if len(file_bytes) > _MAX_DOCUMENT_BYTES:
                    raise HTTPException(413, "Document size exceeds maximum allowed size")
                computed_sha = hashlib.sha256(file_bytes).hexdigest()
                if payload.document_sha256 and computed_sha.lower() != payload.document_sha256.lower():
                    raise HTTPException(422, "Document SHA-256 digest mismatch")
                suffix = Path(payload.filename or "doc.pdf").suffix or ".bin"
                with tempfile.NamedTemporaryFile(prefix="argus-doc-", suffix=suffix, delete=False) as handle:
                    temp_file = Path(handle.name)
                    handle.write(file_bytes)
                facts = extract_document(temp_file, document_id=payload.document_id, bidder_id=payload.bidder_id, gateway=gw)
            elif payload.document_uri:
                with resolved_document(payload.document_uri) as path:
                    facts = extract_document(path, document_id=payload.document_id, bidder_id=payload.bidder_id, gateway=gw)
            else:
                raise HTTPException(422, "Either file_bytes_base64 or document_uri is required")

            if payload.request_id or payload.contract_version:
                return {
                    "contract_version": payload.contract_version or "1.0",
                    "request_id": payload.request_id or "",
                    "document_id": payload.document_id,
                    "bidder_id": payload.bidder_id,
                    "document_sha256": payload.document_sha256 or computed_sha or "",
                    "status": "COMPLETED",
                    "facts": [
                        {
                            "field": fact.field,
                            "value": fact.value,
                            "source_page": fact.source_page,
                            "source_text": fact.source_text,
                            "confidence": fact.confidence,
                            "metadata_json": {
                                "source_page": fact.source_page,
                                "source_text": fact.source_text,
                                "confidence": fact.confidence,
                                "provider": fact.provider,
                                "model": fact.model,
                                "bounding_box": fact.bounding_box,
                            },
                        }
                        for fact in facts
                    ],
                    "provider_model": gw.model_name,
                }

            api_facts = [{k: v for k, v in fact.model_dump(mode="json").items() if k in {"field", "value", "source_page", "source_text", "confidence"}} for fact in facts]
            low_confidence_fields = [fact.field for fact in facts if fact.confidence < _LOW_CONFIDENCE_THRESHOLD]
            return {
                "facts": api_facts,
                "review_required": bool(low_confidence_fields),
                "low_confidence_fields": low_confidence_fields,
            }
        except (ValueError, DocumentResolutionError) as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Document classification
    # ------------------------------------------------------------------

    @app.post("/classify-document")
    def classify_document_endpoint(payload: ClassifyDocumentRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        try:
            with resolved_document(payload.document_uri) as path:
                classification = classify_document(path)
            return classification.model_dump(mode="json")
        except (ValueError, DocumentResolutionError) as exc: raise HTTPException(422, str(exc)) from exc

    # ------------------------------------------------------------------
    # Risk detection
    # ------------------------------------------------------------------

    @app.post("/detect-risk")
    def detect_risk_endpoint(payload: RiskDetectRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        expiry: Optional[date] = None
        if payload.expiry_date:
            try:
                expiry = date.fromisoformat(payload.expiry_date)
            except ValueError:
                raise HTTPException(422, f"Invalid expiry_date format: {payload.expiry_date}")

        # Convert list-of-lists to list-of-tuples for facts_by_field.
        facts_by_field: Optional[dict[str, list[tuple[str, Any]]]] = None
        if payload.facts_by_field:
            facts_by_field = {
                field: [(str(pair[0]), pair[1]) for pair in pairs if len(pair) >= 2]
                for field, pairs in payload.facts_by_field.items()
            }

        identifiers: Optional[dict[str, set[str]]] = None
        if payload.identifiers:
            identifiers = {k: set(v) for k, v in payload.identifiers.items()}

        signals = detect_risk(
            document_bytes=bytes.fromhex(payload.document_hash) if payload.document_hash else None,
            prior_hashes=set(payload.prior_hashes) if payload.prior_hashes else None,
            claim_value=payload.claim_value,
            verified_value=payload.verified_value,
            claimed_entity_name=payload.claimed_entity_name,
            verified_entity_name=payload.verified_entity_name,
            identifiers=identifiers,
            expiry_date=expiry,
            document_text=payload.document_text,
            prior_document_texts=payload.prior_document_texts,
            oem_authorization_hash=payload.oem_authorization_hash,
            prior_oem_authorization_hashes=set(payload.prior_oem_authorization_hashes) if payload.prior_oem_authorization_hashes else None,
            facts_by_field=facts_by_field,
            evidence_ids=payload.evidence_ids,
        )
        return {
            "signals": [signal.model_dump(mode="json") for signal in signals],
            "signal_count": len(signals),
            "has_high_severity": any(s.severity in {"HIGH", "CRITICAL"} for s in signals),
        }

    # ------------------------------------------------------------------
    # RAG query
    # ------------------------------------------------------------------

    @app.post("/rag-query")
    def rag_query_endpoint(payload: RAGQueryRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        filters = dict(payload.filters)
        if payload.tender_id: filters.setdefault("tender_id", payload.tender_id)
        for key, value in filters.items():
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", str(key)):
                raise HTTPException(422, f"Invalid metadata filter key: {key}")
        results = store.retrieve(payload.query, filters, payload.top_k)
        return {"query": payload.query, "results": [item.model_dump(mode="json", exclude={"content_hash", "version", "effective_from", "effective_to", "security_level"}) for item in results], "retrieved_at": datetime.now(timezone.utc).isoformat(), "error_code": None if results else "INSUFFICIENT_EVIDENCE", "error_message": None if results else "No current evidence matched the query."}

    # ------------------------------------------------------------------
    # RAG ingest
    # ------------------------------------------------------------------

    @app.post("/rag-ingest")
    def rag_ingest_endpoint(payload: RAGIngestRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        try:
            with resolved_document(payload.document_uri) as path:
                chunks = ingest_document(path, document_id=payload.document_id, title=payload.title, document_type=payload.document_type, indexer=store, source_uri=payload.source_uri, version=payload.version, effective_from=payload.effective_from, effective_to=payload.effective_to, security_level=payload.security_level, tender_id=payload.tender_id, clause=payload.clause)
            return {"document_id": payload.document_id, "chunks_indexed": len(chunks), "chunks": [chunk.model_dump(mode="json") for chunk in chunks]}
        except (ValueError, DocumentResolutionError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/rag-delete")
    def rag_delete_endpoint(payload: RAGDeleteRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        if not payload.document_id or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", payload.document_id):
            raise HTTPException(422, "Invalid or missing document_id for deletion")
        delete = getattr(store, "delete", None)
        if delete is None:
            raise HTTPException(501, "Configured RAG store does not support document deletion")
        scope: dict[str, Any] = {}
        if payload.tender_id:
            scope["tender_id"] = payload.tender_id
        if payload.tenant_id:
            scope["tenant_id"] = payload.tenant_id
        try:
            chunks_deleted = delete(payload.document_id, scope=scope if scope else None)
        except TypeError:
            chunks_deleted = delete(payload.document_id)
        return {"document_id": payload.document_id, "chunks_deleted": chunks_deleted}

    # ------------------------------------------------------------------
    # Full workflow evaluation (LangGraph orchestration)
    # ------------------------------------------------------------------

    @app.post("/evaluate-bid")
    def evaluate_bid_endpoint(payload: EvaluateBidRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        """Execute the full intelligence pipeline: extract → retrieve → risk → compliance → report.

        The compliance_tool defaults to REVIEW_REQUIRED, which correctly
        triggers the human-in-the-loop interrupt. The API layer should
        call this endpoint and then persist the returned state.
        """
        is_production = os.getenv("APP_ENV", "development").lower() == "production"
        demo_enabled = os.getenv("ARGUS_WORKFLOW_DEMO_ENABLED", "false").lower() in {"true", "1", "yes"}
        if is_production and not demo_enabled:
            raise HTTPException(403, "LangGraph workflow endpoints are disabled in production; backend owns compliance evaluation and governance.")

        from .agents.workflow import build_argus_workflow

        try:
            workflow = build_argus_workflow(rag=store, checkpointer=checkpointer)
            state = {
                "tender": {"document_uri": payload.tender_document_uri},
                "document": {
                    "document_uri": payload.bidder_document_uri,
                    "document_id": payload.document_id,
                    "bidder_id": payload.bidder_id,
                },
                "rag_query": {"query": payload.rag_query or "eligibility requirements compliance"},
            }
            invoke_config = {"configurable": {"thread_id": payload.thread_id}} if payload.thread_id and checkpointer else None
            result = workflow.invoke(state, config=invoke_config)
            # Strip internal LangGraph keys the API doesn't need.
            return {
                "requirements": result.get("requirements", []),
                "facts": result.get("facts", []),
                "evidence": result.get("evidence", []),
                "risks": result.get("risks", []),
                "compliance_evaluation": result.get("compliance_evaluation", {}),
                "review_reasons": result.get("review_reasons", []),
                "report": result.get("report"),
                "interrupted": "__interrupt__" in result,
            }
        except Exception as exc:
            raise HTTPException(500, f"Workflow execution failed: {exc}") from exc

    @app.post("/evaluate-bid/resume")
    def resume_evaluate_bid_endpoint(payload: ResumeWorkflowRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        """Resume an interrupted workflow using the application's injected checkpointer."""
        is_production = os.getenv("APP_ENV", "development").lower() == "production"
        demo_enabled = os.getenv("ARGUS_WORKFLOW_DEMO_ENABLED", "false").lower() in {"true", "1", "yes"}
        if is_production and not demo_enabled:
            raise HTTPException(403, "LangGraph workflow endpoints are disabled in production; backend owns compliance evaluation and governance.")

        if checkpointer is None:
            raise HTTPException(503, "Workflow resume requires a configured durable checkpointer")
        from .agents.workflow import build_argus_workflow
        from langgraph.types import Command
        try:
            workflow = build_argus_workflow(rag=store, checkpointer=checkpointer)
            result = workflow.invoke(Command(resume=payload.decision), config={"configurable": {"thread_id": payload.thread_id}})
            return {
                "requirements": result.get("requirements", []),
                "facts": result.get("facts", []),
                "evidence": result.get("evidence", []),
                "risks": result.get("risks", []),
                "compliance_evaluation": result.get("compliance_evaluation", {}),
                "review_reasons": result.get("review_reasons", []),
                "report": result.get("report"),
                "interrupted": "__interrupt__" in result,
            }
        except Exception as exc:
            raise HTTPException(500, f"Workflow resume failed: {exc}") from exc

    return app

app = create_app()

