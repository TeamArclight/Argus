from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.audit.logger import AuditLogger
from app.db.session import get_db
from app.models.domain import ProcessingJob, Tender, TenderRequirement
from app.schemas.canonical import (
    JobRead,
    JobStage,
    JobStatus,
    TenderCreate,
    TenderRead,
    TenderRequirementCreate,
    TenderRequirementRead,
)
from app.services.ai_adapter import AIServiceAdapter

router = APIRouter(prefix="/tenders", tags=["Tenders"])
ai_adapter = AIServiceAdapter()


@router.post("", response_model=TenderRead, status_code=status.HTTP_201_CREATED)
def create_tender(payload: TenderCreate, db: Session = Depends(get_db)):
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
        payload={"tender_number": tender.tender_number, "title": tender.title},
    )
    return tender


@router.get("", response_model=list[TenderRead])
def list_tenders(skip: int = 0, limit: int = 20, db: Session = Depends(get_db)):
    tenders = db.query(Tender).offset(skip).limit(limit).all()
    return tenders


@router.get("/{id}", response_model=TenderRead)
def get_tender(id: str, db: Session = Depends(get_db)):
    tender = db.query(Tender).filter(Tender.id == id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {id} not found.",
        )
    return tender


@router.post("/{id}/process", response_model=JobRead)
async def process_tender(id: str, db: Session = Depends(get_db)):
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
        payload={"job_id": job.id},
    )

    # Extract requirements via AIServiceAdapter
    doc_uri = tender.raw_document_uri or "s3://tenders/sample_tender.pdf"
    ai_result = await ai_adapter.extract_tender(tender_id=id, document_uri=doc_uri)

    if not ai_result.success or not ai_result.data:
        job.status = JobStatus.FAILED
        job.error_message = ai_result.message
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        tender.status = JobStatus.FAILED
        db.commit()
        return job

    # Persist extracted requirements into DB
    db.query(TenderRequirement).filter(TenderRequirement.tender_id == id).delete()

    for item in ai_result.data:
        req_obj = TenderRequirementCreate.model_validate(item)
        db_req = TenderRequirement(
            tender_id=id,
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
        )
        db.add(db_req)

    job.status = JobStatus.COMPLETED
    job.progress = 100
    job.completed_at = datetime.now(timezone.utc)
    tender.status = JobStatus.COMPLETED
    db.commit()

    AuditLogger.log(
        db,
        action="TENDER_REQUIREMENTS_EXTRACTED",
        entity_type="TENDER",
        entity_id=id,
        payload={"requirements_count": len(ai_result.data)},
    )

    return job


@router.get("/{id}/requirements", response_model=list[TenderRequirementRead])
def get_tender_requirements(id: str, db: Session = Depends(get_db)):
    tender = db.query(Tender).filter(Tender.id == id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {id} not found.",
        )

    requirements = db.query(TenderRequirement).filter(TenderRequirement.tender_id == id).all()
    return requirements
