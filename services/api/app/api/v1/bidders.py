from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session
from app.audit.logger import AuditLogger
from app.auth.dependencies import get_current_principal, require_roles
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
    AuthenticatedPrincipal,
    BidderCreate,
    BidderRead,
    ComplianceOverviewRead,
    ComplianceRunDetailRead,
    ComplianceRunRead,
    ComplianceRunSummaryRead,
    ComplianceStatus,
    DocumentCreate,
    DocumentRead,
    DocumentType,
    EvidenceRead,
    ExtractedFactCreate,
    HumanDecisionCreate,
    HumanDecisionRead,
    HumanDecisionStatus,
    JobRead,
    JobStage,
    JobStatus,
    ReportRead,
    RiskSignalRead,
    RuleEvaluationRead,
    UserRole,
    VerificationResultRead,
)
from app.services.ai_adapter import AIServiceAdapter
from app.services.bid_verification_service import BidVerificationService
from app.services.document_service import DocumentService
from app.storage.factory import get_storage_provider

router = APIRouter(tags=["Bidders"])
ai_adapter = AIServiceAdapter()


@router.post("/tenders/{tender_id}/bidders", response_model=BidderRead, status_code=status.HTTP_201_CREATED)
async def create_bidder(
    tender_id: str,
    payload: BidderCreate,
    principal: AuthenticatedPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)),
    db: Session = Depends(get_db),
):
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
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        payload={"bidder_name": bidder.bidder_name, "gstin": bidder.gstin},
    )

    db.refresh(bidder)
    return bidder


@router.get("/tenders/{tender_id}/bidders", response_model=list[BidderRead])
def list_bidders_for_tender(
    tender_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    tenders = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tenders:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender with ID {tender_id} not found.",
        )
    bidders = db.query(Bidder).filter(Bidder.tender_id == tender_id).all()
    return bidders


