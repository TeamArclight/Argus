from datetime import datetime, timezone
import hashlib
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
    Evidence,
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
    ComplianceMatrixRead,
    ComplianceMatrixRow,
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
    OperatorEnum,
    ReportRead,
    RequirementType,
    RiskSignalRead,
    RiskSummaryRead,
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

    snapshot_data = latest_run.input_snapshot_json or {}
    has_snapshot = _is_phase9_snapshot(snapshot_data)

    if has_snapshot:
        if "risk_signals" in snapshot_data and isinstance(snapshot_data["risk_signals"], list):
            try:
                risk_schema = [RiskSignalRead.model_validate(r) if isinstance(r, dict) else r for r in snapshot_data["risk_signals"]]
            except Exception:
                risk_schema = []
        else:
            risk_schema = []
    else:
        risk_db = (
            db.query(RiskSignal)
            .filter(RiskSignal.run_id == latest_run.id)
            .all()
        )
        risk_schema = [RiskSignalRead.model_validate(r) for r in risk_db]

    risk_summary_schema = None
    if has_snapshot and "risk_summary" in snapshot_data and isinstance(snapshot_data["risk_summary"], dict):
        try:
            risk_summary_schema = RiskSummaryRead.model_validate(snapshot_data["risk_summary"])
        except Exception:
            risk_summary_schema = None

    overall_status = latest_run.overall_status or service.compute_overall_status(evaluations_db)

    return ComplianceOverviewRead(
        bidder_id=id,
        tender_id=bidder.tender_id,
        overall_status=overall_status,
        human_decision_status=latest_decision_db.status if latest_decision_db else HumanDecisionStatus.PENDING,
        rule_evaluations=evaluations_schema,
        risk_signals=risk_schema,
        risk_summary=risk_summary_schema,
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


def _is_phase9_snapshot(snapshot_data: Any) -> bool:
    return bool(
        snapshot_data
        and isinstance(snapshot_data, dict)
        and (
            snapshot_data.get("snapshot_version")
            or "approved_requirements" in snapshot_data
            or "exact_evaluation_linkage" in snapshot_data
        )
    )


def build_compliance_matrix(
    db: Session,
    bidder: Bidder,
    run: ComplianceRun,
    evaluations_db: list[RuleEvaluation],
    verifications_db: list[VerificationResult],
) -> tuple[ComplianceMatrixRead, list[EvidenceRead], str | None]:
    """Reconstruct compliance matrix, evidence list, and historical limitations notice from run input snapshot without fallback fabrication or defaults."""
    snapshot_data = run.input_snapshot_json or {}
    has_snapshot = _is_phase9_snapshot(snapshot_data)

    limitations: list[str] = []
    if has_snapshot:
        required_sections = ["approved_requirements", "facts", "verifications", "evidence"]
        missing_sections = [s for s in required_sections if s not in snapshot_data or not isinstance(snapshot_data[s], list)]
        if missing_sections:
            limitations.append(f"Phase 9 historical snapshot missing or malformed required sections: {', '.join(missing_sections)}.")
    else:
        limitations.append(
            "Historical compliance run was completed before snapshot recording (Phase 9). Evidence citations and exact evaluation inputs cannot be reconstructed historically for this run."
        )

    evidence_schema: list[EvidenceRead] = []

    if has_snapshot and "evidence" in snapshot_data and isinstance(snapshot_data["evidence"], list):
        snapshot_ev = snapshot_data.get("evidence", [])
        for item in snapshot_ev:
            if isinstance(item, dict):
                ev_id = item.get("id")
                if not ev_id:
                    # Skip invalid/missing ID items without fabricating random UUIDs
                    continue

                created_at_val = item.get("created_at")
                if isinstance(created_at_val, str):
                    try:
                        created_at_val = datetime.fromisoformat(created_at_val.replace("Z", "+00:00"))
                    except Exception:
                        created_at_val = None
                elif not isinstance(created_at_val, datetime):
                    created_at_val = None

                obs_at_val = item.get("observed_at")
                if isinstance(obs_at_val, str):
                    try:
                        obs_at_val = datetime.fromisoformat(obs_at_val.replace("Z", "+00:00"))
                    except Exception:
                        obs_at_val = None
                elif not isinstance(obs_at_val, datetime):
                    obs_at_val = None

                evidence_schema.append(
                    EvidenceRead(
                        id=ev_id,
                        entity_type=item.get("entity_type", "BIDDER"),
                        entity_id=item.get("entity_id", bidder.id),
                        snippet=item.get("snippet"),
                        source_uri=item.get("source_uri"),
                        page_number=item.get("page_number"),
                        location_metadata=item.get("location_metadata", {}),
                        bidder_id=item.get("bidder_id", bidder.id),
                        tender_id=item.get("tender_id", bidder.tender_id),
                        document_id=item.get("document_id"),
                        extracted_fact_id=item.get("extracted_fact_id"),
                        verification_result_id=item.get("verification_result_id"),
                        run_id=run.id,
                        source_type=item.get("source_type"),
                        source_reference=item.get("source_reference"),
                        sha256=item.get("sha256"),
                        verification_mode=item.get("verification_mode"),
                        verification_status=item.get("verification_status"),
                        provider_identifier=item.get("provider_identifier"),
                        observed_at=obs_at_val,
                        created_at=created_at_val,
                    )
                )
    else:
        evidence_db = db.query(Evidence).filter(Evidence.run_id == run.id).all()
        evidence_schema = [EvidenceRead.model_validate(e) for e in evidence_db]

    approved_reqs = snapshot_data.get("approved_requirements", []) if has_snapshot and isinstance(snapshot_data.get("approved_requirements"), list) else []
    req_map: dict[str, dict[str, Any]] = {}
    if isinstance(approved_reqs, list):
        for r in approved_reqs:
            if isinstance(r, dict) and "id" in r:
                req_map[r["id"]] = r

    if has_snapshot:
        if "verifications" in snapshot_data and isinstance(snapshot_data["verifications"], list):
            verifications_list = snapshot_data["verifications"]
        else:
            verifications_list = []
    else:
        verifications_list = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in verifications_db]
    ver_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(verifications_list, list):
        for v in verifications_list:
            if isinstance(v, dict) and "id" in v:
                ver_by_id[v["id"]] = v

    facts_list = snapshot_data.get("facts", []) if has_snapshot and isinstance(snapshot_data.get("facts"), list) else []
    facts_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(facts_list, list):
        for f in facts_list:
            if isinstance(f, dict) and "id" in f:
                facts_by_id[f["id"]] = f

    evidence_by_id = {e.id: e for e in evidence_schema}

    eval_list = snapshot_data.get("exact_evaluation_linkage", []) if has_snapshot and "exact_evaluation_linkage" in snapshot_data and isinstance(snapshot_data["exact_evaluation_linkage"], list) else evaluations_db

    rows: list[ComplianceMatrixRow] = []
    for eval_item in eval_list:
        is_dict = isinstance(eval_item, dict)
        req_id = eval_item.get("requirement_id") if is_dict else eval_item.requirement_id
        eval_status = eval_item.get("status") if is_dict else eval_item.status
        reason_code = eval_item.get("reason_code") if is_dict else eval_item.reason_code
        observed_value = eval_item.get("observed_value") if is_dict else eval_item.observed_value
        ev_ids = eval_item.get("evidence_ids", []) if is_dict else (eval_item.evidence_ids or [])

        req_info = req_map.get(req_id) if has_snapshot else None
        if not req_info and not has_snapshot:
            # Legacy run: load current DB requirement as an explicitly labeled current reference
            db_req = db.query(TenderRequirement).filter(TenderRequirement.id == req_id).first()
            if db_req:
                req_info = {
                    "clause": db_req.clause,
                    "requirement_type": db_req.requirement_type,
                    "field": db_req.field,
                    "operator": db_req.operator,
                    "expected_value": db_req.expected_value,
                    "unit": db_req.unit,
                    "mandatory": db_req.mandatory,
                    "is_current_db_reference": True,
                }
        elif not req_info and has_snapshot:
            limitations.append(f"Historical requirement definition for requirement_id '{req_id}' missing from run snapshot.")

        clause = req_info.get("clause") if isinstance(req_info, dict) else None
        req_type = req_info.get("requirement_type") if isinstance(req_info, dict) else None
        field = req_info.get("field") if isinstance(req_info, dict) else None
        operator = req_info.get("operator") if isinstance(req_info, dict) else None
        expected_value = req_info.get("expected_value") if isinstance(req_info, dict) else None
        unit = req_info.get("unit") if isinstance(req_info, dict) else None
        mandatory = req_info.get("mandatory") if isinstance(req_info, dict) else None

        evidence_refs: list[dict[str, Any]] = []
        verification_refs: list[dict[str, Any]] = []
        source_refs: list[dict[str, Any]] = []

        for ev_id in ev_ids:
            if ev_id in evidence_by_id:
                ev_obj = evidence_by_id[ev_id]
                evidence_refs.append(
                    {
                        "evidence_id": ev_obj.id,
                        "document_id": ev_obj.document_id,
                        "extracted_fact_id": ev_obj.extracted_fact_id,
                        "verification_result_id": ev_obj.verification_result_id,
                        "source_type": ev_obj.source_type,
                        "source_reference": ev_obj.source_reference,
                        "page_number": ev_obj.page_number,
                        "snippet": ev_obj.snippet,
                        "sha256": ev_obj.sha256,
                        "verification_mode": ev_obj.verification_mode.value if hasattr(ev_obj.verification_mode, "value") else str(ev_obj.verification_mode) if ev_obj.verification_mode else None,
                        "verification_status": ev_obj.verification_status.value if hasattr(ev_obj.verification_status, "value") else str(ev_obj.verification_status) if ev_obj.verification_status else None,
                        "provider_identifier": ev_obj.provider_identifier,
                        "observed_at": ev_obj.observed_at.isoformat() if hasattr(ev_obj.observed_at, "isoformat") and ev_obj.observed_at else str(ev_obj.observed_at) if ev_obj.observed_at else None,
                    }
                )
                if ev_obj.verification_result_id and ev_obj.verification_result_id in ver_by_id:
                    v_item = ver_by_id[ev_obj.verification_result_id]
                    if v_item not in verification_refs:
                        verification_refs.append(v_item)
                if ev_obj.extracted_fact_id and ev_obj.extracted_fact_id in facts_by_id:
                    f_item = facts_by_id[ev_obj.extracted_fact_id]
                    if f_item not in source_refs:
                        source_refs.append(
                            {
                                "document_id": f_item.get("document_id"),
                                "source_page": f_item.get("source_page"),
                                "source_text": f_item.get("source_text"),
                                "confidence": f_item.get("confidence"),
                            }
                        )

        # STRICTLY NO FIELD-BASED FALLBACK: Only attach references linked by exact evaluation linkage!

        review_req = bool(
            eval_status in (ComplianceStatus.REVIEW_REQUIRED, ComplianceStatus.UNKNOWN, ComplianceStatus.FAIL)
            or (eval_status != ComplianceStatus.PASS and (mandatory is True or mandatory is None))
        )

        rows.append(
            ComplianceMatrixRow(
                requirement_id=req_id,
                clause=clause,
                requirement_type=req_type,
                field=field,
                operator=operator,
                expected_value=expected_value,
                unit=unit,
                mandatory=mandatory,
                status=eval_status,
                reason_code=reason_code,
                observed_value=observed_value,
                evidence_ids=ev_ids,
                evidence_refs=evidence_refs,
                verification_refs=verification_refs,
                source_refs=source_refs,
                review_required=review_req,
            )
        )

    notice = "; ".join(limitations) if limitations else None
    matrix = ComplianceMatrixRead(
        tender_id=bidder.tender_id,
        bidder_id=bidder.id,
        run_id=run.id,
        overall_status=run.overall_status or ComplianceStatus.UNKNOWN,
        rows=rows,
        historical_limitations_notice=notice,
    )
    return matrix, evidence_schema, notice


