from datetime import datetime, timezone
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit.logger import AuditLogger
from app.auth.dependencies import get_current_principal, require_roles
from app.core.config import settings
from app.core.demo_fixtures import (
    DEMO_BIDDERS,
    DEMO_DOCUMENTS,
    DEMO_FIXTURE_VERSION,
    DEMO_RAG_CHUNKS,
    DEMO_REQUIREMENTS,
    DEMO_TENDERS,
)
from app.db.session import get_db
from app.models.domain import (
    AuditEvent,
    Bidder,
    ComplianceRun,
    Document,
    Evidence,
    ExtractedFact,
    HumanDecision,
    JobEvent,
    ProcessingJob,
    RiskSignal,
    RuleEvaluation,
    Tender,
    TenderRequirement,
    VerificationResult,
)
from app.schemas.canonical import (
    AuthenticatedPrincipal,
    DemoStatusRead,
    DocumentType,
    JobRead,
    JobStage,
    JobStatus,
    OperatorEnum,
    RequirementType,
    UserRole,
)
import hashlib
import logging
from pathlib import Path
from app.services.bid_verification_service import BidVerificationService
from app.storage.factory import get_storage_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["Demo Management"])


def _find_demo_pdf_root() -> Path | None:
    """Locates data/demo/pdf root directory across repo environments and containers."""
    current = Path(__file__).resolve()
    for p in [current] + list(current.parents):
        for sub in (
            "data/demo/pdf",
            "demo_data/pdf",
            "services/api/demo_data/pdf",
            "services/api/data/demo/pdf",
        ):
            cand = p / sub
            if cand.exists() and cand.is_dir() and (cand / "tender").exists():
                return cand
    for fallback in ("/app/demo_data/pdf", "/app/data/demo/pdf", "/data/demo/pdf"):
        cand = Path(fallback)
        if cand.exists() and cand.is_dir():
            return cand
    return None


def _check_demo_seed_permission(principal: AuthenticatedPrincipal):
    """Validates that ALLOW_DEMO_SEED is enabled AND requires ADMIN role or explicit demo/evaluation claim."""
    allow = settings.ALLOW_DEMO_SEED or settings.APP_ENV.lower() in ("development", "test")
    if not allow:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo seeding and reset operations are disabled in this environment (ALLOW_DEMO_SEED=false).",
        )
    is_authorized = (
        principal.role == UserRole.ADMIN
        or principal.is_demo_operator
        or principal.evaluation_mode
    )
    if not is_authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo seeding and reset operations require ADMIN role or explicit demo operator permissions (is_demo_operator=true or evaluation_mode=true claim). Normal procurement officers cannot reset or seed the demo scenario.",
        )



@router.get("/status", response_model=DemoStatusRead)

def get_demo_status(db: Session = Depends(get_db)):
    """Returns availability and fixture version status for the synthetic demo scenario."""
    enabled = bool(settings.ALLOW_DEMO_SEED or settings.APP_ENV.lower() in ("development", "test"))

    # Check if canonical tender exists in database
    flagship_tender = (
        db.query(Tender)
        .filter(Tender.id == "tender_gem_2026_01")
        .first()
    )

    if not flagship_tender:
        # Fallback check by tender_number
        flagship_tender = (
            db.query(Tender)
            .filter(Tender.tender_number == "GEM/2026/B/4521089")
            .first()
        )

    if not flagship_tender:
        return DemoStatusRead(
            enabled=enabled,
            seeded=False,
            fixture_version=None,
            expected_fixture_version=DEMO_FIXTURE_VERSION,
            healthy=False,
            message="Demo scenario not seeded. Call POST /api/v1/demo/seed to initialize.",
        )

    meta = flagship_tender.metadata_json or {}
    seeded_version = meta.get("fixture_version")

    # Verify at least one compliance run has been executed
    eval_count = (
        db.query(RuleEvaluation)
        .join(Bidder, RuleEvaluation.bidder_id == Bidder.id)
        .filter(Bidder.tender_id == flagship_tender.id)
        .count()
    )

    # Determine RAG readiness from the most recent RAG_INGEST audit event
    rag_audit = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.action == "RAG_INGEST",
            AuditEvent.entity_id == "doc_tender_gem_2026_01",
        )
        .order_by(AuditEvent.timestamp.desc())
        .first()
    )
    rag_ready = False
    rag_chunks_indexed = 0
    rag_backend: str | None = None
    last_seed_error: str | None = None

    if rag_audit and rag_audit.payload_json:
        ingest_status = rag_audit.payload_json.get("status", "")
        rag_chunks_indexed = int(rag_audit.payload_json.get("chunks_indexed", 0))
        rag_ready = ingest_status == "COMPLETED" and rag_chunks_indexed > 0
        rag_backend = "pgvector" if rag_ready else None
        if not rag_ready:
            last_seed_error = f"RAG ingest status: {ingest_status}"

    is_healthy = bool(
        seeded_version == DEMO_FIXTURE_VERSION
        and eval_count > 0
        and rag_ready
    )

    if not is_healthy and seeded_version != DEMO_FIXTURE_VERSION:
        msg = f"Demo fixture version mismatch (found '{seeded_version}', expected '{DEMO_FIXTURE_VERSION}'). Reset recommended."
    elif not is_healthy and not rag_ready:
        msg = "Demo RAG indexing incomplete or failed. pgvector retrieval unavailable. Re-seed recommended."
    elif not is_healthy:
        msg = "Demo data exists but compliance evaluations are missing. Call POST /api/v1/demo/seed to evaluate."
    else:
        msg = "Demo scenario is ready."

    return DemoStatusRead(
        enabled=enabled,
        seeded=True,
        fixture_version=seeded_version,
        expected_fixture_version=DEMO_FIXTURE_VERSION,
        healthy=is_healthy,
        message=msg,
        rag_ready=rag_ready,
        rag_chunks_indexed=rag_chunks_indexed,
        rag_backend=rag_backend,
        last_seed_error=last_seed_error,
    )


