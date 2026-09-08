from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# CANONICAL STATUS ENUMS
# ---------------------------------------------------------------------------

class ComplianceStatus(str, Enum):
    """Deterministic compliance evaluation states."""
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class HumanDecisionStatus(str, Enum):
    """Human procurement officer decision states."""
    PENDING = "PENDING"
    QUALIFIED = "QUALIFIED"
    DISQUALIFIED = "DISQUALIFIED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class UserRole(str, Enum):
    """Canonical procurement RBAC roles."""
    ADMIN = "ADMIN"
    PROCUREMENT_OFFICER = "PROCUREMENT_OFFICER"
    REVIEWER = "REVIEWER"
    AUDITOR = "AUDITOR"


class AuthenticatedPrincipal(BaseModel):
    """Lightweight authenticated principal representation derived from validated JWT claims."""
    user_id: str
    name: str | None = None
    role: UserRole
    email: str | None = None



class OperatorEnum(str, Enum):
    """Supported deterministic rule operators."""
    EQ = "EQ"
    NE = "NE"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT_EXISTS"
    COUNT_GTE = "COUNT_GTE"
    DATE_BEFORE = "DATE_BEFORE"
    DATE_AFTER = "DATE_AFTER"
    IN = "IN"
    NOT_IN = "NOT_IN"


class RequirementType(str, Enum):
    """Types of tender eligibility requirements."""
    TURNOVER = "TURNOVER"
    EXPERIENCE = "EXPERIENCE"
    BLACK_LIST = "BLACK_LIST"
    GST = "GST"
    UDYAM = "UDYAM"
    EPFO = "EPFO"
    ESIC = "ESIC"
    MCA = "MCA"
    CUSTOM = "CUSTOM"


class DocumentType(str, Enum):
    """Document categories for tenders and bidders."""
    TENDER = "TENDER"
    TURNOVER_CERT = "TURNOVER_CERT"
    GST_CERT = "GST_CERT"
    UDYAM_CERT = "UDYAM_CERT"
    EXPERIENCE_CERT = "EXPERIENCE_CERT"
    EPFO_CHALLAN = "EPFO_CHALLAN"
    ESIC_CHALLAN = "ESIC_CHALLAN"
    PAN_CERT = "PAN_CERT"
    FINANCIAL_STATEMENT = "FINANCIAL_STATEMENT"
    MCA_DOCUMENT = "MCA_DOCUMENT"
    OEM_AUTHORIZATION = "OEM_AUTHORIZATION"
    LOCAL_CONTENT_CERTIFICATE = "LOCAL_CONTENT_CERTIFICATE"
    OTHER = "OTHER"


class VerificationMode(str, Enum):
    """Operational modes for verification data sourcing."""
    LIVE = "LIVE"
    PORTAL_CACHED = "PORTAL_CACHED"
    DEMO = "DEMO"
    DOCUMENT = "DOCUMENT"


class AuthMode(str, Enum):
    """Authentication modes for HTTP live providers."""
    BEARER = "BEARER"
    X_API_KEY = "X_API_KEY"
    NONE = "NONE"


