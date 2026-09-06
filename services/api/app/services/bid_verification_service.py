from datetime import datetime, timezone
import uuid
from typing import Any
from sqlalchemy.orm import Session
from app.audit.logger import AuditLogger
from app.compliance.engine import ComplianceEngine
from app.models.domain import (
    Bidder,
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
    GSTVerificationAdapter,
    MCAVerificationAdapter,
    UdyamVerificationAdapter,
)


class BidVerificationService:
    """Orchestration service coordinating verification, compliance evaluation, risk detection, audit logging, and job state."""

    def __init__(self, db: Session):
        self.db = db
        self.gst_adapter = GSTVerificationAdapter()
        self.udyam_adapter = UdyamVerificationAdapter()
        self.mca_adapter = MCAVerificationAdapter()
        self.epfo_adapter = EPFOVerificationAdapter()
        self.blacklist_adapter = BlacklistVerificationAdapter()

    async def run_verification_workflow(
        self, bidder_id: str, job_id: str | None = None
    ) -> ComplianceOverviewRead:
        # 1. Fetch bidder and associated tender
        bidder = self.db.query(Bidder).filter(Bidder.id == bidder_id).first()
        if not bidder:
            raise ValueError(f"Bidder {bidder_id} not found.")

        tender = self.db.query(Tender).filter(Tender.id == bidder.tender_id).first()
        if not tender:
            raise ValueError(f"Tender {bidder.tender_id} not found.")

        # Update processing job state if provided
        job = None
        if job_id:
            job = self.db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if job:
                job.status = JobStatus.RUNNING
                job.current_stage = JobStage.VERIFICATION
                job.progress = 20
                self.db.commit()

        AuditLogger.log(
            self.db,
            action="VERIFICATION_STARTED",
            entity_type="BIDDER",
            entity_id=bidder.id,
            payload={"gstin": bidder.gstin, "udyam": bidder.udyam_number, "job_id": job_id},
        )

        # 2. Collect bidder facts
        facts_db = (
            self.db.query(ExtractedFact)
            .filter(ExtractedFact.bidder_id == bidder_id)
            .all()
        )
        facts_schema = [FactRead.model_validate(f) for f in facts_db]

        # 3. Invoke verification adapters
        bidder_data = {
            "id": bidder.id,
            "bidder_name": bidder.bidder_name,
            "gstin": bidder.gstin,
            "udyam_number": bidder.udyam_number,
            "cin": bidder.cin,
            "pan": bidder.pan,
            "simulated_mode": bidder.metadata_json.get("simulated_mode", "success"),
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
        if bidder.pan:
            epfo_res = await self.epfo_adapter.verify(bidder_data, "general.epfo")
            verifications_schema.append(epfo_res)

        # Run Blacklist Verification
        blk_res = await self.blacklist_adapter.verify(bidder_data, "debarment.status")
        verifications_schema.append(blk_res)

        # Clear previous verification results for re-run idempotent consistency
        self.db.query(VerificationResult).filter(VerificationResult.bidder_id == bidder_id).delete()
        for v in verifications_schema:
            db_v = VerificationResult(
                id=v.id,
                bidder_id=v.bidder_id,
                field=v.field,
                claimed_value=v.claimed_value,
                verified_value=v.verified_value,
                status=v.status,
                source=v.source,
                checked_at=v.checked_at,
                verification_reference=v.verification_reference,
                error_message=v.error_message,
            )
            self.db.add(db_v)
        self.db.commit()

        if job:
            job.current_stage = JobStage.COMPLIANCE
            job.progress = 60
            self.db.commit()

        # 4. Load tender requirements and evaluate compliance
        requirements_db = (
            self.db.query(TenderRequirement)
            .filter(TenderRequirement.tender_id == tender.id)
            .all()
        )
        requirements_schema = [TenderRequirementRead.model_validate(r) for r in requirements_db]

        # Clear existing evaluations & risk signals for re-run idempotent consistency
        self.db.query(RuleEvaluation).filter(RuleEvaluation.bidder_id == bidder_id).delete()
        self.db.query(RiskSignal).filter(RiskSignal.bidder_id == bidder_id).delete()

        evaluations_schema: list[RuleEvaluationRead] = []
        risk_signals_schema: list[RiskSignalRead] = []

        for req in requirements_schema:
            eval_res = ComplianceEngine.evaluate(
                rule=req,
                facts=facts_schema,
                verification_results=verifications_schema,
                context={"bidder_id": bidder.id, "tender_id": tender.id},
            )
            evaluations_schema.append(eval_res)

            # Save RuleEvaluation to DB
            db_eval = RuleEvaluation(
                id=eval_res.id,
                bidder_id=eval_res.bidder_id,
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
                severity=r.severity,
                signal_type=r.signal_type,
                title=r.title,
                description=r.description,
                evidence_ids=r.evidence_ids,
                created_at=r.created_at,
            )
            self.db.add(db_r)

        # 5. Compute overall compliance status
        overall_status = ComplianceStatus.PASS
        statuses = [e.status for e in evaluations_schema]
        if ComplianceStatus.FAIL in statuses:
            overall_status = ComplianceStatus.FAIL
        elif ComplianceStatus.REVIEW_REQUIRED in statuses:
            overall_status = ComplianceStatus.REVIEW_REQUIRED
        elif ComplianceStatus.UNKNOWN in statuses:
            overall_status = ComplianceStatus.UNKNOWN

        # 6. Fetch latest human decision if present
        latest_decision_db = (
            self.db.query(HumanDecision)
            .filter(HumanDecision.bidder_id == bidder_id)
            .order_by(HumanDecision.decided_at.desc())
            .first()
        )
        latest_decision_schema = HumanDecisionRead.model_validate(latest_decision_db) if latest_decision_db else None

        self.db.commit()

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
            payload={"overall_status": overall_status, "evaluations_count": len(evaluations_schema)},
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
