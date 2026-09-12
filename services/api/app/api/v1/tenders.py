from datetime import datetime, timezone
import hashlib
import uuid
from typing import Any
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.audit.logger import AuditLogger
from app.auth.dependencies import get_current_principal, require_roles
from app.db.session import get_db
from app.services.idempotency_service import IdempotencyService
from app.services.operation_lock_service import OperationLockService
from app.models.domain import Document, ProcessingJob, Tender, TenderRequirement
from app.schemas.canonical import (
    AuthenticatedPrincipal,
    DocumentRead,
    DocumentType,
    JobRead,
    JobStage,
    JobStatus,
    TenderCreate,
    TenderRead,
    TenderRequirementCreate,
    TenderRequirementRead,
    UserRole,
)
from app.services.ai_adapter import AIServiceAdapter
from app.services.document_service import DocumentService
from app.services.rule_validator import RuleValidator
from app.storage.factory import get_storage_provider

router = APIRouter(prefix="/tenders", tags=["Tenders"])
ai_adapter = AIServiceAdapter()


@router.post("", response_model=TenderRead, status_code=status.HTTP_201_CREATED)
def create_tender(
    payload: TenderCreate,
    principal: AuthenticatedPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)),
    db: Session = Depends(get_db),
):
    existing = db.query(Tender).filter(Tender.tender_number == payload.tender_number).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tender number {payload.tender_number} already exists.",
        )

    tender = Tender(
        tender_number=payload.tender_number,
        title=payload.title,
        category=payload.category,
        authority=payload.authority,
        budget=payload.budget,
        deadline=payload.deadline,
        raw_document_uri=payload.raw_document_uri,
        metadata_json=payload.metadata_json,
        status=JobStatus.QUEUED,
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)

    AuditLogger.log(
        db,
        action="TENDER_CREATED",
        entity_type="TENDER",
        entity_id=tender.id,
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        payload={"tender_number": tender.tender_number, "title": tender.title},
    )
    return tender