@router.get("/bidders/{id}/matrix", response_model=ComplianceMatrixRead)
async def get_bidder_compliance_matrix(
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

    if not target_run:
        return ComplianceMatrixRead(
            tender_id=bidder.tender_id,
            bidder_id=id,
            run_id=None,
            overall_status=ComplianceStatus.UNKNOWN,
            rows=[],
            historical_limitations_notice=None,
        )

    evaluations_db = db.query(RuleEvaluation).filter(RuleEvaluation.run_id == target_run.id).all()
    verifications_db = db.query(VerificationResult).filter(VerificationResult.run_id == target_run.id).all()

    matrix, _, _ = build_compliance_matrix(db, bidder, target_run, evaluations_db, verifications_db)
    return matrix


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
        empty_matrix = ComplianceMatrixRead(
            tender_id=bidder.tender_id,
            bidder_id=id,
            run_id=None,
            overall_status=ComplianceStatus.UNKNOWN,
            rows=[],
            historical_limitations_notice=None,
        )
        return ReportRead(
            generated_at=datetime.now(timezone.utc),
            tender=tender,
            bidder=bidder,
            compliance_overview=overview,
            compliance_matrix=empty_matrix,
            verification_results=[],
            evidence=[],
            human_decision=latest_decision_schema,
            historical_limitations_notice=None,
            audit_trail_count=audit_count,
        )

    evaluations_db = db.query(RuleEvaluation).filter(RuleEvaluation.run_id == target_run.id).all()
    evaluations_schema = [RuleEvaluationRead.model_validate(e) for e in evaluations_db]

    has_snapshot = _is_phase9_snapshot(target_run.input_snapshot_json)
    snapshot_data = target_run.input_snapshot_json or {}

    if has_snapshot:
        if "verifications" in snapshot_data and isinstance(snapshot_data["verifications"], list):
            verifications_for_report = [
                VerificationResultRead.model_validate(v) if isinstance(v, dict) else v
                for v in snapshot_data["verifications"]
            ]
        else:
            verifications_for_report = []

        if "risk_signals" in snapshot_data and isinstance(snapshot_data["risk_signals"], list):
            risk_schema = [RiskSignalRead.model_validate(r) if isinstance(r, dict) else r for r in snapshot_data["risk_signals"]]
        else:
            risk_schema = []

        if "risk_summary" in snapshot_data and isinstance(snapshot_data["risk_summary"], dict):
            try:
                risk_summary_schema = RiskSummaryRead.model_validate(snapshot_data["risk_summary"])
            except Exception as e:
                import logging
                logging.error(f"Risk summary validation error in snapshot: {e}")
                risk_summary_schema = None
        else:
            risk_summary_schema = None
    else:
        verifications_db = db.query(VerificationResult).filter(VerificationResult.run_id == target_run.id).all()
        verifications_for_report = [VerificationResultRead.model_validate(v) for v in verifications_db]
        risk_db = db.query(RiskSignal).filter(RiskSignal.run_id == target_run.id).all()
        risk_schema = [RiskSignalRead.model_validate(r) for r in risk_db]
        risk_summary_schema = None

    overall_status = target_run.overall_status or service.compute_overall_status(evaluations_db)

    overview = ComplianceOverviewRead(
        bidder_id=id,
        tender_id=bidder.tender_id,
        overall_status=overall_status,
        human_decision_status=latest_decision_db.status if latest_decision_db else HumanDecisionStatus.PENDING,
        rule_evaluations=evaluations_schema,
        risk_signals=risk_schema,
        risk_summary=risk_summary_schema,
        latest_decision=latest_decision_schema,
    )

    verifications_db = db.query(VerificationResult).filter(VerificationResult.run_id == target_run.id).all()
    matrix, evidence_schema, notice = build_compliance_matrix(
        db, bidder, target_run, evaluations_db, verifications_db
    )

    return ReportRead(
        generated_at=datetime.now(timezone.utc),
        tender=tender,
        bidder=bidder,
        compliance_overview=overview,
        compliance_matrix=matrix,
        verification_results=verifications_for_report,
        evidence=evidence_schema,
        risk_summary=risk_summary_schema,
        human_decision=latest_decision_schema,
        historical_limitations_notice=notice,
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

    for doc in documents:
        if not doc.sha256 or not doc.sha256.strip():
            failed_docs += 1
            AuditLogger.log(
                db,
                action="DOCUMENT_EXTRACTION_FAILED",
                entity_type="DOCUMENT",
                entity_id=doc.id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "request_id": req_id, "bidder_id": bidder_id, "error_code": "MISSING_BIDDER_DOCUMENT"},
            )
            continue

        if not doc.storage_uri or not storage.file_exists(doc.storage_uri):
            failed_docs += 1
            AuditLogger.log(
                db,
                action="DOCUMENT_EXTRACTION_FAILED",
                entity_type="DOCUMENT",
                entity_id=doc.id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "request_id": req_id, "bidder_id": bidder_id, "error_code": "DOCUMENT_FILE_NOT_FOUND"},
            )
            continue

        file_bytes = None
        try:
            file_bytes = storage.read_file(doc.storage_uri)
        except Exception:
            file_bytes = None

        if not file_bytes:
            failed_docs += 1
            AuditLogger.log(
                db,
                action="DOCUMENT_EXTRACTION_FAILED",
                entity_type="DOCUMENT",
                entity_id=doc.id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "request_id": req_id, "bidder_id": bidder_id, "error_code": "STORAGE_READ_ERROR"},
            )
            continue

        computed_sha256 = hashlib.sha256(file_bytes).hexdigest()
        if computed_sha256 != doc.sha256:
            failed_docs += 1
            AuditLogger.log(
                db,
                action="DOCUMENT_EXTRACTION_FAILED",
                entity_type="DOCUMENT",
                entity_id=doc.id,
                actor_id=principal.user_id,
                actor_role=principal.role.value,
                payload={"job_id": job.id, "request_id": req_id, "bidder_id": bidder_id, "error_code": "DOCUMENT_INTEGRITY_MISMATCH"},
            )
            continue

        doc_type_val = doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type)

        ai_res = await ai_adapter.extract_document(
            document_id=doc.id,
            document_sha256=doc.sha256,
            bidder_id=bidder_id,
            file_bytes=file_bytes,
            document_uri=doc.storage_uri,
            document_type=doc_type_val,
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
        
        # Non-destructive reprocessing: preserve ALL existing facts, non-destructive deduplication for new facts
        def _norm_fact_val(v: Any) -> str:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return str(float(v))
            return str(v)

        existing_facts = db.query(ExtractedFact).filter(ExtractedFact.document_id == doc.id).all()
        existing_fact_keys = {
            (f.field, _norm_fact_val(f.value), f.source_page, f.source_text or "")
            for f in existing_facts
        }

        for item in ai_res.data:
            fact_obj = ExtractedFactCreate.model_validate(item)
            item_key = (fact_obj.field, _norm_fact_val(fact_obj.value), fact_obj.source_page, fact_obj.source_text or "")
            if item_key in existing_fact_keys:
                continue

            meta = fact_obj.metadata_json or {}

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
            existing_fact_keys.add(item_key)

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
        job.error_message = None
    elif successful_docs > 0:
        job.status = JobStatus.REVIEW_REQUIRED
        job.error_message = f"Partial extraction completion: {successful_docs}/{total_docs} documents processed successfully ({failed_docs} failed). Officer review required."
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
    else:
        job.status = JobStatus.FAILED
        job.error_message = f"Extraction failed for all {total_docs} documents."
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)

    db.commit()
    return job



