from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.audit.logger import AuditLogger
from app.db.session import get_db
from app.models.domain import (
    AuditEvent,
    Bidder,
    Document,
    ExtractedFact,
    HumanDecision,
    ProcessingJob,
    RuleEvaluation,
    Tender,
    TenderRequirement,
    VerificationResult,
)
from app.schemas.canonical import (
    BidderCreate,
    BidderRead,
    ComplianceOverviewRead,
    DocumentCreate,
    EvidenceRead,
    HumanDecisionCreate,
    HumanDecisionRead,
    HumanDecisionStatus,
    JobRead,
    JobStage,
    JobStatus,
    ReportRead,

)
from app.services.ai_adapter import AIServiceAdapter
from app.services.bid_verification_service import BidVerificationService

router = APIRouter(tags=["Bidders"])
ai_adapter = AIServiceAdapter()


@router.post("/tenders/{tender_id}/bidders", response_model=BidderRead, status_code=status.HTTP_201_CREATED)
async def create_bidder(tender_id: str, payload: BidderCreate, db: Session = Depends(get_db)):
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {tender_id} not found.",
        )

    bidder = Bidder(
        tender_id=tender_id,
        bidder_name=payload.bidder_name,
        gstin=payload.gstin,
        udyam_number=payload.udyam_number,
        cin=payload.cin,
        pan=payload.pan,
        status=HumanDecisionStatus.PENDING,
        metadata_json=payload.metadata_json,
    )
    db.add(bidder)
    db.commit()
    db.refresh(bidder)

    AuditLogger.log(
        db,
        action="BIDDER_CREATED",
        entity_type="BIDDER",
        entity_id=bidder.id,
        payload={"bidder_name": bidder.bidder_name, "gstin": bidder.gstin},
    )

    db.refresh(bidder)
    return bidder


@router.get("/tenders/{tender_id}/bidders", response_model=list[BidderRead])
def list_bidders_for_tender(tender_id: str, db: Session = Depends(get_db)):
    tenders = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tenders:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {tender_id} not found.",
        )
    bidders = db.query(Bidder).filter(Bidder.tender_id == tender_id).all()
    return bidders


@router.get("/bidders/{id}", response_model=BidderRead)
def get_bidder(id: str, db: Session = Depends(get_db)):
    bidder = db.query(Bidder).filter(Bidder.id == id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {id} not found.",
        )
    return bidder


@router.post("/bidders/{id}/verify", response_model=JobRead)
async def verify_bidder(id: str, db: Session = Depends(get_db)):
    bidder = db.query(Bidder).filter(Bidder.id == id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {id} not found.",
        )

    # Check for existing active verification job
    existing_job = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.target_id == id,
            ProcessingJob.job_type == "VERIFY_BIDDER",
            ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .first()
    )
    if existing_job:
        return existing_job

    job = ProcessingJob(
        target_type="BIDDER",
        target_id=id,
        job_type="VERIFY_BIDDER",
        status=JobStatus.RUNNING,
        current_stage=JobStage.VERIFICATION,
        progress=10,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    service = BidVerificationService(db)
    await service.run_verification_workflow(bidder_id=id, job_id=job.id)

    db.refresh(job)
    return job


@router.get("/bidders/{id}/compliance", response_model=ComplianceOverviewRead)
async def get_bidder_compliance(id: str, db: Session = Depends(get_db)):
    bidder = db.query(Bidder).filter(Bidder.id == id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {id} not found.",
        )

    service = BidVerificationService(db)

    # If no rule evaluations exist yet, trigger workflow on demand
    evals_exist = db.query(RuleEvaluation).filter(RuleEvaluation.bidder_id == id).count()
    if evals_exist == 0:
        return await service.run_verification_workflow(bidder_id=id)

    # Reconstruct from DB state
    evaluations_db = db.query(RuleEvaluation).filter(RuleEvaluation.bidder_id == id).all()
    risk_db = db.query(Bidder).filter(Bidder.id == id).first().risk_signals
    latest_decision_db = (
        db.query(HumanDecision)
        .filter(HumanDecision.bidder_id == id)
        .order_by(HumanDecision.decided_at.desc())
        .first()
    )

    overall_status = service.compute_overall_status(evaluations_db)

    return ComplianceOverviewRead(
        bidder_id=id,
        tender_id=bidder.tender_id,
        overall_status=overall_status,
        human_decision_status=latest_decision_db.status if latest_decision_db else HumanDecisionStatus.PENDING,
        rule_evaluations=evaluations_db,
        risk_signals=risk_db,
        latest_decision=HumanDecisionRead.model_validate(latest_decision_db) if latest_decision_db else None,
    )


@router.post("/bidders/{id}/decision", response_model=HumanDecisionRead, status_code=status.HTTP_201_CREATED)
def record_human_decision(id: str, payload: HumanDecisionCreate, db: Session = Depends(get_db)):
    bidder = db.query(Bidder).filter(Bidder.id == id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {id} not found.",
        )

    decision = HumanDecision(
        bidder_id=id,
        status=payload.status,
        reason_code=payload.reason_code,
        remarks=payload.remarks,
        officer_id=payload.officer_id,
        officer_name=payload.officer_name,
    )
    db.add(decision)

    # Update bidder master human decision status
    bidder.status = payload.status
    db.commit()
    db.refresh(decision)

    AuditLogger.log(
        db,
        action="HUMAN_DECISION_RECORDED",
        entity_type="BIDDER",
        entity_id=id,
        actor_id=payload.officer_id,
        actor_role="PROCUREMENT_OFFICER",
        payload={
            "status": payload.status,
            "reason_code": payload.reason_code,
            "officer_name": payload.officer_name,
        },
    )
    return decision


@router.get("/bidders/{id}/report", response_model=ReportRead)
async def get_bidder_report(id: str, db: Session = Depends(get_db)):
    bidder = db.query(Bidder).filter(Bidder.id == id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {id} not found.",
        )

    compliance_overview = await get_bidder_compliance(id, db)
    verifications_db = db.query(VerificationResult).filter(VerificationResult.bidder_id == id).all()
    audit_count = db.query(AuditEvent).filter(AuditEvent.entity_id == id).count()

    tender = db.query(Tender).filter(Tender.id == bidder.tender_id).first()

    return ReportRead(
        generated_at=datetime.now(timezone.utc),
        tender=tender,
        bidder=bidder,
        compliance_overview=compliance_overview,
        verification_results=verifications_db,
        evidence=[],
        audit_trail_count=audit_count,
    )