@router.get("", response_model=list[TenderRead])
def list_tenders(
    skip: int = 0,
    limit: int = 20,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    tenders = db.query(Tender).offset(skip).limit(limit).all()
    return tenders


@router.get("/{id}", response_model=TenderRead)
def get_tender(
    id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    tender = db.query(Tender).filter(Tender.id == id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {id} not found.",
        )
    return tender


@router.post("/{id}/process", response_model=JobRead)
async def process_tender(
    id: str,
    idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
    principal: AuthenticatedPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)),
    db: Session = Depends(get_db),
):
    tender = db.query(Tender).filter(Tender.id == id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {id} not found.",
        )

    cached_json, cached_code, record = IdempotencyService.check_or_start(
        db, idempotency_key, principal.user_id, "TENDER", id, "PROCESS_TENDER"
    )
    if cached_json is not None:
        return JSONResponse(status_code=cached_code, content=cached_json)

    try:
        job = ProcessingJob(
            target_type="TENDER",
            target_id=id,
            job_type="EXTRACT_REQUIREMENTS",
            status=JobStatus.RUNNING,
            current_stage=JobStage.EXTRACTION,
            progress=10,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        IdempotencyService.attach_job(db, record, job.id)

        # Acquire resource-level active operation lock
        try:
            OperationLockService.acquire_lock(
                db=db,
                resource_type="TENDER",
                resource_id=id,
                operation="PROCESS_TENDER",
                job_id=job.id,
                principal_id=principal.user_id,
            )
        except HTTPException:
            db.delete(job)
            if record:
                db.delete(record)
            db.commit()
            raise

        AuditLogger.log(
            db,
            action="TENDER_PROCESSING_STARTED",
            entity_type="TENDER",
            entity_id=id,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            payload={"job_id": job.id},
        )

        # Require real persisted Document row with valid SHA-256 digest
        doc = db.query(Document).filter(Document.tender_id == id).order_by(Document.created_at.desc()).first()

        if not doc or not doc.sha256 or not doc.sha256.strip():
            job.status = JobStatus.FAILED
            job.error_message = "Tender processing requires a persisted document record with a valid recorded SHA-256 digest."
            job.progress = 100
            job.completed_at = datetime.now(timezone.utc)
            tender.status = JobStatus.FAILED
            db.commit()

            AuditLogger.log(
                db,
                action="TENDER_EXTRACTION_FAILED",
                entity_type="TENDER",
                entity_id=id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "error_code": "MISSING_TENDER_DOCUMENT"},
            )
            res_payload = JobRead.model_validate(job).model_dump(mode="json")
            IdempotencyService.complete(db, record, status.HTTP_200_OK, res_payload)
            return job

        storage = get_storage_provider()
        if not doc.storage_uri or not storage.file_exists(doc.storage_uri):
            job.status = JobStatus.FAILED
            job.error_message = "Tender document file not found in storage."
            job.progress = 100
            job.completed_at = datetime.now(timezone.utc)
            tender.status = JobStatus.FAILED
            db.commit()

            AuditLogger.log(
                db,
                action="TENDER_EXTRACTION_FAILED",
                entity_type="TENDER",
                entity_id=id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "error_code": "DOCUMENT_FILE_NOT_FOUND"},
            )
            res_payload = JobRead.model_validate(job).model_dump(mode="json")
            IdempotencyService.complete(db, record, status.HTTP_200_OK, res_payload)
            return job

        try:
            file_bytes = storage.read_file(doc.storage_uri)
        except Exception:
            file_bytes = None

        if not file_bytes:
            job.status = JobStatus.FAILED
            job.error_message = "Unreadable or empty tender document file in storage."
            job.progress = 100
            job.completed_at = datetime.now(timezone.utc)
            tender.status = JobStatus.FAILED
            db.commit()

            AuditLogger.log(
                db,
                action="TENDER_EXTRACTION_FAILED",
                entity_type="TENDER",
                entity_id=id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "error_code": "STORAGE_READ_ERROR"},
            )
            res_payload = JobRead.model_validate(job).model_dump(mode="json")
            IdempotencyService.complete(db, record, status.HTTP_200_OK, res_payload)
            return job

        computed_sha256 = hashlib.sha256(file_bytes).hexdigest()
        if computed_sha256 != doc.sha256:
            job.status = JobStatus.FAILED
            job.error_message = "Document SHA-256 hash mismatch."
            job.progress = 100
            job.completed_at = datetime.now(timezone.utc)
            tender.status = JobStatus.FAILED
            db.commit()

            AuditLogger.log(
                db,
                action="TENDER_EXTRACTION_FAILED",
                entity_type="TENDER",
                entity_id=id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "error_code": "DOCUMENT_INTEGRITY_MISMATCH"},
            )
            res_payload = JobRead.model_validate(job).model_dump(mode="json")
            IdempotencyService.complete(db, record, status.HTTP_200_OK, res_payload)
            return job

        req_id = str(uuid.uuid4())
        AuditLogger.log(
            db,
            action="TENDER_EXTRACTION_REQUESTED",
            entity_type="TENDER",
            entity_id=id,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            payload={"job_id": job.id, "request_id": req_id, "document_id": doc.id},
        )

        ai_result = await ai_adapter.extract_tender(
            tender_id=id,
            document_id=doc.id,
            document_sha256=doc.sha256,
            file_bytes=file_bytes,
            document_uri=doc.storage_uri,
            filename=doc.filename,
            content_type=doc.content_type,
            request_id=req_id,
        )

        if not ai_result.success or not ai_result.data:
            job.status = JobStatus.FAILED
            job.error_message = ai_result.message
            job.progress = 100
            job.completed_at = datetime.now(timezone.utc)
            tender.status = JobStatus.FAILED
            db.commit()

            AuditLogger.log(
                db,
                action="TENDER_EXTRACTION_FAILED",
                entity_type="TENDER",
                entity_id=id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "request_id": req_id, "error_code": ai_result.error_code, "message": ai_result.message},
            )
            res_payload = JobRead.model_validate(job).model_dump(mode="json")
            IdempotencyService.complete(db, record, status.HTTP_200_OK, res_payload)
            return job

        # Canonical requirement idempotency: protect approved requirements and prevent duplicates
        existing_reqs = (
            db.query(TenderRequirement)
            .filter(TenderRequirement.tender_id == id)
            .all()
        )

        def _norm_val(v: Any) -> str:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return str(float(v))
            if isinstance(v, str):
                return v.strip().lower()
            return str(v)

        def _clause_key(c: str | None) -> str:
            return (c or "").strip().lower()

        def _semantic_sig(req_type: Any, field_name: str | None, op: Any, exp_val: Any) -> tuple:
            t = req_type.value if hasattr(req_type, "value") else str(req_type)
            o = op.value if hasattr(op, "value") else str(op)
            f = (field_name or "").strip().lower()
            return (t.upper(), f, o.upper(), _norm_val(exp_val))

        # Index existing requirements
        approved_clauses: dict[str, TenderRequirement] = {}
        approved_sigs: dict[tuple, TenderRequirement] = {}
        approved_type_clauses: dict[tuple, TenderRequirement] = {}

        unapproved_clauses: dict[str, TenderRequirement] = {}
        unapproved_sigs: dict[tuple, TenderRequirement] = {}
        unapproved_type_clauses: dict[tuple, TenderRequirement] = {}

        for r in existing_reqs:
            c_k = _clause_key(r.clause)
            sig = _semantic_sig(r.requirement_type, r.field, r.operator, r.expected_value)
            t_str = r.requirement_type.value if hasattr(r.requirement_type, "value") else str(r.requirement_type)
            tc_sig = (c_k, t_str.upper()) if c_k else None

            if r.is_approved:
                if c_k:
                    approved_clauses[c_k] = r
                approved_sigs[sig] = r
                if tc_sig:
                    approved_type_clauses[tc_sig] = r
            else:
                if c_k and c_k not in unapproved_clauses:
                    unapproved_clauses[c_k] = r
                if sig not in unapproved_sigs:
                    unapproved_sigs[sig] = r
                if tc_sig and tc_sig not in unapproved_type_clauses:
                    unapproved_type_clauses[tc_sig] = r

        new_count = 0
        for item in ai_result.data:
            req_obj = TenderRequirementCreate.model_validate(item)
            c_k = _clause_key(req_obj.clause)
            sig = _semantic_sig(req_obj.requirement_type, req_obj.field, req_obj.operator, req_obj.expected_value)
            t_str = req_obj.requirement_type.value if hasattr(req_obj.requirement_type, "value") else str(req_obj.requirement_type)
            tc_sig = (c_k, t_str.upper()) if c_k else None

            # 1. Protect approved requirements!
            # If this requirement or clause has already been approved by an officer, do not duplicate or mutate.
            if (c_k and c_k in approved_clauses) or (sig in approved_sigs) or (tc_sig and tc_sig in approved_type_clauses):
                continue

            # 2. Check if an unapproved candidate already exists (update in-place, do not duplicate)
            existing_unapproved = None
            if tc_sig and tc_sig in unapproved_type_clauses:
                existing_unapproved = unapproved_type_clauses[tc_sig]
            elif sig in unapproved_sigs:
                existing_unapproved = unapproved_sigs[sig]
            elif c_k and c_k in unapproved_clauses:
                existing_unapproved = unapproved_clauses[c_k]

            if existing_unapproved:
                # Refresh unapproved candidate non-destructively
                existing_unapproved.confidence = max(existing_unapproved.confidence or 0.0, req_obj.confidence or 0.0)
                if req_obj.source_text and not existing_unapproved.source_text:
                    existing_unapproved.source_text = req_obj.source_text
                if req_obj.source_page and not existing_unapproved.source_page:
                    existing_unapproved.source_page = req_obj.source_page
                continue

            # 3. New unique candidate requirement
            db_req = TenderRequirement(
                tender_id=id,
                document_id=doc.id,
                clause=req_obj.clause,
                requirement_type=req_obj.requirement_type,
                field=req_obj.field,
                operator=req_obj.operator,
                expected_value=req_obj.expected_value,
                unit=req_obj.unit,
                mandatory=req_obj.mandatory,
                source_page=req_obj.source_page,
                source_text=req_obj.source_text,
                confidence=req_obj.confidence,
                requires_verification=req_obj.requires_verification,
                is_approved=False,
                metadata_json=req_obj.metadata_json or {},
            )
            db.add(db_req)
            if c_k:
                unapproved_clauses[c_k] = db_req
            unapproved_sigs[sig] = db_req
            if tc_sig:
                unapproved_type_clauses[tc_sig] = db_req
            new_count += 1

        job.status = JobStatus.COMPLETED
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        tender.status = JobStatus.COMPLETED
        db.commit()

        AuditLogger.log(
            db,
            action="TENDER_EXTRACTION_COMPLETED",
            entity_type="TENDER",
            entity_id=id,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            payload={"job_id": job.id, "request_id": req_id, "requirements_count": new_count},
        )

        res_payload = JobRead.model_validate(job).model_dump(mode="json")
        IdempotencyService.complete(db, record, status.HTTP_200_OK, res_payload)
        return job
    except HTTPException:
        raise
    except Exception:
        IdempotencyService.fail(db, record)
        raise
    finally:
        OperationLockService.release_lock(db, "TENDER", id, "PROCESS_TENDER", job.id)