class VerificationSource(str, Enum):
    """Truthful provenance sources for registry verifications."""
    # GST Sources
    GST_AUTHORIZED_API = "GST_AUTHORIZED_API"
    GST_PORTAL_VERIFIED_CACHE = "GST_PORTAL_VERIFIED_CACHE"
    GST_DEMO_DATA = "GST_DEMO_DATA"

    # Udyam Sources
    UDYAM_AUTHORIZED_API = "UDYAM_AUTHORIZED_API"
    UDYAM_PORTAL_VERIFIED_CACHE = "UDYAM_PORTAL_VERIFIED_CACHE"
    UDYAM_DEMO_DATA = "UDYAM_DEMO_DATA"

    # MCA Sources
    MCA_AUTHORIZED_API = "MCA_AUTHORIZED_API"
    MCA_PUBLIC_MASTER_DATA_CACHE = "MCA_PUBLIC_MASTER_DATA_CACHE"
    MCA_DEMO_DATA = "MCA_DEMO_DATA"

    # EPFO Sources
    EPFO_AUTHORIZED_CHANNEL = "EPFO_AUTHORIZED_CHANNEL"
    EPFO_PORTAL_VERIFIED_CACHE = "EPFO_PORTAL_VERIFIED_CACHE"
    EPFO_DOCUMENT_VERIFICATION = "EPFO_DOCUMENT_VERIFICATION"
    EPFO_DEMO_DATA = "EPFO_DEMO_DATA"

    # ESIC Sources
    ESIC_AUTHORIZED_CHANNEL = "ESIC_AUTHORIZED_CHANNEL"
    ESIC_PORTAL_VERIFIED_CACHE = "ESIC_PORTAL_VERIFIED_CACHE"
    ESIC_DOCUMENT_VERIFICATION = "ESIC_DOCUMENT_VERIFICATION"
    ESIC_DEMO_DATA = "ESIC_DEMO_DATA"

    # Blacklist Sources
    BLACKLIST_AUTHORIZED_SOURCE = "BLACKLIST_AUTHORIZED_SOURCE"
    BLACKLIST_PORTAL_VERIFIED_CACHE = "BLACKLIST_PORTAL_VERIFIED_CACHE"
    BLACKLIST_DEMO_DATA = "BLACKLIST_DEMO_DATA"

    # System / Configuration Sources
    SYSTEM_CONFIGURATION_ERROR = "SYSTEM_CONFIGURATION_ERROR"


class VerificationStatus(str, Enum):
    """External verification outcome status."""
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    MISMATCH = "MISMATCH"
    SERVICE_ERROR = "SERVICE_ERROR"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"


