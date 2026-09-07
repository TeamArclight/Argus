import uuid
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base
from app.schemas.canonical import (
    ComplianceStatus,
    DocumentType,
    HumanDecisionStatus,
    JobStage,
    JobStatus,
    OperatorEnum,
    RequirementType,
    RiskSeverity,
    VerificationMode,
    VerificationSource,
    VerificationStatus,
)


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Tender(Base):
    __tablename__ = "tenders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    tender_number: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    authority: Mapped[str | None] = mapped_column(String, nullable=True)
    budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_document_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[JobStatus] = mapped_column(String, default=JobStatus.QUEUED, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    requirements: Mapped[list["TenderRequirement"]] = relationship("TenderRequirement", back_populates="tender", cascade="all, delete-orphan")
    bidders: Mapped[list["Bidder"]] = relationship("Bidder", back_populates="tender", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship("Document", back_populates="tender", cascade="all, delete-orphan")


class TenderRequirement(Base):
    __tablename__ = "tender_requirements"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    tender_id: Mapped[str] = mapped_column(String, ForeignKey("tenders.id"), nullable=False, index=True)
    clause: Mapped[str] = mapped_column(String, nullable=False)
    requirement_type: Mapped[RequirementType] = mapped_column(String, nullable=False)
    field: Mapped[str] = mapped_column(String, nullable=False, index=True)
    operator: Mapped[OperatorEnum] = mapped_column(String, nullable=False)
    expected_value: Mapped[Any] = mapped_column(JSON, nullable=False)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    requires_verification: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    document_id: Mapped[str | None] = mapped_column(String, ForeignKey("documents.id"), nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    tender: Mapped["Tender"] = relationship("Tender", back_populates="requirements")
    document: Mapped["Document | None"] = relationship("Document")
    rule_evaluations: Mapped[list["RuleEvaluation"]] = relationship("RuleEvaluation", back_populates="requirement", cascade="all, delete-orphan")


class Bidder(Base):
    __tablename__ = "bidders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    tender_id: Mapped[str] = mapped_column(String, ForeignKey("tenders.id"), nullable=False, index=True)
    bidder_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    gstin: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    udyam_number: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    cin: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    pan: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[HumanDecisionStatus] = mapped_column(String, default=HumanDecisionStatus.PENDING, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    tender: Mapped["Tender"] = relationship("Tender", back_populates="bidders")
    documents: Mapped[list["Document"]] = relationship("Document", back_populates="bidder", cascade="all, delete-orphan")
    compliance_runs: Mapped[list["ComplianceRun"]] = relationship("ComplianceRun", back_populates="bidder", cascade="all, delete-orphan")
    verification_results: Mapped[list["VerificationResult"]] = relationship("VerificationResult", back_populates="bidder", cascade="all, delete-orphan")
    rule_evaluations: Mapped[list["RuleEvaluation"]] = relationship("RuleEvaluation", back_populates="bidder", cascade="all, delete-orphan")
    risk_signals: Mapped[list["RiskSignal"]] = relationship("RiskSignal", back_populates="bidder", cascade="all, delete-orphan")
    human_decisions: Mapped[list["HumanDecision"]] = relationship("HumanDecision", back_populates="bidder", cascade="all, delete-orphan")


class ComplianceRun(Base):
    __tablename__ = "compliance_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    bidder_id: Mapped[str] = mapped_column(String, ForeignKey("bidders.id"), nullable=False, index=True)
    tender_id: Mapped[str] = mapped_column(String, ForeignKey("tenders.id"), nullable=False, index=True)
    job_id: Mapped[str | None] = mapped_column(String, ForeignKey("processing_jobs.id"), nullable=True, index=True)
    execution_status: Mapped[JobStatus] = mapped_column(String, default=JobStatus.RUNNING, nullable=False)
    overall_status: Mapped[ComplianceStatus | None] = mapped_column(String, nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    rule_version: Mapped[str | None] = mapped_column(String, default="1.0", nullable=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    bidder: Mapped["Bidder"] = relationship("Bidder", back_populates="compliance_runs")
    tender: Mapped["Tender"] = relationship("Tender")
    job: Mapped["ProcessingJob | None"] = relationship("ProcessingJob")
    verification_results: Mapped[list["VerificationResult"]] = relationship("VerificationResult", back_populates="compliance_run", cascade="all, delete-orphan")
    rule_evaluations: Mapped[list["RuleEvaluation"]] = relationship("RuleEvaluation", back_populates="compliance_run", cascade="all, delete-orphan")
    risk_signals: Mapped[list["RiskSignal"]] = relationship("RiskSignal", back_populates="compliance_run", cascade="all, delete-orphan")
    evidence: Mapped[list["Evidence"]] = relationship("Evidence", back_populates="compliance_run", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "(tender_id IS NOT NULL AND bidder_id IS NULL) OR (tender_id IS NULL AND bidder_id IS NOT NULL)",
            name="ck_documents_single_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    tender_id: Mapped[str | None] = mapped_column(String, ForeignKey("tenders.id"), nullable=True, index=True)
    bidder_id: Mapped[str | None] = mapped_column(String, ForeignKey("bidders.id"), nullable=True, index=True)
    document_type: Mapped[DocumentType] = mapped_column(String, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    tender: Mapped["Tender | None"] = relationship("Tender", back_populates="documents")
    bidder: Mapped["Bidder | None"] = relationship("Bidder", back_populates="documents")
    facts: Mapped[list["ExtractedFact"]] = relationship("ExtractedFact", back_populates="document", cascade="all, delete-orphan")


class ExtractedFact(Base):
    __tablename__ = "extracted_facts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.id"), nullable=False, index=True)
    bidder_id: Mapped[str] = mapped_column(String, ForeignKey("bidders.id"), nullable=False, index=True)
    field: Mapped[str] = mapped_column(String, nullable=False, index=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    document: Mapped["Document"] = relationship("Document", back_populates="facts")


class VerificationResult(Base):
    __tablename__ = "verification_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    bidder_id: Mapped[str] = mapped_column(String, ForeignKey("bidders.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("compliance_runs.id"), nullable=True, index=True)
    field: Mapped[str] = mapped_column(String, nullable=False, index=True)
    claimed_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    verified_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    status: Mapped[VerificationStatus] = mapped_column(String, nullable=False)
    source: Mapped[VerificationSource] = mapped_column(String, nullable=False)
    mode: Mapped[VerificationMode] = mapped_column(String, default=VerificationMode.LIVE, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    verification_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    bidder: Mapped["Bidder"] = relationship("Bidder", back_populates="verification_results")
    compliance_run: Mapped["ComplianceRun | None"] = relationship("ComplianceRun", back_populates="verification_results")


class RuleEvaluation(Base):
    __tablename__ = "rule_evaluations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    bidder_id: Mapped[str] = mapped_column(String, ForeignKey("bidders.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("compliance_runs.id"), nullable=True, index=True)
    requirement_id: Mapped[str] = mapped_column(String, ForeignKey("tender_requirements.id"), nullable=False, index=True)
    status: Mapped[ComplianceStatus] = mapped_column(String, nullable=False)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    observed_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    expected_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    rule_version: Mapped[str] = mapped_column(String, default="1.0", nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    bidder: Mapped["Bidder"] = relationship("Bidder", back_populates="rule_evaluations")
    compliance_run: Mapped["ComplianceRun | None"] = relationship("ComplianceRun", back_populates="rule_evaluations")
    requirement: Mapped["TenderRequirement"] = relationship("TenderRequirement", back_populates="rule_evaluations")


class RiskSignal(Base):
    __tablename__ = "risk_signals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    bidder_id: Mapped[str] = mapped_column(String, ForeignKey("bidders.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("compliance_runs.id"), nullable=True, index=True)
    severity: Mapped[RiskSeverity] = mapped_column(String, nullable=False)
    signal_type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    bidder: Mapped["Bidder"] = relationship("Bidder", back_populates="risk_signals")
    compliance_run: Mapped["ComplianceRun | None"] = relationship("ComplianceRun", back_populates="risk_signals")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    entity_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    # Phase 9 Sourcing & Provenance Extensions
    bidder_id: Mapped[str | None] = mapped_column(String, ForeignKey("bidders.id"), nullable=True, index=True)
    tender_id: Mapped[str | None] = mapped_column(String, ForeignKey("tenders.id"), nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(String, ForeignKey("documents.id"), nullable=True, index=True)
    extracted_fact_id: Mapped[str | None] = mapped_column(String, ForeignKey("extracted_facts.id"), nullable=True, index=True)
    verification_result_id: Mapped[str | None] = mapped_column(String, ForeignKey("verification_results.id"), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("compliance_runs.id"), nullable=True, index=True)
    source_type: Mapped[str | None] = mapped_column(String, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    verification_mode: Mapped[VerificationMode | None] = mapped_column(String, nullable=True)
    verification_status: Mapped[VerificationStatus | None] = mapped_column(String, nullable=True)
    provider_identifier: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    compliance_run: Mapped["ComplianceRun | None"] = relationship("ComplianceRun", back_populates="evidence")
    bidder: Mapped["Bidder | None"] = relationship("Bidder")
    tender: Mapped["Tender | None"] = relationship("Tender")
    document: Mapped["Document | None"] = relationship("Document")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    target_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[JobStatus] = mapped_column(String, default=JobStatus.QUEUED, nullable=False)
    current_stage: Mapped[JobStage] = mapped_column(String, default=JobStage.UPLOAD, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    events: Mapped[list["JobEvent"]] = relationship("JobEvent", back_populates="job", cascade="all, delete-orphan")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("processing_jobs.id"), nullable=False, index=True)
    stage: Mapped[JobStage] = mapped_column(String, nullable=False)
    status: Mapped[JobStatus] = mapped_column(String, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    job: Mapped["ProcessingJob"] = relationship("ProcessingJob", back_populates="events")


class HumanDecision(Base):
    __tablename__ = "human_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    bidder_id: Mapped[str] = mapped_column(String, ForeignKey("bidders.id"), nullable=False, index=True)
    status: Mapped[HumanDecisionStatus] = mapped_column(String, nullable=False)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    officer_id: Mapped[str] = mapped_column(String, nullable=False)
    officer_name: Mapped[str] = mapped_column(String, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    bidder: Mapped["Bidder"] = relationship("Bidder", back_populates="human_decisions")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    entity_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_role: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