@router.get("/{id}/requirements", response_model=list[TenderRequirementRead])
def get_tender_requirements(
    id: str,
    approved_only: bool = False,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    tender = db.query(Tender).filter(Tender.id == id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {id} not found.",
        )

    query = db.query(TenderRequirement).filter(TenderRequirement.tender_id == id)
    if approved_only:
        query = query.filter(TenderRequirement.is_approved == True)
    requirements = query.all()

    # Canonical deduplication prioritizing approved requirements
    sorted_reqs = sorted(requirements, key=lambda r: (not r.is_approved, r.created_at or datetime.min))
    seen_sigs: set[str] = set()
    canonical: list[TenderRequirement] = []
    for r in sorted_reqs:
        c_k = (r.clause or "").strip().lower()
        t_k = str(r.requirement_type.value if hasattr(r.requirement_type, "value") else r.requirement_type).upper()
        f_k = (r.field or "").strip().lower()
        o_k = str(r.operator.value if hasattr(r.operator, "value") else r.operator).upper()
        v_k = str(r.expected_value).strip().lower()

        clause_type_sig = f"{c_k}::{t_k}" if c_k else None
        semantic_sig = f"{t_k}::{f_k}::{o_k}::{v_k}"

        if clause_type_sig and clause_type_sig in seen_sigs:
            continue
        if semantic_sig in seen_sigs:
            continue

        if clause_type_sig:
            seen_sigs.add(clause_type_sig)
        seen_sigs.add(semantic_sig)
        canonical.append(r)

    return canonical


@router.post("/{tender_id}/requirements", response_model=TenderRequirementRead, status_code=status.HTTP_201_CREATED)
def create_manual_tender_requirement(
    tender_id: str,
    payload: TenderRequirementCreate,
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Manually create an approved tender requirement by an authorized procurement officer."""
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {tender_id} not found.",
        )

    # Executable rule validation via RuleValidator
    RuleValidator.validate_or_raise_http(payload)

    # Document ownership validation
    if payload.document_id:
        doc_row = db.query(Document).filter(Document.id == payload.document_id).first()
        if not doc_row or doc_row.tender_id != tender_id or doc_row.bidder_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid document_id '{payload.document_id}': document does not belong to tender {tender_id}.",
            )

    now_iso = datetime.now(timezone.utc).isoformat()
    meta = dict(payload.metadata_json or {})
    meta["created_by_officer"] = principal.user_id
    meta["approved_by"] = principal.user_id
    meta["approved_at"] = now_iso
    meta["approval_source"] = "MANUAL_OFFICER_CREATION"

    req = TenderRequirement(
        tender_id=tender_id,
        clause=payload.clause,
        requirement_type=payload.requirement_type,
        field=payload.field,
        operator=payload.operator,
        expected_value=payload.expected_value,
        unit=payload.unit,
        mandatory=payload.mandatory,
        source_page=payload.source_page,
        source_text=payload.source_text,
        confidence=payload.confidence,
        requires_verification=payload.requires_verification,
        is_approved=True,  # Human officer explicitly authored it
        document_id=payload.document_id,
        metadata_json=meta,
    )
    db.add(req)
    db.flush()

    # Single-transaction audit entry staging
    AuditLogger.create_entry(
        db,
        action="TENDER_REQUIREMENT_CREATED",
        entity_type="TENDER_REQUIREMENT",
        entity_id=req.id,
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        payload={
            "tender_id": tender_id,
            "clause": req.clause,
            "field": req.field,
            "is_approved": True,
        },
    )
    db.commit()
    db.refresh(req)

    return req


@router.post("/{tender_id}/requirements/{requirement_id}/approve", response_model=TenderRequirementRead)
def approve_tender_requirement(
    tender_id: str,
    requirement_id: str,
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Approve a candidate tender requirement by an authorized procurement officer."""
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {tender_id} not found.",
        )

    req = (
        db.query(TenderRequirement)
        .filter(
            TenderRequirement.id == requirement_id,
            TenderRequirement.tender_id == tender_id,
        )
        .first()
    )
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Requirement with ID {requirement_id} not found for tender {tender_id}.",
        )

    # Executable rule validation via RuleValidator
    RuleValidator.validate_or_raise_http(req)

    meta = dict(req.metadata_json or {})

    # Idempotent approval check
    if not req.is_approved:
        req.is_approved = True
        now_iso = datetime.now(timezone.utc).isoformat()
        meta["approved_by"] = principal.user_id
        meta["approved_at"] = now_iso
        meta["approval_status"] = "APPROVED"
        req.metadata_json = meta

        # Single-transaction audit entry staging
        AuditLogger.create_entry(
            db,
            action="TENDER_REQUIREMENT_APPROVED",
            entity_type="TENDER_REQUIREMENT",
            entity_id=req.id,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            payload={
                "tender_id": tender_id,
                "clause": req.clause,
                "field": req.field,
                "approved_by": principal.user_id,
                "approved_at": now_iso,
            },
        )
        db.commit()
        db.refresh(req)

    return req


@router.post("/{tender_id}/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_tender_document(
    tender_id: str,
    file: UploadFile = File(...),
    document_type: DocumentType = Form(DocumentType.TENDER),
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Upload a raw document for a tender."""
    return await DocumentService.upload_tender_document(
        db, tender_id=tender_id, file=file, document_type=document_type, principal=principal
    )


@router.get("/{tender_id}/documents", response_model=list[DocumentRead])
def list_tender_documents(
    tender_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """List all documents associated with a tender."""
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {tender_id} not found.",
        )

    documents = db.query(Document).filter(Document.tender_id == tender_id).all()
    return documents
