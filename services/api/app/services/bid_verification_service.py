from datetime import datetime, timezone
import uuid
from typing import Any
from sqlalchemy.orm import Session
from app.audit.logger import AuditLogger
from app.compliance.engine import ComplianceEngine
from app.models.domain import (
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
    BidderRead,
    ComplianceOverviewRead,
    ComplianceStatus,
    DocumentRead,
    FactRead,
    HumanDecisionRead,
    HumanDecisionStatus,
    JobStage,
    JobStatus,
    RiskSeverity,
    RiskSignalRead,
    RuleEvaluationRead,
    TenderRequirementRead,
    VerificationResultRead,
)
from app.verification.adapters import (
    BlacklistVerificationAdapter,
    EPFOVerificationAdapter,
    ESICVerificationAdapter,
    GSTVerificationAdapter,
    MCAVerificationAdapter,
    UdyamVerificationAdapter,
)


from app.services.evidence_service import EvidenceNormalizationService


class BidVerificationService:
    """Orchestration service coordinating verification, compliance evaluation, risk detection, audit logging, and job state."""

    @staticmethod
    def compute_overall_status(evaluations: list[Any]) -> ComplianceStatus:
        """Computes overall compliance rollup status following deterministic precedence hierarchy."""
        if not evaluations:
            return ComplianceStatus.UNKNOWN
        statuses = [e.status for e in evaluations]
        if ComplianceStatus.FAIL in statuses:
            return ComplianceStatus.FAIL
        elif ComplianceStatus.REVIEW_REQUIRED in statuses:
            return ComplianceStatus.REVIEW_REQUIRED
        elif ComplianceStatus.UNKNOWN in statuses:
            return ComplianceStatus.UNKNOWN
        return ComplianceStatus.PASS

    @staticmethod
    def close_orphaned_run(db: Session, run: ComplianceRun, bidder_id: str) -> None:
        """Marks a stale RUNNING ComplianceRun as FAILED and emits a safe audit event."""
        run.execution_status = JobStatus.FAILED
        run.completed_at = datetime.now(timezone.utc)
        run.summary_json = {"error_code": "ORPHANED_ACTIVE_RUN"}
        db.commit()

        AuditLogger.log(
            db,
            action="VERIFICATION_RUN_ORPHANED",
            entity_type="BIDDER",
            entity_id=bidder_id,
            payload={
                "run_id": run.id,
                "error_code": "ORPHANED_ACTIVE_RUN",
            },
        )

    def __init__(self, db: Session):
        self.db = db
        self.gst_adapter = GSTVerificationAdapter()
        self.udyam_adapter = UdyamVerificationAdapter()
        self.mca_adapter = MCAVerificationAdapter()
        self.epfo_adapter = EPFOVerificationAdapter()
        self.esic_adapter = ESICVerificationAdapter()
        self.blacklist_adapter = BlacklistVerificationAdapter()

    async def run_verification_workflow(
        self,
        bidder_id: str,
        job_id: str | None = None,
        triggered_by: str | None = None,
        actor_id: str = "SYSTEM",
        actor_role: str = "SYSTEM",
    ) -> ComplianceOverviewRead:
        # 1. Fetch bidder and associated tender
        bidder = self.db.query(Bidder).filter(Bidder.id == bidder_id).first()
        if not bidder:
            raise ValueError(f"Bidder {bidder_id} not found.")

        tender = self.db.query(Tender).filter(Tender.id == bidder.tender_id).first()
        if not tender:
            raise ValueError(f"Tender {bidder.tender_id} not found.")

        # Service-level duplicate run protection: check for existing active RUNNING ComplianceRun
        existing_run = (
            self.db.query(ComplianceRun)
            .filter(
                ComplianceRun.bidder_id == bidder_id,
                ComplianceRun.execution_status == JobStatus.RUNNING,
            )
            .first()
        )
        if existing_run:
            active_job = None
            if existing_run.job_id:
                active_job = (
                    self.db.query(ProcessingJob)
                    .filter(
                        ProcessingJob.id == existing_run.job_id,
                        ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                    )
                    .first()
                )

            same_job = (
                existing_run.job_id is not None
                and job_id is not None
                and existing_run.job_id == job_id
            )

            if active_job or same_job:
                evals_db = (
                    self.db.query(RuleEvaluation)
                    .filter(RuleEvaluation.run_id == existing_run.id)
                    .all()
                )
                evals_schema = [RuleEvaluationRead.model_validate(e) for e in evals_db]
                risks_db = (
                    self.db.query(RiskSignal)
                    .filter(RiskSignal.run_id == existing_run.id)
                    .all()
                )
                risks_schema = [RiskSignalRead.model_validate(r) for r in risks_db]
                ov_status = existing_run.overall_status or self.compute_overall_status(evals_schema)
                latest_decision_db = (
                    self.db.query(HumanDecision)
                    .filter(HumanDecision.bidder_id == bidder_id)
                    .order_by(HumanDecision.decided_at.desc())
                    .first()
                )
                return ComplianceOverviewRead(
                    bidder_id=bidder.id,
                    tender_id=tender.id,
                    overall_status=ov_status,
                    human_decision_status=latest_decision_db.status if latest_decision_db else HumanDecisionStatus.PENDING,
                    rule_evaluations=evals_schema,
                    risk_signals=risks_schema,
                    latest_decision=HumanDecisionRead.model_validate(latest_decision_db) if latest_decision_db else None,
                )
            else:
                # Stale / orphaned active run without an active job -> mark FAILED and log audit event
                self.close_orphaned_run(self.db, existing_run, bidder_id)

        # Create and commit ComplianceRun immediately before verification begins
        run = ComplianceRun(
            id=str(uuid.uuid4()),
            bidder_id=bidder.id,
            tender_id=tender.id,
            job_id=job_id,
            execution_status=JobStatus.RUNNING,
            overall_status=None,
            triggered_by=triggered_by,
            started_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            rule_version="1.0",
            summary_json={},
            input_snapshot_json={},
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        # Update processing job state if provided
        job = None
        if job_id:
            job = self.db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if job:
                job.status = JobStatus.RUNNING
                job.current_stage = JobStage.VERIFICATION
                job.progress = 20
                self.db.commit()

        bidder_meta = bidder.metadata_json or {}

        AuditLogger.log(
            self.db,
            action="VERIFICATION_STARTED",
            entity_type="BIDDER",
            entity_id=bidder.id,
            actor_id=actor_id,
            actor_role=actor_role,
            payload={"gstin": bidder.gstin, "udyam": bidder.udyam_number, "job_id": job_id, "run_id": run.id},
        )

        try:
            # 2. Collect bidder facts and normalize fact evidence
            facts_db = (
                self.db.query(ExtractedFact)
                .filter(ExtractedFact.bidder_id == bidder_id)
                .all()
            )
            facts_schema = [FactRead.model_validate(f) for f in facts_db]

            fact_evidence_map: dict[str, Evidence] = {}
            for f in facts_db:
                doc = self.db.query(Document).filter(Document.id == f.document_id).first()
                ev = EvidenceNormalizationService.normalize_fact_evidence(
                    self.db, bidder.id, tender.id, f, document=doc, run_id=run.id
                )
                fact_evidence_map[f.id] = ev

            # 3. Invoke verification adapters and normalize verification evidence
            bidder_data = {
                "id": bidder.id,
                "bidder_name": bidder.bidder_name,
                "gstin": bidder.gstin,
                "udyam_number": bidder.udyam_number,
                "cin": bidder.cin,
                "pan": bidder.pan,
                "verification_mode": bidder_meta.get("verification_mode"),
            }

            verifications_schema: list[VerificationResultRead] = []

            # Run GST Verification
            if bidder.gstin:
                gst_res = await self.gst_adapter.verify(bidder_data, "general.gstin")
                verifications_schema.append(gst_res)

            # Run Udyam Verification
            if bidder.udyam_number:
                udyam_res = await self.udyam_adapter.verify(bidder_data, "general.udyam")
                verifications_schema.append(udyam_res)

            # Run MCA Verification
            if bidder.cin:
                mca_res = await self.mca_adapter.verify(bidder_data, "general.cin")
                verifications_schema.append(mca_res)

            # Run EPFO Verification
            if bidder.pan or bidder.bidder_name:
                epfo_res = await self.epfo_adapter.verify(bidder_data, "general.epfo")
                verifications_schema.append(epfo_res)

            # Run ESIC Verification
            if bidder.pan or bidder.bidder_name:
                esic_res = await self.esic_adapter.verify(bidder_data, "general.esic")
                verifications_schema.append(esic_res)

            # Run Blacklist Verification
            blk_res = await self.blacklist_adapter.verify(bidder_data, "debarment.status")
            verifications_schema.append(blk_res)

            # Save VerificationResult rows append-only, assigned to run.id
            ver_evidence_map: dict[str, Evidence] = {}
            for v in verifications_schema:
                v.run_id = run.id
                db_v = VerificationResult(
                    id=v.id,
                    bidder_id=v.bidder_id,
                    run_id=run.id,
                    field=v.field,
                    claimed_value=v.claimed_value,
                    verified_value=v.verified_value,
                    status=v.status,
                    source=v.source,
                    mode=v.mode,
                    checked_at=v.checked_at,
                    verification_reference=v.verification_reference,
                    error_message=v.error_message,
                )
                self.db.add(db_v)

                ev_v = EvidenceNormalizationService.normalize_verification_evidence(
                    self.db, bidder.id, tender.id, db_v, run_id=run.id
                )
                ver_evidence_map[db_v.id] = ev_v

            if job:
                job.current_stage = JobStage.COMPLIANCE
                job.progress = 60

            # 4. Load ONLY APPROVED tender requirements and evaluate compliance
            requirements_db = (
                self.db.query(TenderRequirement)
                .filter(
                    TenderRequirement.tender_id == tender.id,
                    TenderRequirement.is_approved == True,
                )
                .all()
            )
            requirements_schema = [TenderRequirementRead.model_validate(r) for r in requirements_db]

            evaluations_schema: list[RuleEvaluationRead] = []
            risk_signals_schema: list[RiskSignalRead] = []

            for req in requirements_schema:
                eval_res = ComplianceEngine.evaluate(
                    rule=req,
                    facts=facts_schema,
                    verification_results=verifications_schema,
                    context={"bidder_id": bidder.id, "tender_id": tender.id, "run_id": run.id},
                )
                eval_res.run_id = run.id

                # Map exact contributing input IDs from ComplianceEngine to staged Evidence IDs
                mapped_evidence_ids = []
                for input_id in eval_res.evidence_ids:
                    if input_id in fact_evidence_map:
                        mapped_evidence_ids.append(fact_evidence_map[input_id].id)
                    elif input_id in ver_evidence_map:
                        mapped_evidence_ids.append(ver_evidence_map[input_id].id)
                    else:
                        mapped_evidence_ids.append(input_id)
                eval_res.evidence_ids = mapped_evidence_ids
                evaluations_schema.append(eval_res)

                # Save RuleEvaluation to DB assigned to run.id
                db_eval = RuleEvaluation(
                    id=eval_res.id,
                    bidder_id=eval_res.bidder_id,
                    run_id=run.id,
                    requirement_id=eval_res.requirement_id,
                    status=eval_res.status,
                    reason_code=eval_res.reason_code,
                    observed_value=eval_res.observed_value,
                    expected_value=eval_res.expected_value,
                    evidence_ids=eval_res.evidence_ids,
                    rule_version=eval_res.rule_version,
                    evaluated_at=eval_res.evaluated_at,
                )
                self.db.add(db_eval)

                # Generate Risk Signals for failures / mismatches
                if eval_res.status == ComplianceStatus.FAIL and req.mandatory:
                    risk_id = str(uuid.uuid4())
                    risk = RiskSignalRead(
                        id=risk_id,
                        bidder_id=bidder.id,
                        run_id=run.id,
                        severity=RiskSeverity.HIGH,
                        signal_type="MANDATORY_REQUIREMENT_FAILED",
                        title=f"Mandatory Requirement Failed: {req.clause}",
                        description=f"Field '{req.field}' observed value {eval_res.observed_value} does not meet expected threshold {eval_res.expected_value}.",
                        evidence_ids=eval_res.evidence_ids,
                        created_at=datetime.now(timezone.utc),
                    )
                    risk_signals_schema.append(risk)

                elif eval_res.status == ComplianceStatus.REVIEW_REQUIRED:
                    risk_id = str(uuid.uuid4())
                    risk = RiskSignalRead(
                        id=risk_id,
                        bidder_id=bidder.id,
                        run_id=run.id,
                        severity=RiskSeverity.MEDIUM,
                        signal_type="DISCREPANCY_REVIEW_REQUIRED",
                        title=f"Discrepancy Requiring Officer Review: {req.clause}",
                        description=f"Reason: {eval_res.reason_code}. Observed: {eval_res.observed_value}.",
                        evidence_ids=eval_res.evidence_ids,
                        created_at=datetime.now(timezone.utc),
                    )
                    risk_signals_schema.append(risk)

            for r in risk_signals_schema:
                db_r = RiskSignal(
                    id=r.id,
                    bidder_id=r.bidder_id,
                    run_id=run.id,
                    severity=r.severity,
                    signal_type=r.signal_type,
                    title=r.title,
                    description=r.description,
                    evidence_ids=r.evidence_ids,
                    created_at=r.created_at,
                )
                self.db.add(db_r)

            # 5. Build explicit run input snapshot
            staged_evidence_list = list(fact_evidence_map.values()) + list(ver_evidence_map.values())
            bidder_docs = self.db.query(Document).filter(Document.bidder_id == bidder.id).all()

            input_snapshot = {
                "snapshot_version": "1.0",
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "approved_requirements": [r.model_dump(mode="json") for r in requirements_schema],
                "facts": [f.model_dump(mode="json") for f in facts_schema],
                "verifications": [v.model_dump(mode="json") for v in verifications_schema],
                "evidence": [
                    {
                        "id": e.id,
                        "entity_type": e.entity_type,
                        "entity_id": e.entity_id,
                        "snippet": e.snippet,
                        "page_number": e.page_number,
                        "document_id": e.document_id,
                        "extracted_fact_id": e.extracted_fact_id,
                        "verification_result_id": e.verification_result_id,
                        "source_type": e.source_type,
                        "source_reference": e.source_reference,
                        "sha256": e.sha256,
                        "verification_mode": e.verification_mode.value if hasattr(e.verification_mode, "value") else str(e.verification_mode) if e.verification_mode else None,
                        "verification_status": e.verification_status.value if hasattr(e.verification_status, "value") else str(e.verification_status) if e.verification_status else None,
                        "provider_identifier": e.provider_identifier,
                        "observed_at": e.observed_at.isoformat() if hasattr(e.observed_at, "isoformat") and e.observed_at else str(e.observed_at) if e.observed_at else None,
                        "location_metadata": e.location_metadata,
                    }
                    for e in staged_evidence_list
                ],
                "exact_evaluation_linkage": [e.model_dump(mode="json") for e in evaluations_schema],
                "documents": [
                    {
                        "id": d.id,
                        "filename": d.filename,
                        "document_type": d.document_type.value if hasattr(d.document_type, "value") else str(d.document_type),
                        "sha256": d.sha256,
                    }
                    for d in bidder_docs
                ],
            }

            # Compute overall compliance status
            overall_status = self.compute_overall_status(evaluations_schema)
            reason_code = "NO_APPROVED_REQUIREMENTS" if not evaluations_schema else None

            # Mark ComplianceRun execution_status = COMPLETED, set overall_status & input_snapshot_json
            run.execution_status = JobStatus.COMPLETED
            run.overall_status = overall_status
            run.completed_at = datetime.now(timezone.utc)
            summary_dict = {
                "overall_status": overall_status.value if isinstance(overall_status, ComplianceStatus) else str(overall_status),
                "evaluation_count": len(evaluations_schema),
                "risk_count": len(risk_signals_schema),
            }
            if reason_code:
                summary_dict["reason_code"] = reason_code
            run.summary_json = summary_dict
            run.input_snapshot_json = input_snapshot
            self.db.commit()

            # 6. Fetch latest human decision if present
            latest_decision_db = (
                self.db.query(HumanDecision)
                .filter(HumanDecision.bidder_id == bidder_id)
                .order_by(HumanDecision.decided_at.desc())
                .first()
            )
            latest_decision_schema = HumanDecisionRead.model_validate(latest_decision_db) if latest_decision_db else None

            # Update job completion status
            if job:
                job.status = JobStatus.COMPLETED if overall_status in (ComplianceStatus.PASS, ComplianceStatus.FAIL) else JobStatus.REVIEW_REQUIRED
                job.current_stage = JobStage.REPORTING
                job.progress = 100
                job.completed_at = datetime.now(timezone.utc)
                self.db.commit()

            AuditLogger.log(
                self.db,
                action="COMPLIANCE_EVALUATION_COMPLETED",
                entity_type="BIDDER",
                entity_id=bidder.id,
                payload={"overall_status": overall_status, "evaluations_count": len(evaluations_schema), "run_id": run.id},
            )

            return ComplianceOverviewRead(
                bidder_id=bidder.id,
                tender_id=tender.id,
                overall_status=overall_status,
                human_decision_status=latest_decision_db.status if latest_decision_db else HumanDecisionStatus.PENDING,
                rule_evaluations=evaluations_schema,
                risk_signals=risk_signals_schema,
                latest_decision=latest_decision_schema,
            )

        except Exception as e:
            self.db.rollback()
            run_db = self.db.query(ComplianceRun).filter(ComplianceRun.id == run.id).first()
            if run_db:
                run_db.execution_status = JobStatus.FAILED
                run_db.completed_at = datetime.now(timezone.utc)
                run_db.summary_json = {"error_code": "VERIFICATION_WORKFLOW_FAILED"}
                self.db.commit()

            if job_id:
                job_db = self.db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
                if job_db:
                    job_db.status = JobStatus.FAILED
                    job_db.error_message = "Verification workflow failed."
                    job_db.completed_at = datetime.now(timezone.utc)
                    self.db.commit()

            AuditLogger.log(
                self.db,
                action="VERIFICATION_FAILED",
                entity_type="BIDDER",
                entity_id=bidder.id,
                payload={
                    "run_id": run.id,
                    "error_code": "VERIFICATION_WORKFLOW_FAILED",
                    "error_type": type(e).__name__,
                },
            )
            raise