class RiskSeverity(str, Enum):
    """Severity levels for risk signals."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class JobStatus(str, Enum):
    """Processing job lifecycle states."""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class JobStage(str, Enum):
    """Processing workflow stages."""
    UPLOAD = "UPLOAD"
    PARSING = "PARSING"
    OCR = "OCR"
    EXTRACTION = "EXTRACTION"
    VERIFICATION = "VERIFICATION"
    COMPLIANCE = "COMPLIANCE"
    RISK_ANALYSIS = "RISK_ANALYSIS"
    REPORTING = "REPORTING"


# ---------------------------------------------------------------------------
# TENDER SCHEMAS
# ---------------------------------------------------------------------------

class TenderRequirementCreate(BaseModel):
    clause: str = Field(..., description="Tender document clause reference, e.g. '4.2'")
    requirement_type: RequirementType
    field: str = Field(..., description="Canonical fact field key, e.g. 'financial.average_annual_turnover'")
    operator: OperatorEnum
    expected_value: Any = Field(..., description="Expected threshold or value")
    unit: str | None = None
    mandatory: bool = True
    source_page: int | None = None
    source_text: str | None = None
    confidence: float = 1.0
    requires_verification: bool = False
    is_approved: bool = False
    document_id: str | None = None
    metadata_json: dict[str, Any] | None = Field(default_factory=dict)

    @field_validator("metadata_json", mode="before")
    @classmethod
    def sanitize_metadata(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class TenderRequirementRead(TenderRequirementCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tender_id: str
    created_at: datetime


class TenderCreate(BaseModel):
    tender_number: str = Field(..., description="Unique GeM or authority tender identifier")
    title: str
    category: str | None = None
    authority: str | None = None
    budget: float | None = None
    deadline: datetime | None = None
    raw_document_uri: str | None = None
    metadata_json: dict[str, Any] | None = Field(default_factory=dict)

    @field_validator("metadata_json", mode="before")
    @classmethod
    def sanitize_metadata(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class TenderRead(TenderCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: JobStatus = JobStatus.QUEUED
    requirements: list[TenderRequirementRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# BIDDER & DOCUMENT SCHEMAS
# ---------------------------------------------------------------------------

class ExtractedFactCreate(BaseModel):
    field: str = Field(..., description="Canonical fact field key, e.g. 'gstin'")
    value: Any = Field(..., description="Extracted fact value")
    source_page: int | None = None
    source_text: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata_json: dict[str, Any] | None = Field(default_factory=dict)

    @field_validator("metadata_json", mode="before")
    @classmethod
    def sanitize_metadata(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class FactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    document_id: str
    bidder_id: str
    field: str
    value: Any
    source_page: int | None = None
    source_text: str | None = None
    confidence: float = 1.0
    metadata_json: dict[str, Any] | None = Field(default_factory=dict)
    created_at: datetime


class DocumentCreate(BaseModel):
    document_type: DocumentType
    storage_uri: str
    filename: str
    tender_id: str | None = None
    bidder_id: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    metadata_json: dict[str, Any] | None = Field(default_factory=dict)

    @field_validator("metadata_json", mode="before")
    @classmethod
    def sanitize_metadata(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class DocumentRead(DocumentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tender_id: str | None = None
    bidder_id: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    facts: list[FactRead] = Field(default_factory=list)
    created_at: datetime


class BidderCreate(BaseModel):
    bidder_name: str
    gstin: str | None = None
    udyam_number: str | None = None
    cin: str | None = None
    pan: str | None = None
    metadata_json: dict[str, Any] | None = Field(default_factory=dict)

    @field_validator("metadata_json", mode="before")
    @classmethod
    def sanitize_metadata(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class BidderRead(BidderCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tender_id: str
    status: HumanDecisionStatus = HumanDecisionStatus.PENDING
    documents: list[DocumentRead] = Field(default_factory=list)
    created_at: datetime


# ---------------------------------------------------------------------------
# VERIFICATION, COMPLIANCE & RISK SCHEMAS
# ---------------------------------------------------------------------------

class VerificationResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    bidder_id: str
    run_id: str | None = None
    field: str
    claimed_value: Any | None = None
    verified_value: Any | None = None
    status: VerificationStatus
    source: VerificationSource
    mode: VerificationMode = VerificationMode.LIVE
    checked_at: datetime
    verification_reference: str | None = None
    error_message: str | None = None
    location_metadata: dict[str, Any] | None = None


class EvidenceCreate(BaseModel):
    entity_type: str
    entity_id: str
    snippet: str
    source_uri: str | None = None
    page_number: int | None = None
    location_metadata: dict[str, Any] | None = Field(default_factory=dict)

    # Sourcing & Provenance Extensions
    bidder_id: str | None = None
    tender_id: str | None = None
    document_id: str | None = None
    extracted_fact_id: str | None = None
    verification_result_id: str | None = None
    run_id: str | None = None
    source_type: str | None = None
    source_reference: str | None = None
    sha256: str | None = None
    verification_mode: VerificationMode | None = None
    verification_status: VerificationStatus | None = None
    provider_identifier: str | None = None
    observed_at: datetime | None = None

    @field_validator("location_metadata", mode="before")
    @classmethod
    def sanitize_metadata(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class EvidenceRead(EvidenceCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime | None = None


class RuleEvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    bidder_id: str
    run_id: str | None = None
    requirement_id: str
    status: ComplianceStatus
    reason_code: str
    observed_value: Any | None = None
    expected_value: Any | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    rule_version: str = "1.0"
    evaluated_at: datetime | None = None


class ProviderConfigurationStatus(str, Enum):
    CONFIGURED = "CONFIGURED"
    UNCONFIGURED = "UNCONFIGURED"


class ProviderOperationalHealth(str, Enum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ProviderHealthRead(BaseModel):
    provider_identifier: str
    domain: str
    configured_mode: VerificationMode
    configuration_status: ProviderConfigurationStatus
    operational_health: ProviderOperationalHealth
    supported_fields: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    last_checked_at: datetime | None = None
    notes: str | None = None


class RiskInputType(str, Enum):
    EXTRACTED_FACT = "EXTRACTED_FACT"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    DOCUMENT = "DOCUMENT"
    EVIDENCE = "EVIDENCE"
    BIDDER_RECORD = "BIDDER_RECORD"


class RiskInputRef(BaseModel):
    ref_type: RiskInputType
    id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskSignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    bidder_id: str
    run_id: str | None = None
    severity: RiskSeverity
    signal_type: str
    title: str
    description: str
    reason_code: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    verification_ids: list[str] = Field(default_factory=list)
    input_refs: list[RiskInputRef] = Field(default_factory=list)
    source_mode: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("metadata_json", mode="before")
    @classmethod
    def sanitize_metadata(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}

    @model_validator(mode="before")
    @classmethod
    def populate_input_refs_from_metadata(cls, data: Any) -> Any:
        if isinstance(data, dict):
            meta = data.get("metadata_json") or {}
            if not data.get("input_refs") and isinstance(meta, dict) and "input_refs" in meta:
                data["input_refs"] = meta["input_refs"]
        elif hasattr(data, "metadata_json"):
            meta = getattr(data, "metadata_json", {}) or {}
            input_refs = getattr(data, "input_refs", None)
            if not input_refs and isinstance(meta, dict) and "input_refs" in meta:
                return {
                    "id": getattr(data, "id", None),
                    "bidder_id": getattr(data, "bidder_id", None),
                    "run_id": getattr(data, "run_id", None),
                    "severity": getattr(data, "severity", None),
                    "signal_type": getattr(data, "signal_type", None),
                    "title": getattr(data, "title", None),
                    "description": getattr(data, "description", None),
                    "reason_code": getattr(data, "reason_code", None),
                    "evidence_ids": getattr(data, "evidence_ids", []) or [],
                    "verification_ids": getattr(data, "verification_ids", []) or [],
                    "input_refs": meta.get("input_refs", []),
                    "source_mode": getattr(data, "source_mode", None),
                    "metadata_json": meta,
                    "created_at": getattr(data, "created_at", None),
                }
        return data


class RiskSummaryRead(BaseModel):
    signal_count: int = 0
    counts_by_severity: dict[str, int] = Field(default_factory=dict)
    counts_by_type: dict[str, int] = Field(default_factory=dict)
    unresolved_count: int = 0
    source_mode_breakdown: dict[str, int] = Field(default_factory=dict)
    risk_engine_version: str = "1.0"
    risk_policy_version: str = "1.0"


class AdvisoryMLRiskSignalRead(BaseModel):
    contract_version: str = "1.0"
    request_id: str
    bidder_id: str
    run_id: str | None = None
    model_identifier: str
    model_version: str
    advisory_signal_type: str
    explanation: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    confidence_score: float | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class HumanDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: HumanDecisionStatus
    reason_code: str
    remarks: str | None = None


class HumanDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    bidder_id: str
    status: HumanDecisionStatus
    reason_code: str
    remarks: str | None = None
    officer_id: str
    officer_name: str
    decided_at: datetime


class ComplianceRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    bidder_id: str
    tender_id: str
    job_id: str | None = None
    execution_status: JobStatus
    overall_status: ComplianceStatus | None = None
    triggered_by: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime
    rule_version: str | None = "1.0"
    summary_json: dict[str, Any] = Field(default_factory=dict)
    input_snapshot_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("summary_json", "input_snapshot_json", mode="before")
    @classmethod
    def sanitize_summary(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class ComplianceRunSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    bidder_id: str
    tender_id: str
    job_id: str | None = None
    execution_status: JobStatus
    overall_status: ComplianceStatus | None = None
    started_at: datetime
    completed_at: datetime | None = None
    evaluation_count: int = 0
    risk_count: int = 0


class ComplianceRunDetailRead(BaseModel):
    run: ComplianceRunRead
    verification_results: list[VerificationResultRead] = Field(default_factory=list)
    rule_evaluations: list[RuleEvaluationRead] = Field(default_factory=list)
    risk_signals: list[RiskSignalRead] = Field(default_factory=list)
    evidence: list[EvidenceRead] = Field(default_factory=list)


class ComplianceMatrixRow(BaseModel):
    requirement_id: str
    clause: str | None = None
    requirement_type: RequirementType | None = None
    field: str | None = None
    operator: OperatorEnum | None = None
    expected_value: Any | None = None
    unit: str | None = None
    mandatory: bool | None = None
    status: ComplianceStatus
    reason_code: str
    observed_value: Any | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    verification_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    review_required: bool = False


class ComplianceMatrixRead(BaseModel):
    tender_id: str
    bidder_id: str
    run_id: str | None = None
    overall_status: ComplianceStatus
    rows: list[ComplianceMatrixRow] = Field(default_factory=list)
    historical_limitations_notice: str | None = None


class ComplianceOverviewRead(BaseModel):
    bidder_id: str
    tender_id: str
    overall_status: ComplianceStatus
    human_decision_status: HumanDecisionStatus
    rule_evaluations: list[RuleEvaluationRead] = Field(default_factory=list)
    risk_signals: list[RiskSignalRead] = Field(default_factory=list)
    risk_summary: RiskSummaryRead | None = None
    latest_decision: HumanDecisionRead | None = None


class ReportRead(BaseModel):
    generated_at: datetime
    tender: TenderRead
    bidder: BidderRead
    compliance_overview: ComplianceOverviewRead
    compliance_matrix: ComplianceMatrixRead | None = None
    verification_results: list[VerificationResultRead] = Field(default_factory=list)
    evidence: list[EvidenceRead] = Field(default_factory=list)
    risk_summary: RiskSummaryRead | None = None
    human_decision: HumanDecisionRead | None = None
    historical_limitations_notice: str | None = None
    audit_trail_count: int = 0


# ---------------------------------------------------------------------------
# PROCESSING JOB & SSE SCHEMAS
# ---------------------------------------------------------------------------

class JobEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    job_id: str
    stage: JobStage
    status: JobStatus
    progress: int = Field(..., ge=0, le=100)
    message: str
    payload: dict[str, Any] | None = Field(default_factory=dict)
    timestamp: datetime

    @field_validator("payload", mode="before")
    @classmethod
    def sanitize_metadata(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    target_type: str
    target_id: str
    job_type: str
    status: JobStatus
    current_stage: JobStage
    progress: int = Field(0, ge=0, le=100)
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    events: list[JobEventRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# INTEGRATION STATUS & AI / RAG SCHEMAS
# ---------------------------------------------------------------------------

class IntegrationServiceStatus(BaseModel):
    mode: VerificationMode
    configured: bool
    details: str | None = None


class IntegrationsHealthResponse(BaseModel):
    gst: IntegrationServiceStatus
    udyam: IntegrationServiceStatus
    mca: IntegrationServiceStatus
    epfo: IntegrationServiceStatus
    esic: IntegrationServiceStatus
    blacklist: IntegrationServiceStatus
    intelligence: IntegrationServiceStatus


class RAGQueryRequest(BaseModel):
    query: str
    tender_id: str | None = None
    filters: dict[str, Any] | None = Field(default_factory=dict)
    top_k: int = 5

    @field_validator("filters", mode="before")
    @classmethod
    def sanitize_metadata(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class RAGQueryResponse(BaseModel):
    query: str
    results: list[EvidenceRead] = Field(default_factory=list)
    retrieved_at: datetime
    error_code: str | None = None
    error_message: str | None = None


class AIServiceResult(BaseModel):
    success: bool
    data: Any | None = None
    error_code: str | None = None
    retryable: bool = False
    message: str | None = None


class AIResponseEnvelope(BaseModel):
    """Strict schema envelope for intelligence service HTTP responses."""
    model_config = ConfigDict(extra="ignore")

    contract_version: str
    request_id: str
    document_id: str
    document_sha256: str
    status: str
    bidder_id: str | None = None
    requirements: list[dict[str, Any]] | None = None
    facts: list[dict[str, Any]] | None = None
    provider_model: str | None = None
    error: str | None = None
    message: str | None = None



# ---------------------------------------------------------------------------
# STANDARDIZED API ERROR SCHEMAS
# ---------------------------------------------------------------------------

class ErrorDetails(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class APIErrorResponse(BaseModel):
    error: ErrorDetails