async def _execute_demo_seed(
    db: Session,
    actor_id: str,
    actor_role: str,
    job: ProcessingJob,
) -> ProcessingJob:
    """Executes the complete real ARGUS backend demo pipeline deterministically."""
    now = datetime.now(timezone.utc)

    # Stage 1: Ingestion & Tender Creation
    job.current_stage = JobStage.EXTRACTION
    job.progress = 15
    db.commit()

    tender_id_map: dict[str, str] = {}
    for t_data in DEMO_TENDERS:
        t_id = t_data["id"]
        existing = db.query(Tender).filter((Tender.id == t_id) | (Tender.tender_number == t_data["tender_number"])).first()
        if not existing:
            t = Tender(
                id=t_id,
                tender_number=t_data["tender_number"],
                title=t_data["title"],
                category=t_data.get("category"),
                authority=t_data.get("authority"),
                budget=t_data.get("budget"),
                deadline=datetime.fromisoformat(t_data["deadline"].replace("Z", "+00:00")) if t_data.get("deadline") else None,
                status=JobStatus.COMPLETED,
                raw_document_uri=t_data.get("raw_document_uri"),
                metadata_json=t_data.get("metadata_json", {}),
            )
            db.add(t)
            db.commit()
            tender_id_map[t_id] = t.id
        else:
            existing.title = t_data["title"]
            existing.authority = t_data.get("authority")
            existing.budget = t_data.get("budget")
            existing.metadata_json = t_data.get("metadata_json", {})
            existing.raw_document_uri = t_data.get("raw_document_uri")
            db.commit()
            tender_id_map[t_id] = existing.id

    # Stage 2: Tender Requirements - Real Extraction Pipeline for Flagship Tender
    job.progress = 30
    db.commit()

    storage = get_storage_provider()
    pdf_root = _find_demo_pdf_root()

    # Ensure intelligence is in sys.path
    import sys
    for p in [Path(__file__).resolve()] + list(Path(__file__).resolve().parents):
        intel_cand = p / "services" / "intelligence"
        if intel_cand.exists() and intel_cand.is_dir():
            if str(intel_cand) not in sys.path:
                sys.path.insert(0, str(intel_cand))
            break
        intel_cand_direct = p / "intelligence"
        if intel_cand_direct.exists() and (intel_cand_direct / "argus_ai").exists():
            if str(intel_cand_direct) not in sys.path:
                sys.path.insert(0, str(intel_cand_direct))
            break

    flagship_tender_id = tender_id_map.get("tender_gem_2026_01", "tender_gem_2026_01")
    flagship_pdf = pdf_root / "tender" / "tender_gem_2026_B_4521089.pdf" if pdf_root else None

    # 1. Real extraction for Flagship Demo Tender from physical PDF
    if flagship_pdf and flagship_pdf.exists():
        t_bytes = flagship_pdf.read_bytes()
        t_sha = hashlib.sha256(t_bytes).hexdigest()
        t_key = f"tenders/{flagship_tender_id}/tender_gem_2026_B_4521089.pdf"
        if not storage.file_exists(t_key):
            t_storage_uri = storage.store_file(t_bytes, t_key)
        else:
            t_storage_uri = t_key.replace("\\", "/").lstrip("/")

        from argus_ai.extraction.service import extract_tender
        extracted_tender_reqs = extract_tender(flagship_pdf)

        for draft in extracted_tender_reqs:
            req_id = f"req_{flagship_tender_id}_{draft.clause.replace('.', '_')}_{draft.requirement_type.value.lower()}"
            existing_req = (
                db.query(TenderRequirement)
                .filter(
                    (TenderRequirement.id == req_id)
                    | ((TenderRequirement.tender_id == flagship_tender_id) & (TenderRequirement.clause == draft.clause))
                )
                .first()
            )
            req_meta = {
                "seed_source": "ACTUAL_TENDER_PARSER",
                "parser": "argus_ai.extraction.service.extract_tender",
                "fixture_version": DEMO_FIXTURE_VERSION,
            }
            if draft.requirement_type.value == "TURNOVER":
                req_meta["currency"] = "INR"

            is_canonical_approved = draft.clause in ("2.1", "2.2", "2.3", "2.4", "2.5") or draft.field in {
                "tax.gstin",
                "registration.udyam",
                "financial.average_annual_turnover",
                "experience.years",
                "legal.blacklisted",
            }
            if not existing_req:
                req = TenderRequirement(
                    id=req_id,
                    tender_id=flagship_tender_id,
                    clause=draft.clause,
                    requirement_type=RequirementType(draft.requirement_type.value),
                    field=draft.field,
                    operator=OperatorEnum(draft.operator.value),
                    expected_value=draft.expected_value,
                    unit=draft.unit or ("INR" if draft.requirement_type.value == "TURNOVER" else None),
                    mandatory=draft.mandatory,
                    source_page=draft.source_page,
                    source_text=draft.source_text,
                    confidence=draft.confidence,
                    requires_verification=draft.requires_verification,
                    is_approved=is_canonical_approved,
                    metadata_json=req_meta,
                )
                db.add(req)
            else:
                existing_req.expected_value = draft.expected_value
                existing_req.operator = OperatorEnum(draft.operator.value)
                existing_req.source_page = draft.source_page
                existing_req.source_text = draft.source_text
                existing_req.confidence = draft.confidence
                existing_req.unit = draft.unit or ("INR" if draft.requirement_type.value == "TURNOVER" else None)
                existing_req.is_approved = is_canonical_approved
                existing_req.metadata_json = req_meta
        db.commit()

    # 2. Secondary synthetic background tenders (e.g. tender_gem_2026_02, tender_gem_2026_03)
    for t_key, req_list in DEMO_REQUIREMENTS.items():
        if t_key == "tender_gem_2026_01":
            # Flagship requirements were produced strictly by real extraction above; 0 directly seeded
            continue
        actual_tender_id = tender_id_map.get(t_key, t_key)
        for r_data in req_list:
            req_id = r_data["id"]
            existing_req = (
                db.query(TenderRequirement)
                .filter(
                    (TenderRequirement.id == req_id)
                    | ((TenderRequirement.tender_id == actual_tender_id) & (TenderRequirement.clause == r_data["clause"]))
                )
                .first()
            )
            if not existing_req:
                req = TenderRequirement(
                    id=req_id,
                    tender_id=actual_tender_id,
                    clause=r_data["clause"],
                    requirement_type=RequirementType(r_data["requirement_type"]),
                    field=r_data["field"],
                    operator=OperatorEnum(r_data["operator"]),
                    expected_value=r_data["expected_value"],
                    unit=r_data.get("unit"),
                    mandatory=r_data.get("mandatory", True),
                    source_page=r_data.get("source_page"),
                    source_text=r_data.get("source_text"),
                    confidence=r_data.get("confidence", 1.0),
                    requires_verification=r_data.get("requires_verification", True),
                    is_approved=True,
                    metadata_json=r_data.get("metadata_json", {}),
                )
                db.add(req)
            else:
                existing_req.expected_value = r_data["expected_value"]
                existing_req.operator = OperatorEnum(r_data["operator"])
                existing_req.is_approved = True
                existing_req.metadata_json = r_data.get("metadata_json", {})
    db.commit()

    # Stage 3: Bidders & Document Ingestion
    job.progress = 45
    db.commit()

    bidder_id_map: dict[str, str] = {}
    for t_key, bidder_list in DEMO_BIDDERS.items():
        actual_tender_id = tender_id_map.get(t_key, t_key)
        for b_data in bidder_list:
            b_id = b_data["id"]
            existing_bidder = db.query(Bidder).filter((Bidder.id == b_id) | ((Bidder.tender_id == actual_tender_id) & (Bidder.bidder_name == b_data["bidder_name"]))).first()
            if not existing_bidder:
                b = Bidder(
                    id=b_id,
                    tender_id=actual_tender_id,
                    bidder_name=b_data["bidder_name"],
                    gstin=b_data.get("gstin"),
                    udyam_number=b_data.get("udyam_number"),
                    pan=b_data.get("pan"),
                    cin=b_data.get("cin"),
                    metadata_json=b_data.get("metadata_json", {}),
                )
                db.add(b)
                db.commit()
                bidder_id_map[b_id] = b.id
            else:
                existing_bidder.gstin = b_data.get("gstin")
                existing_bidder.udyam_number = b_data.get("udyam_number")
                existing_bidder.metadata_json = b_data.get("metadata_json", {})
                db.commit()
                bidder_id_map[b_id] = existing_bidder.id

    # Seed Physical Storage, Documents, and Extracted Facts
    storage = get_storage_provider()
    pdf_root = _find_demo_pdf_root()

    # Ingest Tender Documents into physical storage & DB for ALL demo tenders
    DEMO_TENDER_DOCS = [
        {
            "tender_id": "tender_gem_2026_01",
            "doc_id": "doc_tender_gem_2026_01",
            "filename": "tender_gem_2026_B_4521089.pdf",
            "rel_path": "tender/tender_gem_2026_B_4521089.pdf",
        },
        {
            "tender_id": "tender_gem_2026_02",
            "doc_id": "doc_tender_gem_2026_02",
            "filename": "meity_cloud_cluster_rfp.pdf",
            "rel_path": "tender/meity_cloud_cluster_rfp.pdf",
        },
        {
            "tender_id": "tender_gem_2026_03",
            "doc_id": "doc_tender_gem_2026_03",
            "filename": "seci_solar_grid_rfp.pdf",
            "rel_path": "tender/seci_solar_grid_rfp.pdf",
        },
    ]

    for t_spec in DEMO_TENDER_DOCS:
        t_id = tender_id_map.get(t_spec["tender_id"], t_spec["tender_id"])
        tender_doc_id = t_spec["doc_id"]
        fname = t_spec["filename"]
        if pdf_root:
            tender_pdf = pdf_root / t_spec["rel_path"]
            if tender_pdf.exists():
                t_bytes = tender_pdf.read_bytes()
                t_sha = hashlib.sha256(t_bytes).hexdigest()
                t_key = f"tenders/{t_id}/{fname}"
                if storage.file_exists(t_key):
                    storage.delete_file(t_key)
                t_storage_uri = storage.store_file(t_bytes, t_key)
                existing_tdoc = db.query(Document).filter(
                    (Document.id == tender_doc_id) | ((Document.tender_id == t_id) & (Document.filename == fname))
                ).first()
                if not existing_tdoc:
                    tdoc = Document(
                        id=tender_doc_id,
                        tender_id=t_id,
                        bidder_id=None,
                        filename=fname,
                        storage_uri=t_storage_uri,
                        sha256=t_sha,
                        document_type=DocumentType.TENDER,
                        content_type="application/pdf",
                        size_bytes=len(t_bytes),
                    )
                    db.add(tdoc)
                else:
                    existing_tdoc.id = tender_doc_id
                    existing_tdoc.storage_uri = t_storage_uri
                    existing_tdoc.sha256 = t_sha
                    existing_tdoc.size_bytes = len(t_bytes)
                    existing_tdoc.filename = fname

                t_obj = db.query(Tender).filter(Tender.id == t_id).first()
                if t_obj:
                    t_obj.raw_document_uri = t_storage_uri
                    if t_obj.metadata_json and isinstance(t_obj.metadata_json, dict):
                        att = t_obj.metadata_json.get("attached_file")
                        if isinstance(att, dict):
                            att["filename"] = fname
                            att["size_bytes"] = len(t_bytes)
                            att["content_type"] = "application/pdf"

                db.commit()
                AuditLogger.log(
                    db,
                    action="DOCUMENT_UPLOADED",
                    entity_type="DOCUMENT",
                    entity_id=tender_doc_id,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    payload={
                        "filename": fname,
                        "sha256": t_sha,
                        "storage_uri": t_storage_uri,
                        "tender_id": t_id,
                    },
                )

    # Ingest Bidder Documents and Extracted Facts
    import sys
    for p in [Path(__file__).resolve()] + list(Path(__file__).resolve().parents):
        intel_cand = p / "services" / "intelligence"
        if intel_cand.exists() and intel_cand.is_dir():
            if str(intel_cand) not in sys.path:
                sys.path.insert(0, str(intel_cand))
            break
        intel_cand_direct = p / "intelligence"
        if intel_cand_direct.exists() and (intel_cand_direct / "argus_ai").exists():
            if str(intel_cand_direct) not in sys.path:
                sys.path.insert(0, str(intel_cand_direct))
            break

    for b_key, doc_list in DEMO_DOCUMENTS.items():
        actual_bidder_id = bidder_id_map.get(b_key, b_key)
        bidder_obj = db.query(Bidder).filter(Bidder.id == actual_bidder_id).first()
        if not bidder_obj:
            continue

        folder_name = b_key.rsplit("_", 1)[0] if "_" in b_key else b_key
        bidder_dir = pdf_root / folder_name if pdf_root else None

        for d_data in doc_list:
            doc_id = d_data["id"]
            pdf_file = bidder_dir / d_data["filename"] if bidder_dir else None

            stored_uri = d_data["storage_uri"]
            doc_sha = d_data["sha256"]
            doc_size = d_data.get("size_bytes", 1024)

            if pdf_file and pdf_file.exists():
                pdf_bytes = pdf_file.read_bytes()
                doc_sha = hashlib.sha256(pdf_bytes).hexdigest()
                doc_size = len(pdf_bytes)
                key = f"bidders/{actual_bidder_id}/{d_data['filename']}"
                if storage.file_exists(key):
                    storage.delete_file(key)
                stored_uri = storage.store_file(pdf_bytes, key)

            existing_doc = db.query(Document).filter(Document.id == doc_id).first()
            if not existing_doc:
                doc = Document(
                    id=doc_id,
                    bidder_id=actual_bidder_id,
                    tender_id=None,
                    filename=d_data["filename"],
                    storage_uri=stored_uri,
                    sha256=doc_sha,
                    document_type=DocumentType(d_data["document_type"]),
                    content_type=d_data.get("content_type", "application/pdf"),
                    size_bytes=doc_size,
                )
                db.add(doc)
                db.commit()
            else:
                existing_doc.storage_uri = stored_uri
                existing_doc.sha256 = doc_sha
                existing_doc.size_bytes = doc_size
                db.commit()
                doc = existing_doc

            AuditLogger.log(
                db,
                action="DOCUMENT_UPLOADED",
                entity_type="DOCUMENT",
                entity_id=doc.id,
                actor_id=actor_id,
                actor_role=actor_role,
                payload={
                    "filename": doc.filename,
                    "sha256": doc.sha256,
                    "storage_uri": doc.storage_uri,
                    "bidder_id": actual_bidder_id,
                },
            )

            # Real document parser extraction
            extracted_fields: set[str] = set()
            if pdf_file and pdf_file.exists():
                try:
                    from argus_ai.extraction.service import extract_document
                    parsed_facts = extract_document(pdf_file, document_id=doc.id, bidder_id=actual_bidder_id)
                    for pf in parsed_facts:
                        extracted_fields.add(pf.field)
                        ex_fact = (
                            db.query(ExtractedFact)
                            .filter(
                                ExtractedFact.bidder_id == actual_bidder_id,
                                ExtractedFact.document_id == doc.id,
                                ExtractedFact.field == pf.field,
                            )
                            .first()
                        )
                        fact_meta = {
                            "seed_source": "ACTUAL_PARSER",
                            "parser": "pypdf_regex",
                            "fixture_version": DEMO_FIXTURE_VERSION,
                        }
                        if pf.field == "financial.average_annual_turnover":
                            fact_meta["currency"] = "INR"
                            fact_meta["unit"] = "INR"
                        if not ex_fact:
                            fact_obj = ExtractedFact(
                                document_id=doc.id,
                                bidder_id=actual_bidder_id,
                                field=pf.field,
                                value=pf.value,
                                source_page=pf.source_page or 1,
                                source_text=pf.source_text,
                                confidence=pf.confidence,
                                metadata_json=fact_meta,
                            )
                            db.add(fact_obj)
                            AuditLogger.log(
                                db,
                                action="FACT_EXTRACTED",
                                entity_type="FACT",
                                entity_id=f"{doc.id}_{pf.field}",
                                actor_id=actor_id,
                                actor_role=actor_role,
                                payload={"field": pf.field, "value": str(pf.value), "provenance": "ACTUAL_PARSER"},
                            )
                except Exception as parse_exc:
                    logger.warning(f"Parser extraction error for {d_data['filename']}: {parse_exc}")

            # Persist any complementary fixture facts not captured by regex
            for f_data in d_data.get("facts", []):
                if f_data["field"] in extracted_fields:
                    continue
                existing_fact = (
                    db.query(ExtractedFact)
                    .filter(
                        ExtractedFact.bidder_id == actual_bidder_id,
                        ExtractedFact.document_id == doc.id,
                        ExtractedFact.field == f_data["field"],
                    )
                    .first()
                )
                if not existing_fact:
                    fact_meta = {"seed_source": "SYNTHETIC_DEMO", "fixture_version": DEMO_FIXTURE_VERSION}
                    if f_data.get("metadata_json"):
                        fact_meta.update(f_data["metadata_json"])
                    fact = ExtractedFact(
                        document_id=doc.id,
                        bidder_id=actual_bidder_id,
                        field=f_data["field"],
                        value=f_data["value"],
                        source_page=f_data.get("source_page", 1),
                        source_text=f_data.get("source_text"),
                        confidence=f_data.get("confidence", 1.0),
                        metadata_json=fact_meta,
                    )
                    db.add(fact)
                    AuditLogger.log(
                        db,
                        action="FACT_EXTRACTED",
                        entity_type="FACT",
                        entity_id=f"{doc.id}_{f_data['field']}",
                        actor_id=actor_id,
                        actor_role=actor_role,
                        payload={"field": f_data["field"], "value": str(f_data["value"]), "provenance": "SYNTHETIC_DEMO"},
                    )
    db.commit()

    # Stage 4: Real Compliance & Verification Execution
    job.current_stage = JobStage.COMPLIANCE
    job.progress = 65
    db.commit()

    verification_service = BidVerificationService(db)
    flagship_bidders = ["bidder_alpha_01", "bidder_bharat_03", "bidder_crest_02"]

    for b_id in flagship_bidders:
        actual_id = bidder_id_map.get(b_id, b_id)
        bidder = db.query(Bidder).filter(Bidder.id == actual_id).first()
        if not bidder:
            continue

        try:
            await verification_service.run_verification_workflow(
                bidder_id=actual_id,
                triggered_by=actor_id,
                actor_id=actor_id,
                actor_role=actor_role,
            )
        except Exception as err:
            # Log error but continue so partial failures do not halt overall scenario seed
            print(f"Warning: Demo verification workflow for bidder {actual_id} emitted error: {err}")

    # Stage 4.5: RAG Ingest — index all demo tenders into pgvector for authentic retrieval
    job.progress = 80
    db.commit()

    try:
        from app.services.rag_adapter import RAGServiceAdapter
        from app.schemas.canonical import RAGQueryRequest
        rag = RAGServiceAdapter()

        demo_rag_targets = [
            ("doc_tender_gem_2026_01", "tender_gem_2026_01", "tender_gem_2026_B_4521089.pdf", "tender/tender_gem_2026_B_4521089.pdf", "GEM/2026/B/4521089 — IT Infrastructure Tender"),
            ("doc_tender_gem_2026_02", "tender_gem_2026_02", "meity_cloud_cluster_rfp.pdf", "tender/meity_cloud_cluster_rfp.pdf", "MeitY Cloud Cluster RFP"),
            ("doc_tender_gem_2026_03", "tender_gem_2026_03", "seci_solar_grid_rfp.pdf", "tender/seci_solar_grid_rfp.pdf", "SECI Smart Solar Grid Micro-Inverter Deployment (Phase IV)"),
        ]

        for doc_id, t_key, fname, rel_path, title in demo_rag_targets:
            t_id = tender_id_map.get(t_key, t_key)
            t_storage_key = f"tenders/{t_id}/{fname}"
            pdf_bytes = None
            if pdf_root:
                pdf_file = pdf_root / rel_path
                if pdf_file.exists():
                    pdf_bytes = pdf_file.read_bytes()
            if not pdf_bytes and storage.file_exists(t_storage_key):
                try:
                    pdf_bytes = storage.read_file(t_storage_key)
                except Exception:
                    pass

            rag_chunks_indexed = 0
            rag_ingest_status = "NOT_ATTEMPTED"
            try:
                ingest_result = await rag.ingest_document(
                    document_id=doc_id,
                    title=title,
                    document_uri=t_storage_key,
                    document_type="TENDER",
                    tender_id=t_id,
                    clause=None,
                    security_level="INTERNAL",
                    file_bytes=pdf_bytes,
                )
                if ingest_result.get("success") and ingest_result.get("chunks_indexed", 0) > 0:
                    rag_chunks_indexed = ingest_result["chunks_indexed"]
                    rag_ingest_status = "COMPLETED"
                else:
                    existing_check = await rag.retrieve(RAGQueryRequest(query="EMD", tender_id=t_id, top_k=5))
                    if existing_check and existing_check.results and any(doc_id in (r.entity_id or "") for r in existing_check.results):
                        rag_chunks_indexed = len(existing_check.results)
                        rag_ingest_status = "COMPLETED"
                    else:
                        rag_chunks_indexed = 0
                        rag_ingest_status = f"FAILED:{ingest_result.get('error_code', 'UNKNOWN')}"
            except Exception as rag_err:
                rag_ingest_status = f"ERROR:{rag_err}"
                logger.warning(f"Demo RAG ingest failed for {t_id}: {rag_err}")

            AuditLogger.log(
                db,
                action="RAG_INGEST",
                entity_type="DOCUMENT",
                entity_id=doc_id,
                actor_id=actor_id,
                actor_role=actor_role,
                payload={
                    "tender_id": t_id,
                    "storage_key": t_storage_key,
                    "chunks_indexed": rag_chunks_indexed,
                    "status": rag_ingest_status,
                },
            )
        db.commit()
    except Exception as rag_exc:
        logger.warning(f"Demo RAG batch ingest failed: {rag_exc}")

    # Stage 5: Finalization & Audit
    job.current_stage = JobStage.REPORTING
    job.progress = 100
    job.status = JobStatus.COMPLETED
    job.completed_at = datetime.now(timezone.utc)
    db.commit()

    AuditLogger.log(
        db,
        action="DEMO_SEEDED",
        entity_type="DEMO",
        entity_id="demo_scenario",
        actor_id=actor_id,
        actor_role=actor_role,
        payload={
            "fixture_version": DEMO_FIXTURE_VERSION,
            "tenders_count": len(DEMO_TENDERS),
            "bidders_count": len(flagship_bidders),
            "rag_ingest_status": rag_ingest_status,
            "rag_chunks_indexed": rag_chunks_indexed,
            "timestamp": now.isoformat(),
        },
    )

    return job