@router.get("/bidders/{id}", response_model=BidderRead)
def get_bidder(
    id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    bidder = db.query(Bidder).filter(Bidder.id == id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {id} not found.",
        )
    return bidder


@router.post("/bidders/{id}/verify", response_model=JobRead)
async def verify_bidder(
    id: str,
    principal: AuthenticatedPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)),
    db: Session = Depends(get_db),
):
    bidder = db.query(Bidder).filter(Bidder.id == id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {id} not found.",
        )

    # 1. Check for an existing RUNNING ComplianceRun for bidder_id
    existing_run = (
        db.query(ComplianceRun)
        .filter(
            ComplianceRun.bidder_id == id,
            ComplianceRun.execution_status == JobStatus.RUNNING,
        )
        .first()
    )
    if existing_run:
        if existing_run.job_id:
            active_job = (
                db.query(ProcessingJob)
                .filter(
                    ProcessingJob.id == existing_run.job_id,
                    ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
                .first()
            )
            if active_job:
                return active_job

        # Stale/orphaned active run without an active job -> mark FAILED and log audit event
        BidVerificationService.close_orphaned_run(db, existing_run, id)

    # 2. Check for existing active verification job
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
    await service.run_verification_workflow(
        bidder_id=id,
        job_id=job.id,
        triggered_by=principal.user_id,
        actor_id=principal.user_id,
        actor_role=principal.role.value,
    )

    db.refresh(job)
    return job


@router.get("/bidders/{id}/compliance", response_model=ComplianceOverviewRead)
async def get_bidder_compliance(
    id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
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
def list_bidder_compliance_runs(
    bidder_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
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
def get_bidder_compliance_run_detail(
    bidder_id: str,
    run_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
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
def record_human_decision(
    id: str,
    payload: HumanDecisionCreate,
    principal: AuthenticatedPrincipal = Depends(require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)),
    db: Session = Depends(get_db),
):
    bidder = db.query(Bidder).filter(Bidder.id == id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {id} not found.",
        )

    officer_id = principal.user_id
    officer_name = principal.name or principal.user_id

    decision = HumanDecision(
        bidder_id=id,
        status=payload.status,
        reason_code=payload.reason_code,
        remarks=payload.remarks,
        officer_id=officer_id,
        officer_name=officer_name,
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
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        payload={
            "status": payload.status,
            "reason_code": payload.reason_code,
            "officer_name": officer_name,
        },
    )
    return decision


@router.get("/bidders/{id}/report", response_model=ReportRead)
async def get_bidder_report(
    id: str,
    run_id: str | None = Query(None, description="Optional compliance run ID"),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
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


@router.post("/bidders/{bidder_id}/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_bidder_document(
    bidder_id: str,
    file: UploadFile = File(...),
    document_type: DocumentType = Form(...),
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Upload a raw document for a bidder."""
    return await DocumentService.upload_bidder_document(
        db, bidder_id=bidder_id, file=file, document_type=document_type, principal=principal
    )


@router.get("/bidders/{bidder_id}/documents", response_model=list[DocumentRead])
def list_bidder_documents(
    bidder_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """List all documents associated with a bidder."""
    bidder = db.query(Bidder).filter(Bidder.id == bidder_id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {bidder_id} not found.",
        )

    documents = db.query(Document).filter(Document.bidder_id == bidder_id).all()
    return documents


@router.post("/bidders/{bidder_id}/process-documents", response_model=JobRead)
async def process_bidder_documents(
    bidder_id: str,
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Trigger AI fact extraction across all uploaded bidder documents."""
    bidder = db.query(Bidder).filter(Bidder.id == bidder_id).first()
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder with ID {bidder_id} not found.",
        )

    # Check for existing active processing job
    existing_job = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.target_id == bidder_id,
            ProcessingJob.job_type == "EXTRACT_FACTS",
            ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .first()
    )
    if existing_job:
        return existing_job

    job = ProcessingJob(
        target_type="BIDDER",
        target_id=bidder_id,
        job_type="EXTRACT_FACTS",
        status=JobStatus.RUNNING,
        current_stage=JobStage.EXTRACTION,
        progress=10,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    req_id = str(uuid.uuid4())
    AuditLogger.log(
        db,
        action="DOCUMENT_EXTRACTION_REQUESTED",
        entity_type="BIDDER",
        entity_id=bidder_id,
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        payload={"job_id": job.id, "request_id": req_id},
    )

    documents = db.query(Document).filter(Document.bidder_id == bidder_id).all()
    if not documents:
        job.status = JobStatus.FAILED
        job.error_message = "No documents found for bidder: upload bidder documents before extraction."
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        db.commit()

        AuditLogger.log(
            db,
            action="DOCUMENT_EXTRACTION_FAILED",
            entity_type="BIDDER",
            entity_id=bidder_id,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            payload={"job_id": job.id, "request_id": req_id, "error_code": "MISSING_BIDDER_DOCUMENT"},
        )
        return job

    storage = get_storage_provider()
    total_docs = len(documents)
    successful_docs = 0
    failed_docs = 0

    # Collect referenced fact IDs to preserve historical compliance evidence
    referenced_evals = db.query(RuleEvaluation.evidence_ids).filter(RuleEvaluation.bidder_id == bidder_id).all()
    referenced_fact_ids = set()
    for row in referenced_evals:
        if row[0] and isinstance(row[0], list):
            referenced_fact_ids.update(row[0])

    for doc in documents:
        file_bytes = None
        if doc.storage_uri and storage.file_exists(doc.storage_uri):
            try:
                file_bytes = storage.read_file(doc.storage_uri)
            except Exception:
                file_bytes = None

        doc_type_val = doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type)

        ai_res = await ai_adapter.extract_document(
            document_id=doc.id,
            bidder_id=bidder_id,
            document_type=doc_type_val,
            document_sha256=doc.sha256,
            file_bytes=file_bytes,
            filename=doc.filename,
            content_type=doc.content_type,
            request_id=req_id,
        )

        if not ai_res.success or not ai_res.data:
            failed_docs += 1
            AuditLogger.log(
                db,
                action="DOCUMENT_EXTRACTION_FAILED",
                entity_type="DOCUMENT",
                entity_id=doc.id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "request_id": req_id, "bidder_id": bidder_id, "error_code": ai_res.error_code, "message": ai_res.message},
            )
            continue

        successful_docs += 1
        
        # Non-destructive reprocessing: delete only unreferenced existing facts for this document
        existing_facts = db.query(ExtractedFact).filter(ExtractedFact.document_id == doc.id).all()
        for f in existing_facts:
            if f.id not in referenced_fact_ids:
                db.delete(f)

        for item in ai_res.data:
            fact_obj = ExtractedFactCreate.model_validate(item)
            
            # Truthful provenance metadata
            meta = fact_obj.metadata_json or {}
            meta.setdefault("request_id", req_id)
            meta.setdefault("document_id", doc.id)
            if doc.sha256:
                meta.setdefault("document_sha256", doc.sha256)

            db_fact = ExtractedFact(
                document_id=doc.id,
                bidder_id=bidder_id,
                field=fact_obj.field,
                value=fact_obj.value,
                source_page=fact_obj.source_page,
                source_text=fact_obj.source_text,
                confidence=fact_obj.confidence,
                metadata_json=meta,
            )
            db.add(db_fact)

        AuditLogger.log(
            db,
            action="DOCUMENT_EXTRACTION_COMPLETED",
            entity_type="DOCUMENT",
            entity_id=doc.id,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            payload={"job_id": job.id, "request_id": req_id, "bidder_id": bidder_id, "facts_count": len(ai_res.data)},
        )

    if successful_docs == total_docs:
        job.status = JobStatus.COMPLETED
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
    elif successful_docs > 0:
        job.status = JobStatus.COMPLETED
        job.error_message = f"Partial extraction completion: {successful_docs}/{total_docs} documents processed successfully."
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
    else:
        job.status = JobStatus.FAILED
        job.error_message = f"Extraction failed for all {total_docs} documents."
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)

    db.commit()
    return job



