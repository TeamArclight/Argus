from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session
from app.audit.logger import AuditLogger
from app.auth.dependencies import get_current_principal, require_roles
from app.db.session import get_db
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
    principal: AuthenticatedPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)),
    db: Session = Depends(get_db),
):
    tender = db.query(Tender).filter(Tender.id == id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {id} not found.",
        )

    # Check for existing active processing job
    existing_job = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.target_id == id,
            ProcessingJob.job_type == "EXTRACT_REQUIREMENTS",
            ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .first()
    )
    if existing_job:
        return existing_job

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

    AuditLogger.log(
        db,
        action="TENDER_PROCESSING_STARTED",
        entity_type="TENDER",
        entity_id=id,
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        payload={"job_id": job.id},
    )

    # Select tender document from DB (or raw_document_uri)
    doc = db.query(Document).filter(Document.tender_id == id).order_by(Document.created_at.desc()).first()

    document_id = doc.id if doc else f"doc_tender_{id}"
    document_sha256 = doc.sha256 if doc else None
    filename = doc.filename if doc else None
    content_type = doc.content_type if doc else None
    file_bytes = None

    storage = get_storage_provider()
    if doc and doc.storage_uri and storage.file_exists(doc.storage_uri):
        try:
            file_bytes = storage.read_file(doc.storage_uri)
        except Exception:
            file_bytes = None

    if not doc and not tender.raw_document_uri:
        job.status = JobStatus.FAILED
        job.error_message = "Missing tender raw_document_uri: cannot process tender without document."
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
        return job

    req_id = str(uuid.uuid4())
    AuditLogger.log(
        db,
        action="TENDER_EXTRACTION_REQUESTED",
        entity_type="TENDER",
        entity_id=id,
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        payload={"job_id": job.id, "request_id": req_id, "document_id": document_id},
    )

    ai_result = await ai_adapter.extract_tender(
        tender_id=id,
        document_uri=tender.raw_document_uri or doc.storage_uri if doc else "",
        document_id=document_id,
        document_sha256=document_sha256,
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
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
        return job

    # Non-destructive reprocessing: preserve all existing requirements (approved and unapproved)
    existing_reqs = (
        db.query(TenderRequirement)
        .filter(TenderRequirement.tender_id == id)
        .all()
    )

    def _norm_val(v: Any) -> str:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(float(v))
        return str(v)

    # Build lookup for deduplication
    existing_keys = {
        (
            r.clause,
            r.field,
            str(r.operator.value if hasattr(r.operator, "value") else r.operator),
            _norm_val(r.expected_value),
            r.document_id,
        )
        for r in existing_reqs
    }

    new_count = 0
    for item in ai_result.data:
        req_obj = TenderRequirementCreate.model_validate(item)
        
        doc_id_val = doc.id if doc else None
        op_str = str(req_obj.operator.value if hasattr(req_obj.operator, "value") else req_obj.operator)
        item_key = (req_obj.clause, req_obj.field, op_str, _norm_val(req_obj.expected_value), doc_id_val)

        if item_key in existing_keys:
            continue

        db_req = TenderRequirement(
            tender_id=id,
            document_id=doc_id_val,
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
            is_approved=False,  # All AI-extracted candidate requirements default to unapproved
            metadata_json=req_obj.metadata_json or {},
        )
        db.add(db_req)
        existing_keys.add(item_key)
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

    return job


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
    return requirements


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

    if not payload.clause or not payload.clause.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Requirement clause must be non-empty.")
    if not payload.field or not payload.field.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Requirement field must be non-empty.")
    if payload.expected_value is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Requirement expected_value cannot be None.")

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
    db.commit()
    db.refresh(req)

    AuditLogger.log(
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

    # Executable rule validation
    if not req.clause or not req.clause.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Requirement clause must be non-empty.")
    if not req.field or not req.field.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Requirement field must be non-empty.")
    if req.expected_value is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Requirement expected_value cannot be None.")
    if req.confidence < 0.0 or req.confidence > 1.0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Requirement confidence must be between 0.0 and 1.0.")

    meta = dict(req.metadata_json or {})

    # Idempotent approval check
    if not req.is_approved:
        req.is_approved = True
        now_iso = datetime.now(timezone.utc).isoformat()
        meta["approved_by"] = principal.user_id
        meta["approved_at"] = now_iso
        meta["approval_status"] = "APPROVED"
        req.metadata_json = meta

        AuditLogger.log(
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