@router.post("/seed", response_model=JobRead, status_code=status.HTTP_200_OK)
async def seed_demo(
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Deterministically seeds the canonical ARGUS demo scenario and runs the real backend pipeline."""
    _check_demo_seed_permission(principal)

    # Create tracked processing job
    job = ProcessingJob(
        target_type="DEMO",
        target_id="demo_scenario",
        job_type="DEMO_SEED",
        status=JobStatus.RUNNING,
        current_stage=JobStage.EXTRACTION,
        progress=10,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        completed_job = await _execute_demo_seed(
            db=db,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            job=job,
        )
        return completed_job
    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise


@router.post("/reset", response_model=JobRead, status_code=status.HTTP_200_OK)
async def reset_demo(
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Safely resets ONLY demo-isolated entities without touching authentic data, then re-seeds."""
    _check_demo_seed_permission(principal)

    # 1. Identify demo tenders strictly by demo IDs or metadata
    demo_tender_ids = [t["id"] for t in DEMO_TENDERS]
    demo_tender_numbers = [t["tender_number"] for t in DEMO_TENDERS]

    demo_tenders = (
        db.query(Tender)
        .filter(
            (Tender.id.in_(demo_tender_ids))
            | (Tender.tender_number.in_(demo_tender_numbers))
        )
        .all()
    )

    t_ids_to_clean = [t.id for t in demo_tenders]

    if t_ids_to_clean:
        # Cascade-delete related demo entities in strict FK leaf-to-root order.
        # bulk .delete(synchronize_session=False) bypasses ORM cascade, so each
        # child table must be handled explicitly before its parent.

        demo_bidders = db.query(Bidder).filter(Bidder.tender_id.in_(t_ids_to_clean)).all()
        b_ids = [b.id for b in demo_bidders]

        # --- Collect all demo ProcessingJob IDs (bidder + tender + DEMO target_type) ---
        demo_job_ids = [
            row.id for row in db.query(ProcessingJob.id).filter(
                (ProcessingJob.target_id.in_(t_ids_to_clean)) |
                (ProcessingJob.target_id.in_(b_ids) if b_ids else False) |
                (ProcessingJob.target_type == "DEMO")
            ).all()
        ]

        # --- Leaf tables — strict FK leaf-to-root order ---
        # Constraint: fk_evidence_verification_result_id_verification_results
        #   evidence.verification_result_id → verification_results.id
        # Evidence children of VerificationResult must be deleted BEFORE their parent.
        # Not all such Evidence rows are captured by bidder_id; query via VR IDs.
        if b_ids:
            db.query(RuleEvaluation).filter(RuleEvaluation.bidder_id.in_(b_ids)).delete(synchronize_session=False)

            # Collect VR IDs that belong to demo bidders, then delete their Evidence children first
            vr_ids = [
                row.id for row in
                db.query(VerificationResult.id).filter(VerificationResult.bidder_id.in_(b_ids)).all()
            ]
            if vr_ids:
                # Evidence rows keyed by verification_result_id (may have NULL bidder_id)
                db.query(Evidence).filter(Evidence.verification_result_id.in_(vr_ids)).delete(synchronize_session=False)

            # Now safe to delete VerificationResult (all Evidence children by VR ID gone)
            db.query(VerificationResult).filter(VerificationResult.bidder_id.in_(b_ids)).delete(synchronize_session=False)

            db.query(RiskSignal).filter(RiskSignal.bidder_id.in_(b_ids)).delete(synchronize_session=False)
            # Delete any remaining Evidence rows keyed directly by bidder_id
            db.query(Evidence).filter(Evidence.bidder_id.in_(b_ids)).delete(synchronize_session=False)
            db.query(HumanDecision).filter(HumanDecision.bidder_id.in_(b_ids)).delete(synchronize_session=False)
            db.query(ExtractedFact).filter(ExtractedFact.bidder_id.in_(b_ids)).delete(synchronize_session=False)

        # JobEvent.job_id FK -> processing_jobs.id — must delete before ProcessingJob
        if demo_job_ids:
            db.query(JobEvent).filter(JobEvent.job_id.in_(demo_job_ids)).delete(synchronize_session=False)

        if b_ids:
            # ComplianceRun.job_id FK -> processing_jobs.id — delete after JobEvent, before ProcessingJob
            db.query(ComplianceRun).filter(ComplianceRun.bidder_id.in_(b_ids)).delete(synchronize_session=False)
            db.query(Document).filter(Document.bidder_id.in_(b_ids)).delete(synchronize_session=False)
            db.query(Bidder).filter(Bidder.id.in_(b_ids)).delete(synchronize_session=False)

        # Tender-level Documents (bidder_id=None, tender_id in demo set)
        db.query(TenderRequirement).filter(TenderRequirement.tender_id.in_(t_ids_to_clean)).update({TenderRequirement.document_id: None}, synchronize_session=False)
        tender_doc_ids = [
            row.id for row in
            db.query(Document.id).filter(Document.tender_id.in_(t_ids_to_clean)).all()
        ]
        if tender_doc_ids:
            db.query(ExtractedFact).filter(ExtractedFact.document_id.in_(tender_doc_ids)).delete(synchronize_session=False)
            db.query(Evidence).filter(Evidence.document_id.in_(tender_doc_ids)).delete(synchronize_session=False)
            db.query(Document).filter(Document.id.in_(tender_doc_ids)).delete(synchronize_session=False)

        # Evidence rows with direct tender_id FK
        db.query(Evidence).filter(Evidence.tender_id.in_(t_ids_to_clean)).delete(synchronize_session=False)

        # ProcessingJob — now safe to delete (ComplianceRun and JobEvent children gone)
        if demo_job_ids:
            db.query(ProcessingJob).filter(ProcessingJob.id.in_(demo_job_ids)).delete(synchronize_session=False)

        db.query(TenderRequirement).filter(TenderRequirement.tender_id.in_(t_ids_to_clean)).delete(synchronize_session=False)
        db.query(Tender).filter(Tender.id.in_(t_ids_to_clean)).delete(synchronize_session=False)
        db.commit()

    AuditLogger.log(
        db,
        action="DEMO_RESET",
        entity_type="DEMO",
        entity_id="demo_scenario",
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        payload={
            "action": "CLEAN_DEMO_ENTITIES",
            "cleaned_tenders": t_ids_to_clean,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    # 2. Re-seed clean canonical scenario
    job = ProcessingJob(
        target_type="DEMO",
        target_id="demo_scenario",
        job_type="DEMO_SEED",
        status=JobStatus.RUNNING,
        current_stage=JobStage.EXTRACTION,
        progress=10,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    completed_job = await _execute_demo_seed(
        db=db,
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        job=job,
    )
    return completed_job
