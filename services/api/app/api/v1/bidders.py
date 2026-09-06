from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.audit.logger import AuditLogger
from app.db.session import get_db
from app.models.domain import (
    AuditEvent,
    Bidder,
    ComplianceRun,
    Document,
    ExtractedFact,
    HumanDecision,
    ProcessingJob,
    RiskSignal,
    RuleEvaluation,
    Tender,
    TenderRequirement,
    VerificationResult,
)
from app.schemas.canonical import (
    BidderCreate,
    BidderRead,
    ComplianceOverviewRead,
    ComplianceRunDetailRead,
    ComplianceRunRead,
    ComplianceRunSummaryRead,
    ComplianceStatus,
    DocumentCreate,
    EvidenceRead,
    HumanDecisionCreate,
    HumanDecisionRead,
    HumanDecisionStatus,
    JobRead,
    JobStage,
    JobStatus,
    ReportRead,
    RiskSignalRead,
    RuleEvaluationRead,
    VerificationResultRead,
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

    # Find latest successfully COMPLETED ComplianceRun
    latest_run = (
        db.query(ComplianceRun)
        .filter(
            ComplianceRun.bidder_id == id,
            ComplianceRun.execution_status == JobStatus.COMPLETED,
        )
        .order_by(
            ComplianceRun.completed_at.desc(),
            ComplianceRun.created_at.desc(),
            ComplianceRun.id.desc(),
        )
        .first()
    )

    latest_decision_db = (
        db.query(HumanDecision)
        .filter(HumanDecision.bidder_id == id)
        .order_by(HumanDecision.decided_at.desc())
        .first()
    )
    latest_decision_schema = HumanDecisionRead.model_validate(latest_decision_db) if latest_decision_db else None

    # NO side effect: If no completed ComplianceRun exists, return UNKNOWN without silently running verification
    if not latest_run:
        return ComplianceOverviewRead(
            bidder_id=id,
            tender_id=bidder.tender_id,
            overall_status=ComplianceStatus.UNKNOWN,
            human_decision_status=latest_decision_db.status if latest_decision_db else HumanDecisionStatus.PENDING,
            rule_evaluations=[],
            risk_signals=[],
            latest_decision=latest_decision_schema,
        )

    # Reconstruct overview using evaluations and risk signals scoped strictly to latest_run.id
    evaluations_db = (
        db.query(RuleEvaluation)
        .filter(RuleEvaluation.run_id == latest_run.id)
        .all()
    )
    evaluations_schema = [RuleEvaluationRead.model_validate(e) for e in evaluations_db]

    risk_db = (
        db.query(RiskSignal)
        .filter(RiskSignal.run_id == latest_run.id)
        .all()
    )
    risk_schema = [RiskSignalRead.model_validate(r) for r in risk_db]

    overall_status = latest_run.overall_status or service.compute_overall_status(evaluations_db)

    return ComplianceOverviewRead(
        bidder_id=id,
        tender_id=bidder.tender_id,
        overall_status=overall_status,
        human_decision_status=latest_decision_db.status if latest_decision_db else HumanDecisionStatus.PENDING,
        rule_evaluations=evaluations_schema,
        risk_signals=risk_schema,
        latest_decision=latest_decision_schema,
    )


@router.get("/bidders/{bidder_id}/runs", response_model=list[ComplianceRunSummaryRead])
def list_bidder_compliance_runs(bidder_id: str, db: Session = Depends(get_db)):
    bidder = db.query(Bidder).filter(Bidder.id == bidder_id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {bidder_id} not found.",
        )

    runs = (
        db.query(ComplianceRun)
        .filter(ComplianceRun.bidder_id == bidder_id)
        .order_by(ComplianceRun.created_at.desc(), ComplianceRun.id.desc())
        .all()
    )

    summaries = []
    for r in runs:
        eval_cnt = r.summary_json.get("evaluation_count") if r.summary_json else None
        if eval_cnt is None:
            eval_cnt = db.query(RuleEvaluation).filter(RuleEvaluation.run_id == r.id).count()
        risk_cnt = r.summary_json.get("risk_count") if r.summary_json else None
        if risk_cnt is None:
            risk_cnt = db.query(RiskSignal).filter(RiskSignal.run_id == r.id).count()

        summaries.append(
            ComplianceRunSummaryRead(
                id=r.id,
                bidder_id=r.bidder_id,
                tender_id=r.tender_id,
                job_id=r.job_id,
                execution_status=r.execution_status,
                overall_status=r.overall_status,
                started_at=r.started_at,
                completed_at=r.completed_at,
                evaluation_count=eval_cnt,
                risk_count=risk_cnt,
            )
        )
    return summaries


@router.get("/bidders/{bidder_id}/runs/{run_id}", response_model=ComplianceRunDetailRead)
def get_bidder_compliance_run_detail(bidder_id: str, run_id: str, db: Session = Depends(get_db)):
    bidder = db.query(Bidder).filter(Bidder.id == bidder_id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {bidder_id} not found.",
        )

    run = db.query(ComplianceRun).filter(ComplianceRun.id == run_id).first()
    if not run or run.bidder_id != bidder_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Compliance run {run_id} not found for bidder {bidder_id}.",
        )

    verifications_db = db.query(VerificationResult).filter(VerificationResult.run_id == run_id).all()
    evaluations_db = db.query(RuleEvaluation).filter(RuleEvaluation.run_id == run_id).all()
    risk_db = db.query(RiskSignal).filter(RiskSignal.run_id == run_id).all()

    return ComplianceRunDetailRead(
        run=ComplianceRunRead.model_validate(run),
        verification_results=[VerificationResultRead.model_validate(v) for v in verifications_db],
        rule_evaluations=[RuleEvaluationRead.model_validate(e) for e in evaluations_db],
        risk_signals=[RiskSignalRead.model_validate(r) for r in risk_db],
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
async def get_bidder_report(id: str, run_id: str | None = Query(None, description="Optional compliance run ID"), db: Session = Depends(get_db)):
    bidder = db.query(Bidder).filter(Bidder.id == id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {id} not found.",
        )

    service = BidVerificationService(db)
    tender = db.query(Tender).filter(Tender.id == bidder.tender_id).first()
    audit_count = db.query(AuditEvent).filter(AuditEvent.entity_id == id).count()

    if run_id:
        target_run = db.query(ComplianceRun).filter(ComplianceRun.id == run_id).first()
        if not target_run or target_run.bidder_id != id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Compliance run {run_id} not found for bidder {id}.",
            )
    else:
        target_run = (
            db.query(ComplianceRun)
            .filter(
                ComplianceRun.bidder_id == id,
                ComplianceRun.execution_status == JobStatus.COMPLETED,
            )
            .order_by(
                ComplianceRun.completed_at.desc(),
                ComplianceRun.created_at.desc(),
                ComplianceRun.id.desc(),
            )
            .first()
        )

    latest_decision_db = (
        db.query(HumanDecision)
        .filter(HumanDecision.bidder_id == id)
        .order_by(HumanDecision.decided_at.desc())
        .first()
    )
    latest_decision_schema = HumanDecisionRead.model_validate(latest_decision_db) if latest_decision_db else None

    if not target_run:
        overview = ComplianceOverviewRead(
            bidder_id=id,
            tender_id=bidder.tender_id,
            overall_status=ComplianceStatus.UNKNOWN,
            human_decision_status=latest_decision_db.status if latest_decision_db else HumanDecisionStatus.PENDING,
            rule_evaluations=[],
            risk_signals=[],
            latest_decision=latest_decision_schema,
        )
        return ReportRead(
            generated_at=datetime.now(timezone.utc),
            tender=tender,
            bidder=bidder,
            compliance_overview=overview,
            verification_results=[],
            evidence=[],
            audit_trail_count=audit_count,
        )

    evaluations_db = db.query(RuleEvaluation).filter(RuleEvaluation.run_id == target_run.id).all()
    evaluations_schema = [RuleEvaluationRead.model_validate(e) for e in evaluations_db]
    risk_db = db.query(RiskSignal).filter(RiskSignal.run_id == target_run.id).all()
    risk_schema = [RiskSignalRead.model_validate(r) for r in risk_db]
    verifications_db = db.query(VerificationResult).filter(VerificationResult.run_id == target_run.id).all()

    overall_status = target_run.overall_status or service.compute_overall_status(evaluations_db)

    overview = ComplianceOverviewRead(
        bidder_id=id,
        tender_id=bidder.tender_id,
        overall_status=overall_status,
        human_decision_status=latest_decision_db.status if latest_decision_db else HumanDecisionStatus.PENDING,
        rule_evaluations=evaluations_schema,
        risk_signals=risk_schema,
        latest_decision=latest_decision_schema,
    )

    return ReportRead(
        generated_at=datetime.now(timezone.utc),
        tender=tender,
        bidder=bidder,
        compliance_overview=overview,
        verification_results=verifications_db,
        evidence=[],
        audit_trail_count=audit_count,
    )
